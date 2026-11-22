import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.core.config import Settings
from app.services.document_storage import DocumentStorage
from app.services.processing_handler import ProcessingContext, ProcessingFailure
from app.services.processing_handler import safe_failure_details
from app.services import processing_worker as worker_service
from app.services.processing_worker import (
    ClaimedProcessingItem,
    ProcessingWorker,
    WorkerCapacityError,
    WorkerConfigurationError,
    acquire_worker_slot,
    claim_next_item,
    retry_delay_seconds,
    run_worker_process,
)


def worker_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_queue_refuses_sqlite_instead_of_treating_it_as_skip_locked(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with pytest.raises(WorkerConfigurationError, match="requires PostgreSQL"):
            claim_next_item(
                lambda: Session(engine),
                "worker-test",
                worker_settings(),
                DocumentStorage(tmp_path / "storage"),
            )
    finally:
        engine.dispose()


def test_worker_process_refuses_to_run_without_real_handler() -> None:
    with pytest.raises(WorkerConfigurationError, match="No production processing handler"):
        run_worker_process(worker_settings(), None)


def test_manual_worker_entrypoint_registers_default_dispatcher(monkeypatch) -> None:
    from app import worker as worker_entrypoint
    from app.services.processing_handlers import FormatAwareProcessingHandler

    monkeypatch.setattr(worker_entrypoint, "get_production_handler", lambda: None)
    registered = []
    started = []
    monkeypatch.setattr(worker_entrypoint, "register_production_handler", registered.append)
    monkeypatch.setattr(
        worker_entrypoint,
        "run_worker_process",
        lambda settings, handler, shutdown: started.append((settings, handler, shutdown)),
    )
    assert worker_entrypoint.main() == 0
    assert len(registered) == 1 and isinstance(registered[0], FormatAwareProcessingHandler)
    assert started[0][1] is registered[0]


def test_worker_process_limit_is_hard_capped_at_two() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with pytest.raises(WorkerConfigurationError, match="limit is invalid"):
            acquire_worker_slot(engine, 3)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "settings",
    [
        {"tenderwise_worker_max_processes": 3},
        {
            "tenderwise_processing_lease_seconds": 30,
            "tenderwise_processing_heartbeat_seconds": 10,
        },
        {
            "tenderwise_processing_retry_base_seconds": 20,
            "tenderwise_processing_retry_max_seconds": 10,
        },
    ],
)
def test_invalid_worker_settings_are_rejected(settings) -> None:
    with pytest.raises(ValidationError):
        worker_settings(**settings)


def test_fastapi_does_not_register_worker_startup_callback() -> None:
    from app.main import app

    assert all(callback.__module__ != "app.worker" for callback in app.router.on_startup)


@pytest.mark.parametrize(
    "attempt,base,maximum,random_value,expected",
    [
        (1, 5.0, 300.0, 0.5, 2.5),
        (3, 5.0, 300.0, 1.0, 20.0),
        (10, 5.0, 30.0, 0.5, 15.0),
        (10, 5.0, 30.0, 0.0, 0.0),
    ],
)
def test_retry_delay_uses_capped_exponential_backoff_and_jitter(
    attempt, base, maximum, random_value, expected
) -> None:
    assert retry_delay_seconds(
        attempt, base, maximum, random_value=lambda: random_value
    ) == expected


def make_claim() -> ClaimedProcessingItem:
    return ClaimedProcessingItem(
        item_id=uuid4(),
        run_id=uuid4(),
        document_id=uuid4(),
        tender_id=uuid4(),
        worker_id="worker-test",
        fencing_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        source_path=Path("private-source.pdf"),
    )


def test_worker_checks_at_most_one_handler_at_a_time(monkeypatch, tmp_path) -> None:
    claims = [make_claim(), make_claim()]
    claim_iterator = iter(claims)
    monkeypatch.setattr(worker_service, "claim_next_item", lambda *_args: next(claim_iterator, None))
    monkeypatch.setattr(worker_service, "persist_result", lambda *_args: True)

    class TrackingHandler:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0
            self.calls = 0
            self.shutdown = None

        def process(self, _context: ProcessingContext):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            time.sleep(0.02)
            self.active -= 1
            self.calls += 1
            if self.calls == 2:
                self.shutdown.set()
            return []

    shutdown = threading.Event()
    handler = TrackingHandler()
    handler.shutdown = shutdown
    worker = ProcessingWorker(
        lambda: None,
        worker_settings(tenderwise_processing_heartbeat_seconds=1),
        DocumentStorage(tmp_path / "storage"),
        handler,
    )
    worker.run(shutdown)

    assert handler.calls == 2
    assert handler.maximum_active == 1


def test_worker_passes_original_filename_and_extension_to_handler(monkeypatch, tmp_path) -> None:
    claim = make_claim()
    claim = ClaimedProcessingItem(
        **{**claim.__dict__, "original_filename": "Tender Notice.PDF", "extension": ".pdf"}
    )
    observed = []
    monkeypatch.setattr(worker_service, "persist_result", lambda *_args: True)

    class ContextHandler:
        def process(self, context: ProcessingContext):
            observed.append(context)
            return []

    worker = ProcessingWorker(
        lambda: None,
        worker_settings(tenderwise_processing_heartbeat_seconds=1),
        DocumentStorage(tmp_path / "storage"),
        ContextHandler(),
    )
    worker._process_claim(claim, threading.Event())

    assert len(observed) == 1
    assert observed[0].source_path == claim.source_path
    assert observed[0].original_filename == "Tender Notice.PDF"
    assert observed[0].extension == ".pdf"


