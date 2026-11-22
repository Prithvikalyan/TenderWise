from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.document_processing import (
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
)
from app.models.tender_document import TenderDocument
from app.models.retrieval import DocumentRetrievalChunk
from app.models.company import CompanyDocument, CompanyDocumentVersion
from app.services.document_storage import DocumentStorage, StorageError
from app.services.document_validation import (
    ValidationResult,
    validate_document_source,
    validate_staged_source,
    validate_company_document_source,
    validate_company_document_staged_source,
)
from app.services.processing import aggregate_run_status
from app.services.retrieval_chunks import build_retrieval_chunks
from app.services.processing_handler import (
    ExtractionUnitData,
    ProcessingContext,
    ProcessingFailure,
    ProcessingHandler,
    safe_failure_details,
)

logger = logging.getLogger(__name__)

MAX_WORKER_PROCESSES = 2
MAX_CANDIDATES_PER_POLL = 32
WORKER_LOCK_NAMESPACE = 73142


class WorkerConfigurationError(RuntimeError):
    pass


class WorkerCapacityError(RuntimeError):
    pass


class LostOwnershipError(RuntimeError):
    pass


class SourceMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedProcessingItem:
    item_id: UUID
    run_id: UUID
    document_id: UUID | None
    tender_id: UUID | None
    worker_id: str
    fencing_token: int
    attempt_count: int
    max_attempts: int
    lease_expires_at: datetime
    source_path: Path
    original_filename: str = ""
    extension: str = ""
    company_document_version_id: UUID | None = None
    company_document_id: UUID | None = None
    company_id: UUID | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _require_postgresql(session: Session) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise WorkerConfigurationError("The processing worker requires PostgreSQL")


def acquire_worker_slot(engine: Engine, configured_max: int) -> tuple[int, Connection]:
    """Hold one of two PostgreSQL session advisory locks for process lifetime."""
    if not 1 <= configured_max <= MAX_WORKER_PROCESSES:
        raise WorkerConfigurationError("Configured worker process limit is invalid")
    if engine.dialect.name != "postgresql":
        raise WorkerConfigurationError("The processing worker requires PostgreSQL")

    for slot in range(configured_max):
        connection = engine.connect()
        try:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
                {"namespace": WORKER_LOCK_NAMESPACE, "slot": slot},
            )
            connection.commit()
            if acquired:
                return slot, connection
        except BaseException:
            connection.close()
            raise
        connection.close()
    raise WorkerCapacityError("Maximum processing worker count is already running")


def release_worker_slot(connection: Connection, slot: int) -> None:
    try:
        connection.scalar(
            text("SELECT pg_advisory_unlock(:namespace, :slot)"),
            {"namespace": WORKER_LOCK_NAMESPACE, "slot": slot},
        )
    finally:
        connection.close()


def _eligible_condition(now: datetime):
    return or_(
        and_(
            DocumentProcessingRunItem.status == "pending",
            DocumentProcessingRunItem.next_attempt_at <= now,
        ),
        and_(
            DocumentProcessingRunItem.status == "processing",
            DocumentProcessingRunItem.lease_expires_at <= now,
        ),
    )


def _effective_max_attempts(item: DocumentProcessingRunItem, settings) -> int:
    return min(item.max_attempts, settings.tenderwise_processing_max_attempts)


def _clear_claim(item: DocumentProcessingRunItem) -> None:
    item.worker_id = None
    item.lease_expires_at = None
    item.heartbeat_at = None


def _claim_source_filter(claim: ClaimedProcessingItem):
    if claim.company_document_version_id is not None:
        return DocumentProcessingRunItem.company_document_version_id == claim.company_document_version_id
    return DocumentProcessingRunItem.document_id == claim.document_id