def test_lost_heartbeat_cancels_handler_and_prevents_persistence(monkeypatch, tmp_path) -> None:
    claim = make_claim()
    cancelled = threading.Event()
    handler_started = threading.Event()
    persisted = []
    monkeypatch.setattr(worker_service, "renew_lease", lambda *_args: False)
    monkeypatch.setattr(worker_service, "persist_result", lambda *_args: persisted.append(True))

    class CooperativeHandler:
        def process(self, context: ProcessingContext):
            handler_started.set()
            assert context.cancellation_requested.wait(timeout=3)
            cancelled.set()
            return []

    worker = ProcessingWorker(
        lambda: None,
        worker_settings(tenderwise_processing_heartbeat_seconds=1),
        DocumentStorage(tmp_path / "storage"),
        CooperativeHandler(),
    )
    worker._process_claim(claim, threading.Event())

    assert handler_started.is_set()
    assert cancelled.is_set()
    assert not persisted


def test_shutdown_cancels_current_handler_and_schedules_retry(monkeypatch, tmp_path) -> None:
    claim = make_claim()
    handler_started = threading.Event()
    scheduled = []
    monkeypatch.setattr(worker_service, "record_failure", lambda *_args: scheduled.append(True) or True)

    class CooperativeHandler:
        def process(self, context: ProcessingContext):
            handler_started.set()
            assert context.cancellation_requested.wait(timeout=3)
            return []

    shutdown = threading.Event()
    worker = ProcessingWorker(
        lambda: None,
        worker_settings(tenderwise_processing_heartbeat_seconds=1),
        DocumentStorage(tmp_path / "storage"),
        CooperativeHandler(),
    )
    task = threading.Thread(target=worker._process_claim, args=(claim, shutdown))
    task.start()
    assert handler_started.wait(timeout=2)
    shutdown.set()
    task.join(timeout=3)

    assert not task.is_alive()
    assert scheduled


def test_timeout_cancels_handler_and_schedules_retry(monkeypatch, tmp_path) -> None:
    claim = make_claim()
    handler_started = threading.Event()
    scheduled_failures: list[ProcessingFailure] = []
    monkeypatch.setattr(
        worker_service,
        "record_failure",
        lambda _factory, _claim, failure, _settings: scheduled_failures.append(failure) or True,
    )

    class CooperativeHandler:
        def process(self, context: ProcessingContext):
            handler_started.set()
            assert context.cancellation_requested.wait(timeout=4)
            return []

    worker = ProcessingWorker(
        lambda: None,
        SimpleNamespace(
            tenderwise_processing_lease_seconds=30,
            tenderwise_processing_heartbeat_seconds=1,
            tenderwise_processing_timeout_seconds=0.1,
        ),
        DocumentStorage(tmp_path / "storage"),
        CooperativeHandler(),
    )
    worker._process_claim(claim, threading.Event())

    assert handler_started.is_set()
    assert [failure.outcome_code for failure in scheduled_failures] == ["processing_timeout"]


def test_worker_logs_do_not_include_failure_details(caplog, monkeypatch, tmp_path) -> None:
    claim = make_claim()
    sensitive = "private document text must not appear"
    monkeypatch.setattr(worker_service, "record_failure", lambda *_args: True)

    class FailingHandler:
        def process(self, _context: ProcessingContext):
            raise ProcessingFailure("permanent", "parser_error", sensitive)

    worker = ProcessingWorker(
        lambda: None,
        worker_settings(tenderwise_processing_heartbeat_seconds=1),
        DocumentStorage(tmp_path / "storage"),
        FailingHandler(),
    )
    worker._process_claim(claim, threading.Event())

    assert sensitive not in caplog.text
    assert claim.source_path.as_posix() not in caplog.text


@pytest.mark.parametrize(
    "sensitive",
    ["private exception text", "secret.pdf", "C:/private/secret.pdf", "extracted tender text"],
)
def test_handler_failure_text_never_becomes_api_visible_message(sensitive: str) -> None:
    failure = ProcessingFailure("permanent", "unknown_handler_code", sensitive)
    failure_class, outcome_code, safe_message = safe_failure_details(failure)

    assert failure_class == "permanent"
    assert outcome_code == "extraction_failed"
    assert safe_message == "Document processing could not be completed."
    assert sensitive not in safe_message


def test_result_database_failure_is_scheduled_as_retryable(monkeypatch, tmp_path) -> None:
    claim = make_claim()
    failures: list[ProcessingFailure] = []
    monkeypatch.setattr(
        worker_service,
        "persist_result",
        lambda *_args: (_ for _ in ()).throw(SQLAlchemyError("private database detail")),
    )
    monkeypatch.setattr(
        worker_service,
        "record_failure",
        lambda _factory, _claim, failure, _settings: failures.append(failure) or True,
    )

    class EmptyHandler:
        def process(self, _context: ProcessingContext):
            return []

    worker = ProcessingWorker(
        lambda: None,
        worker_settings(tenderwise_processing_heartbeat_seconds=1),
        DocumentStorage(tmp_path / "storage"),
        EmptyHandler(),
    )
    worker._process_claim(claim, threading.Event())

    assert [(failure.failure_class, failure.outcome_code) for failure in failures] == [
        ("retryable", "database_error")
    ]