def _lock_claim_source(session: Session, claim: ClaimedProcessingItem):
    """Lock and return the tender file or company version owned by this claim."""
    if claim.company_document_version_id is not None:
        row = session.scalar(
            select(CompanyDocumentVersion)
            .join(CompanyDocument, CompanyDocument.id == CompanyDocumentVersion.document_id)
            .where(
                CompanyDocumentVersion.id == claim.company_document_version_id,
                CompanyDocument.company_id == claim.company_id,
            )
            .with_for_update(read=True, key_share=True, of=CompanyDocumentVersion)
        )
        if row is None:
            return None
        return row, row.document
    row = session.scalar(
        select(TenderDocument)
        .where(
            TenderDocument.id == claim.document_id,
            TenderDocument.tender_id == claim.tender_id,
        )
        .with_for_update(read=True, key_share=True)
    )
    return (row, None) if row is not None else None


def _terminal_failure(
    session: Session,
    item: DocumentProcessingRunItem,
    *,
    outcome_code: str,
    safe_message: str,
    now: datetime,
    failure_class: str = "permanent",
) -> None:
    item.status = "failed"
    item.failure_class = failure_class
    item.outcome_code = outcome_code
    item.safe_message = safe_message[:240]
    item.completed_at = now
    _clear_claim(item)
    aggregate_run_status(session, item.run_id)


def claim_next_item(
    session_factory: Callable[[], Session],
    worker_id: str,
    settings,
    storage: DocumentStorage,
    *,
    now: datetime | None = None,
) -> ClaimedProcessingItem | None:
    """Claim a tender document or immutable company version for extraction."""
    now = now or _now()
    session = session_factory()
    source_stage: Path | None = None
    try:
        _require_postgresql(session)
        with session.begin():
            candidates = session.execute(
                select(
                    DocumentProcessingRunItem.id,
                    DocumentProcessingRunItem.document_id,
                    DocumentProcessingRunItem.company_document_version_id,
                    DocumentProcessingRunItem.run_id,
                    DocumentProcessingRun.tender_id,
                    DocumentProcessingRun.company_id,
                )
                .join(
                    DocumentProcessingRun,
                    DocumentProcessingRun.id == DocumentProcessingRunItem.run_id,
                )
                .where(
                    or_(
                        DocumentProcessingRunItem.document_id.is_not(None),
                        DocumentProcessingRunItem.company_document_version_id.is_not(None),
                    ),
                    _eligible_condition(now),
                )
                .order_by(
                    DocumentProcessingRunItem.next_attempt_at,
                    DocumentProcessingRunItem.created_at,
                    DocumentProcessingRunItem.id,
                )
                .limit(MAX_CANDIDATES_PER_POLL)
            ).all()

            for candidate_id, tender_document_id, company_version_id, run_id, tender_id, company_id in candidates:
                source = None
                company_document = None
                if tender_document_id is not None:
                    source = session.scalar(
                        select(TenderDocument)
                        .where(
                            TenderDocument.id == tender_document_id,
                            TenderDocument.tender_id == tender_id,
                        )
                        .with_for_update(read=True, key_share=True)
                    )
                    source_filter = DocumentProcessingRunItem.document_id == tender_document_id
                elif company_version_id is not None:
                    version = session.scalar(
                        select(CompanyDocumentVersion)
                        .join(CompanyDocument, CompanyDocument.id == CompanyDocumentVersion.document_id)
                        .where(
                            CompanyDocumentVersion.id == company_version_id,
                            CompanyDocument.company_id == company_id,
                        )
                        .with_for_update(read=True, key_share=True, of=CompanyDocumentVersion)
                    )
                    if version is not None:
                        company_document = version.document
                        source = version
                    source_filter = (
                        DocumentProcessingRunItem.company_document_version_id == company_version_id
                    )
                else:
                    continue

                # Lock the source row before the queue item so deletion and
                # processing cannot race past the staged private copy.
                item = session.scalar(
                    select(DocumentProcessingRunItem)
                    .where(
                        DocumentProcessingRunItem.id == candidate_id,
                        DocumentProcessingRunItem.run_id == run_id,
                        source_filter,
                        _eligible_condition(now),
                    )
                    .with_for_update(skip_locked=True)
                )
                if item is None:
                    continue

                if source is None:
                    _terminal_failure(
                        session,
                        item,
                        outcome_code="source_unavailable",
                        safe_message=safe_failure_details(
                            ProcessingFailure("permanent", "source_unavailable", "")
                        )[2],
                        now=now,
                    )
                    continue

                try:
                    if company_version_id is None:
                        validation = validate_document_source(
                            source,
                            tender_id,
                            storage,
                            settings.tenderwise_max_file_size_bytes,
                            settings.tenderwise_csv_fallback_encodings,
                        )
                    else:
                        validation = validate_company_document_source(
                            source,
                            company_document,
                            company_id,
                            storage,
                            settings.tenderwise_max_file_size_bytes,
                            settings.tenderwise_csv_fallback_encodings,
                        )
                except Exception:
                    logger.warning("Document validation failed item_id=%s", item.id)
                    validation = ValidationResult(None, None, "malformed_file")
                if not validation.valid:
                    outcome_code = validation.outcome_code or "malformed_file"
                    _terminal_failure(
                        session,
                        item,
                        outcome_code=outcome_code,
                        safe_message=safe_failure_details(
                            ProcessingFailure("permanent", outcome_code, "")
                        )[2],
                        now=now,
                    )
                    continue

                if item.attempt_count >= _effective_max_attempts(item, settings):
                    _terminal_failure(
                        session,
                        item,
                        outcome_code="attempt_limit_exceeded",
                        safe_message="Processing could not be completed after repeated attempts.",
                        now=now,
                        failure_class="retryable",
                    )
                    continue

                item.attempt_count += 1
                item.fencing_token += 1
                claimed_fencing_token = item.fencing_token
                item.status = "processing"
                item.worker_id = worker_id
                item.claimed_at = now
                item.heartbeat_at = now
                item.lease_expires_at = now + timedelta(
                    seconds=settings.tenderwise_processing_lease_seconds
                )
                item.started_at = item.started_at or now
                aggregate_run_status(session, item.run_id)
                run = session.get(DocumentProcessingRun, item.run_id)
                if run is not None and run.started_at is None:
                    run.started_at = now

                session.flush()
                confirmed_item = session.scalar(
                    select(DocumentProcessingRunItem)
                    .where(
                        DocumentProcessingRunItem.id == item.id,
                        source_filter,
                        DocumentProcessingRunItem.status == "processing",
                        DocumentProcessingRunItem.worker_id == worker_id,
                        DocumentProcessingRunItem.fencing_token == claimed_fencing_token,
                    )
                    .with_for_update()
                )
                if confirmed_item is None:
                    raise LostOwnershipError("Processing claim changed while staging source")

                source_stage = storage.stage_source(source.storage_key)
                try:
                    if company_version_id is None:
                        staged_validation = validate_staged_source(
                            source_stage,
                            source,
                            storage,
                            settings.tenderwise_max_file_size_bytes,
                            settings.tenderwise_csv_fallback_encodings,
                        )
                    else:
                        staged_validation = validate_company_document_staged_source(
                            source_stage,
                            source,
                            company_document,
                            company_id,
                            storage,
                            settings.tenderwise_max_file_size_bytes,
                            settings.tenderwise_csv_fallback_encodings,
                        )
                except Exception:
                    logger.warning("Staged document validation failed item_id=%s", item.id)
                    staged_validation = ValidationResult(None, None, "malformed_file")
                if not staged_validation.valid:
                    storage.cleanup_source_stage(source_stage)
                    source_stage = None
                    outcome_code = staged_validation.outcome_code or "malformed_file"
                    _terminal_failure(
                        session,
                        item,
                        outcome_code=outcome_code,
                        safe_message=safe_failure_details(
                            ProcessingFailure("permanent", outcome_code, "")
                        )[2],
                        now=now,
                    )
                    continue

                claim = ClaimedProcessingItem(
                    item_id=item.id,
                    run_id=item.run_id,
                    document_id=(source.id if company_version_id is None else None),
                    tender_id=tender_id,
                    worker_id=worker_id,
                    fencing_token=claimed_fencing_token,
                    attempt_count=item.attempt_count,
                    max_attempts=_effective_max_attempts(item, settings),
                    lease_expires_at=item.lease_expires_at,
                    source_path=source_stage,
                    original_filename=source.original_filename,
                    extension=source.extension.lower(),
                    company_document_version_id=company_version_id,
                    company_document_id=(company_document.id if company_document is not None else None),
                    company_id=company_id,
                )
                return claim
        return None
    except BaseException:
        if source_stage is not None:
            storage.cleanup_source_stage(source_stage)
        raise
    finally:
        session.close()


def renew_lease(
    session_factory: Callable[[], Session],
    claim: ClaimedProcessingItem,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or _now()
    session = session_factory()
    try:
        _require_postgresql(session)
        with session.begin():
            result = session.execute(
                update(DocumentProcessingRunItem)
                .where(
                    DocumentProcessingRunItem.id == claim.item_id,
                    _claim_source_filter(claim),
                    DocumentProcessingRunItem.status == "processing",
                    DocumentProcessingRunItem.worker_id == claim.worker_id,
                    DocumentProcessingRunItem.fencing_token == claim.fencing_token,
                    DocumentProcessingRunItem.lease_expires_at > now,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            return result.rowcount == 1
    finally:
        session.close()


def _validate_claimed_item(
    item: DocumentProcessingRunItem,
    claim: ClaimedProcessingItem,
    now: datetime,
) -> bool:
    lease_expires_at = item.lease_expires_at
    if lease_expires_at is not None and lease_expires_at.tzinfo is None:
        # SQLite drops timezone information from DateTime(timezone=True) values;
        # production PostgreSQL timestamps remain timezone-aware.
        lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
    return (
        item.status == "processing"
        and item.worker_id == claim.worker_id
        and item.fencing_token == claim.fencing_token
        and lease_expires_at is not None
        and lease_expires_at > now
        and (
            item.company_document_version_id == claim.company_document_version_id
            if claim.company_document_version_id is not None
            else item.document_id == claim.document_id
        )
    )


def _was_committed(
    session_factory: Callable[[], Session], claim: ClaimedProcessingItem
) -> bool:
    session = session_factory()
    try:
        item = session.get(DocumentProcessingRunItem, claim.item_id)
        return (
            item is not None
            and item.status == "completed"
            and item.fencing_token == claim.fencing_token
        )
    finally:
        session.close()


def _mark_source_deleted_if_owned(
    session_factory: Callable[[], Session],
    claim: ClaimedProcessingItem,
    *,
    now: datetime | None = None,
) -> bool:
    """Fence an active worker when the source row disappeared during work."""
    now = now or _now()
    session = session_factory()
    try:
        _require_postgresql(session)
        with session.begin():
            item = session.scalar(
                select(DocumentProcessingRunItem)
                .where(DocumentProcessingRunItem.id == claim.item_id)
                .with_for_update()
            )
            if (
                item is None
                or item.status != "processing"
                or item.worker_id != claim.worker_id
                or item.fencing_token != claim.fencing_token
                or (
                    item.company_document_version_id is not None
                    if claim.company_document_version_id is not None
                    else item.document_id is not None
                )
            ):
                return False
            session.execute(
                delete(DocumentRetrievalChunk).where(
                    DocumentRetrievalChunk.run_item_id == item.id
                )
            )
            session.execute(
                delete(DocumentExtractionUnit).where(
                    DocumentExtractionUnit.run_item_id == item.id
                )
            )
            item.fencing_token += 1
            item.status = "failed"
            item.outcome_code = "source_deleted"
            item.failure_class = "permanent"
            item.safe_message = None
            item.completed_at = now
            _clear_claim(item)
            aggregate_run_status(session, item.run_id)
        return True
    finally:
        session.close()


def persist_result(
    session_factory: Callable[[], Session],
    claim: ClaimedProcessingItem,
    units: Sequence[ExtractionUnitData],
    *,
    now: datetime | None = None,
) -> bool:
    """Persist a handler result atomically if the worker still owns its lease."""
    now = now or _now()
    session = session_factory()
    try:
        _require_postgresql(session)
        with session.begin():
            locked_source = _lock_claim_source(session, claim)
            if locked_source is None:
                raise SourceMissingError("Source document is no longer available")
            item = session.scalar(
                select(DocumentProcessingRunItem)
                .where(DocumentProcessingRunItem.id == claim.item_id)
                .with_for_update()
            )
            if item is None or not _validate_claimed_item(item, claim, now):
                raise LostOwnershipError("Processing claim is no longer owned")

            extraction_rows = []
            for index, unit in enumerate(units):
                source_ref = dict(unit.source_ref)
                if claim.company_document_version_id is not None:
                    source_ref.update(
                        {
                            "source_scope": "company",
                            "company_document_id": str(claim.company_document_id),
                            "company_document_version_id": str(claim.company_document_version_id),
                        }
                    )
                extraction_rows.append(
                    DocumentExtractionUnit(
                        run_item_id=item.id,
                        unit_index=index,
                        unit_type=unit.unit_type,
                        source_ref=source_ref,
                        content_text=unit.content_text,
                        table_data=unit.table_data,
                        metadata_json=unit.metadata,
                    )
                )
            session.add_all(extraction_rows)
            session.flush()
            # Persist searchable chunks in the same transaction as extraction
            # units. Company evidence extraction remains separate from tender RAG.
            if claim.tender_id is not None and claim.document_id is not None:
                session.execute(
                    delete(DocumentRetrievalChunk).where(
                        DocumentRetrievalChunk.run_item_id == item.id
                    )
                )
                retrieval_chunks = build_retrieval_chunks(
                    tender_id=claim.tender_id,
                    document_id=claim.document_id,
                    run_item_id=item.id,
                    units=extraction_rows,
                )
                session.add_all(retrieval_chunks)
            item.status = "completed"
            item.outcome_code = "processed"
            item.failure_class = None
            item.safe_message = None
            item.completed_at = now
            _clear_claim(item)
            aggregate_run_status(session, item.run_id)
        return True
    except LostOwnershipError:
        session.rollback()
        return False
    except SourceMissingError:
        session.rollback()
        _mark_source_deleted_if_owned(session_factory, claim, now=now)
        return False
    except SQLAlchemyError:
        # SQLAlchemy dispatches ``after_commit`` after the database has
        # committed, but before ``SessionTransaction.commit()`` has finished
        # its bookkeeping. If an acknowledgement hook raises there, rollback
        # on this Session can itself raise InvalidRequestError because the
        # transaction is already committed. The confirmation query below must
        # use a fresh Session and must not be masked by cleanup of the old one.
        try:
            session.rollback()
        except SQLAlchemyError:
            pass
        # Commit acknowledgement may have been lost after PostgreSQL committed.
        if _was_committed(session_factory, claim):
            return True
        raise
    finally:
        session.close()


def retry_delay_seconds(
    attempt_count: int,
    base_seconds: float,
    maximum_seconds: float,
    *,
    random_value: Callable[[], float] = random.random,
) -> float:
    """Capped exponential delay with full jitter in [0, capped delay]."""
    exponential = min(maximum_seconds, base_seconds * (2 ** max(0, attempt_count - 1)))
    return exponential * min(1.0, max(0.0, random_value()))


def record_failure(
    session_factory: Callable[[], Session],
    claim: ClaimedProcessingItem,
    failure: ProcessingFailure,
    settings,
    *,
    now: datetime | None = None,
    random_value: Callable[[], float] = random.random,
) -> bool:
    now = now or _now()
    failure_class, outcome_code, safe_message = safe_failure_details(failure)
    session = session_factory()
    disposition = "failed"
    try:
        _require_postgresql(session)
        with session.begin():
            locked_source = _lock_claim_source(session, claim)
            if locked_source is None:
                raise SourceMissingError("Source document is no longer available")
            item = session.scalar(
                select(DocumentProcessingRunItem)
                .where(DocumentProcessingRunItem.id == claim.item_id)
                .with_for_update()
            )
            if item is None or not _validate_claimed_item(item, claim, now):
                return False

            item.failure_class = failure_class
            item.outcome_code = outcome_code
            item.safe_message = safe_message
            if (
                failure_class == "retryable"
                and item.attempt_count < _effective_max_attempts(item, settings)
            ):
                delay = retry_delay_seconds(
                    item.attempt_count,
                    settings.tenderwise_processing_retry_base_seconds,
                    settings.tenderwise_processing_retry_max_seconds,
                    random_value=random_value,
                )
                item.status = "pending"
                disposition = "retry_scheduled"
                item.next_attempt_at = now + timedelta(seconds=delay)
                item.completed_at = None
                _clear_claim(item)
            else:
                item.status = "failed"
                if failure_class == "retryable":
                    item.outcome_code = "attempt_limit_exceeded"
                    item.safe_message = "Processing could not be completed after repeated attempts."
                item.completed_at = now
                _clear_claim(item)
            aggregate_run_status(session, item.run_id)
        logger.info("Processing %s operation_id=%s", disposition, claim.item_id)
        return True
    except SourceMissingError:
        session.rollback()
        _mark_source_deleted_if_owned(session_factory, claim, now=now)
        return False
    finally:
        session.close()


class ProcessingWorker:
    """Single-item-at-a-time worker runtime with lease heartbeat thread."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings,
        storage: DocumentStorage,
        handler: ProcessingHandler,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.storage = storage
        self.handler = handler
        self.worker_id = worker_id or str(uuid4())

    def run(self, shutdown_requested: threading.Event) -> None:
        while not shutdown_requested.is_set():
            try:
                claim = claim_next_item(
                    self.session_factory, self.worker_id, self.settings, self.storage
                )
            except Exception:
                logger.warning("Worker claim cycle failed worker_id=%s", self.worker_id)
                shutdown_requested.wait(self.settings.tenderwise_worker_poll_interval_seconds)
                continue
            if claim is None:
                shutdown_requested.wait(self.settings.tenderwise_worker_poll_interval_seconds)
                continue
            logger.info("Processing item claimed operation_id=%s", claim.item_id)
            try:
                self._process_claim(claim, shutdown_requested)
            except Exception:
                logger.warning("Worker item cycle failed operation_id=%s", claim.item_id)

    def _process_claim(
        self, claim: ClaimedProcessingItem, shutdown_requested: threading.Event
    ) -> None:
        cancel_handler = threading.Event()
        keeper_stop = threading.Event()
        lost_ownership = threading.Event()
        timed_out = threading.Event()
        keeper = threading.Thread(
            target=self._heartbeat_loop,
            args=(claim, shutdown_requested, keeper_stop, cancel_handler, lost_ownership, timed_out),
            name="tenderwise-lease-heartbeat",
            daemon=True,
        )
        keeper.start()
        try:
            try:
                units = self.handler.process(
                    ProcessingContext(
                        claim.document_id or claim.company_document_id,
                        claim.source_path,
                        cancel_handler,
                        original_filename=claim.original_filename,
                        extension=claim.extension,
                    )
                )
            except ProcessingFailure as failure:
                if not lost_ownership.is_set() and not shutdown_requested.is_set():
                    if record_failure(self.session_factory, claim, failure, self.settings):
                        logger.info("Processing item failure recorded operation_id=%s", claim.item_id)
                    else:
                        logger.info("Processing ownership lost operation_id=%s", claim.item_id)
                return
            except Exception:
                if not lost_ownership.is_set() and not shutdown_requested.is_set():
                    failure = ProcessingFailure(
                        "retryable", "processing_error", "Processing could not be completed."
                    )
                    if record_failure(self.session_factory, claim, failure, self.settings):
                        logger.warning("Processing handler failed operation_id=%s", claim.item_id)
                    else:
                        logger.info("Processing ownership lost operation_id=%s", claim.item_id)
                return

            if lost_ownership.is_set():
                logger.info("Processing ownership lost operation_id=%s", claim.item_id)
                return
            if timed_out.is_set():
                record_failure(
                    self.session_factory,
                    claim,
                    ProcessingFailure(
                        "retryable", "processing_timeout", "Processing exceeded its time limit."
                    ),
                    self.settings,
                )
                return
            if shutdown_requested.is_set():
                record_failure(
                    self.session_factory,
                    claim,
                    ProcessingFailure(
                        "retryable", "worker_shutdown", "Processing will resume when a worker is available."
                    ),
                    self.settings,
                )
                return
            # Recheck immediately before entering the persistence transaction.
            # The handler contract requires bounded work; a supervisor remains
            # responsible for a hard process termination deadline.
            if lost_ownership.is_set() or timed_out.is_set() or shutdown_requested.is_set():
                return
            try:
                persisted = persist_result(self.session_factory, claim, units)
            except SQLAlchemyError:
                try:
                    record_failure(
                        self.session_factory,
                        claim,
                        ProcessingFailure(
                            "retryable", "database_error", "Processing could not be saved temporarily."
                        ),
                        self.settings,
                    )
                except Exception:
                    logger.warning("Processing persistence recovery deferred operation_id=%s", claim.item_id)
                else:
                    logger.warning("Processing persistence failed operation_id=%s", claim.item_id)
                return
            if persisted:
                logger.info("Processing item completed operation_id=%s", claim.item_id)
            else:
                logger.info("Processing ownership lost operation_id=%s", claim.item_id)
        finally:
            keeper_stop.set()
            keeper.join(timeout=self.settings.tenderwise_processing_heartbeat_seconds + 1)
            self.storage.cleanup_source_stage(claim.source_path)

    def _heartbeat_loop(
        self,
        claim: ClaimedProcessingItem,
        shutdown_requested: threading.Event,
        keeper_stop: threading.Event,
        cancel_handler: threading.Event,
        lost_ownership: threading.Event,
        timed_out: threading.Event,
    ) -> None:
        started = time.monotonic()
        next_heartbeat = started + self.settings.tenderwise_processing_heartbeat_seconds
        while not keeper_stop.is_set():
            if shutdown_requested.is_set():
                cancel_handler.set()
                return
            elapsed = time.monotonic() - started
            if elapsed >= self.settings.tenderwise_processing_timeout_seconds:
                timed_out.set()
                cancel_handler.set()
                return
            wait_seconds = min(
                1.0,
                next_heartbeat - time.monotonic(),
                self.settings.tenderwise_processing_timeout_seconds - elapsed,
            )
            if keeper_stop.wait(max(0.01, wait_seconds)):
                return
            if shutdown_requested.is_set():
                cancel_handler.set()
                return
            if time.monotonic() - started >= self.settings.tenderwise_processing_timeout_seconds:
                timed_out.set()
                cancel_handler.set()
                return
            if time.monotonic() >= next_heartbeat:
                try:
                    renewed = renew_lease(
                        self.session_factory,
                        claim,
                        self.settings.tenderwise_processing_lease_seconds,
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lost_ownership.set()
                    cancel_handler.set()
                    return
                next_heartbeat += self.settings.tenderwise_processing_heartbeat_seconds


def run_worker_process(
    settings,
    handler: ProcessingHandler | None,
    shutdown_requested: threading.Event | None = None,
) -> None:
    if handler is None:
        raise WorkerConfigurationError("No production processing handler is registered")
    from sqlalchemy.orm import sessionmaker

    from app.db.session import get_engine

    engine = get_engine()
    slot, lock_connection = acquire_worker_slot(
        engine, settings.tenderwise_worker_max_processes
    )
    worker_id = f"worker-{slot}-{uuid4()}"
    session_factory = sessionmaker(
        bind=engine, class_=Session, autoflush=False, autocommit=False
    )
    shutdown_requested = shutdown_requested or threading.Event()
    try:
        storage = DocumentStorage()
        storage.recover_pending_deletions(session_factory)
        worker = ProcessingWorker(session_factory, settings, storage, handler, worker_id=worker_id)
        logger.info("Worker started worker_id=%s slot=%s", worker_id, slot)
        worker.run(shutdown_requested)
    finally:
        release_worker_slot(lock_connection, slot)
        engine.dispose()
