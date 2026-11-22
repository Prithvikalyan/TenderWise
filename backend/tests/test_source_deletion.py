from uuid import uuid4
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    DocumentCategory,
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    Tender,
    TenderDocument,
)
from app.services.document_storage import DocumentStorage
from app.services.source_deletion import delete_source_document, delete_tender_sources
import app.services.document_storage as document_storage_module


@pytest.fixture
def deletion_context(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    storage = DocumentStorage(tmp_path / "private-storage")
    try:
        yield engine, storage
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def seed_document(session: Session, storage: DocumentStorage, *, status="pending"):
    tender = Tender(name=f"Deletion test {uuid4()}")
    document = TenderDocument(
        tender=tender,
        original_filename="sensitive-source.pdf",
        storage_key=f"{uuid4().hex}.pdf",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=4,
        category=DocumentCategory.MAIN_TENDER,
    )
    run = DocumentProcessingRun(tender=tender, requested_document_count=1)
    item = DocumentProcessingRunItem(
        run=run,
        document=document,
        status=status,
        attempt_count=1 if status == "processing" else 0,
        worker_id="active-worker" if status == "processing" else None,
        claimed_at=None,
        lease_expires_at=None,
        heartbeat_at=None,
        fencing_token=2 if status == "processing" else 0,
    )
    session.add(tender)
    session.flush()
    path = storage.resolve_key(document.storage_key)
    path.write_bytes(b"source")
    session.add(
        DocumentExtractionUnit(
            run_item=item,
            unit_index=0,
            unit_type="text",
            source_ref={"page": 1, "filename": "sensitive-source.pdf"},
            content_text="sensitive extracted content",
            metadata_json={"filename": "sensitive-source.pdf"},
        )
    )
    session.commit()
    return tender, document, run, item, path


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_document_deletion_invalidates_item_and_removes_derived_content(
    deletion_context, status
):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, run, item, path = seed_document(session, storage, status=status)
        document_id, item_id, run_id = document.id, item.id, run.id
        old_token = item.fencing_token
        delete_source_document(session, document_id, storage)

    assert not path.exists()
    assert list(storage.quarantine.glob("*.json")) == []
    with Session(engine) as session:
        assert session.get(TenderDocument, document_id) is None
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None
        assert item.document_id is None
        assert item.status == "failed"
        assert item.outcome_code == "source_deleted"
        assert item.failure_class == "permanent"
        assert item.safe_message is None
        assert item.fencing_token == old_token + 1
        assert item.worker_id is None
        assert item.claimed_at is None
        assert item.lease_expires_at is None
        assert item.heartbeat_at is None
        assert session.scalar(
            select(func.count()).select_from(DocumentExtractionUnit)
        ) == 0
        run = session.get(DocumentProcessingRun, run_id)
        assert run is not None and run.status == "failed"


def test_quarantine_failure_leaves_database_unchanged(deletion_context, monkeypatch):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, _run, _item, path = seed_document(session, storage)
        document_id = document.id

        def fail_quarantine(_keys):
            raise OSError("private path detail")

        monkeypatch.setattr(storage, "quarantine_documents", fail_quarantine)
        with pytest.raises(OSError):
            delete_source_document(session, document_id, storage)

    assert path.exists()
    with Session(engine) as session:
        assert session.get(TenderDocument, document_id) is not None


def test_database_failure_restores_source_and_metadata(deletion_context):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, _run, _item, path = seed_document(session, storage)
        document_id = document.id

        def fail_commit(_session):
            raise SQLAlchemyError("private database detail")

        event.listen(Session, "before_commit", fail_commit)
        try:
            with pytest.raises(SQLAlchemyError):
                delete_source_document(session, document_id, storage)
        finally:
            event.remove(Session, "before_commit", fail_commit)

    assert path.exists()
    assert list(storage.quarantine.glob("*.json")) == []
    with Session(engine) as session:
        doc = session.get(TenderDocument, document_id)
        assert doc is not None and doc.original_filename == "sensitive-source.pdf"


def test_tender_deletion_coordinates_multiple_documents_and_items(deletion_context):
    engine, storage = deletion_context
    with Session(engine) as session:
        tender = Tender(name="Multi-document deletion")
        session.add(tender)
        session.flush()
        paths = []
        document_ids = []
        item_ids = []
        for index in range(2):
            document = TenderDocument(
                tender=tender,
                original_filename=f"private-{index}.pdf",
                storage_key=f"{uuid4().hex}.pdf",
                extension=".pdf",
                content_type="application/pdf",
                size_bytes=4,
                category=DocumentCategory.MAIN_TENDER,
            )
            run = DocumentProcessingRun(tender=tender, requested_document_count=1)
            item = DocumentProcessingRunItem(
                run=run,
                document=document,
                status="processing",
                attempt_count=1,
                worker_id=f"worker-{index}",
                fencing_token=4,
            )
            session.add_all([document, run])
            session.flush()
            path = storage.resolve_key(document.storage_key)
            path.write_bytes(b"source")
            paths.append(path)
            document_ids.append(document.id)
            item_ids.append(item.id)
            session.add(
                DocumentExtractionUnit(
                    run_item=item,
                    unit_index=0,
                    unit_type="text",
                    source_ref={"page": 1},
                    content_text="derived content",
                )
            )
        tender_id = tender.id
        session.commit()
        delete_tender_sources(session, tender_id, storage_factory=lambda: storage)

    assert all(not path.exists() for path in paths)
    assert list(storage.quarantine.glob("*.json")) == []
    with Session(engine) as session:
        assert session.get(Tender, tender_id) is None
        assert all(session.get(TenderDocument, doc_id) is None for doc_id in document_ids)
        assert all(session.get(DocumentProcessingRunItem, item_id) is None for item_id in item_ids)
        assert session.scalar(select(func.count()).select_from(DocumentExtractionUnit)) == 0


def test_pending_purge_manifest_is_safe_to_retry(deletion_context):
    _engine, storage = deletion_context
    held = storage.quarantine / "opaque-token.held"
    held.write_bytes(b"quarantined")
    manifest = storage.quarantine / "purge-retry-token.json"
    manifest.write_text(
        '{"state":"purge","files":[{"source":"source.pdf","held":"opaque-token.held"}]}',
        encoding="utf-8",
    )

    storage.retry_pending_purges()
    storage.retry_pending_purges()

    assert not held.exists()
    assert not manifest.exists()


def test_partial_tender_quarantine_failure_restores_earlier_files(deletion_context, monkeypatch):
    engine, storage = deletion_context
    with Session(engine) as session:
        tender = Tender(name="Partial quarantine rollback")
        session.add(tender)
        session.flush()
        paths = []
        for index in range(2):
            document = TenderDocument(
                tender=tender,
                original_filename=f"private-{index}.pdf",
                storage_key=f"{uuid4().hex}.pdf",
                extension=".pdf",
                content_type="application/pdf",
                size_bytes=4,
                category=DocumentCategory.MAIN_TENDER,
            )
            session.add(document)
            session.flush()
            path = storage.resolve_key(document.storage_key)
            path.write_bytes(b"source")
            paths.append(path)
        tender_id = tender.id
        session.commit()

        real_replace = document_storage_module.os.replace
        calls = 0

        def fail_second_move(source, target):
            nonlocal calls
            calls += 1
            if target.suffix == ".held" and calls == 2:
                raise OSError("private path detail")
            return real_replace(source, target)

        monkeypatch.setattr(document_storage_module.os, "replace", fail_second_move)
        with pytest.raises(OSError):
            delete_tender_sources(session, tender_id, storage_factory=lambda: storage)

    assert all(path.exists() for path in paths)
    assert list(storage.quarantine.glob("*.json")) == []
    with Session(engine) as session:
        assert session.get(Tender, tender_id) is not None
        assert session.scalar(select(func.count()).select_from(TenderDocument)) == 2


def test_post_commit_purge_failure_never_restores_source(deletion_context, monkeypatch):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, _run, _item, path = seed_document(session, storage)
        document_id = document.id
        real_unlink = type(path).unlink
        failed_once = False

        def fail_held_unlink(target, *args, **kwargs):
            nonlocal failed_once
            if target.suffix == ".held" and not failed_once:
                failed_once = True
                raise OSError("private cleanup detail")
            return real_unlink(target, *args, **kwargs)

        monkeypatch.setattr(type(path), "unlink", fail_held_unlink)
        with pytest.raises(OSError):
            delete_source_document(session, document_id, storage)

    assert not path.exists()
    with Session(engine) as session:
        assert session.get(TenderDocument, document_id) is None
    assert failed_once
    assert list(storage.quarantine.glob("delete-*.json"))
    monkeypatch.setattr(type(path), "unlink", real_unlink)
    storage2 = DocumentStorage(storage.root)
    storage2.recover_pending_deletions(lambda: Session(engine))
    assert list(storage.quarantine.glob("*.json")) == []


def test_recovery_restores_incomplete_quarantine_idempotently(deletion_context):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, _run, _item, path = seed_document(session, storage)
        doc_id, tender_id, key = document.id, document.tender_id, document.storage_key

    manifest, moved = storage.quarantine_documents([(doc_id, tender_id, key)])
    assert manifest is not None and moved and not path.exists()
    storage._release_journal_lock()  # Simulate process death before recovery.
    storage = DocumentStorage(storage.root)
    factory = lambda: Session(engine)
    storage.recover_pending_deletions(factory)
    storage.recover_pending_deletions(factory)

    assert path.exists()
    assert list(storage.quarantine.glob("delete-*.json")) == []


def test_recovery_purges_committed_deletion_and_is_idempotent(deletion_context):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, _run, _item, path = seed_document(session, storage)
        doc_id, tender_id, key = document.id, document.tender_id, document.storage_key

    manifest, moved = storage.quarantine_documents([(doc_id, tender_id, key)])
    assert manifest is not None and moved
    storage._release_journal_lock()  # Simulate process death before database commit.
    storage = DocumentStorage(storage.root)
    with Session(engine) as session:
        session.delete(session.get(TenderDocument, doc_id))
        session.commit()
    factory = lambda: Session(engine)
    storage.recover_pending_deletions(factory)
    storage.recover_pending_deletions(factory)

    assert not path.exists()
    assert list(storage.quarantine.glob("*.held")) == []
    assert list(storage.quarantine.glob("delete-*.json")) == []


def test_recovery_defers_when_database_is_unavailable(deletion_context):
    engine, storage = deletion_context
    with Session(engine) as session:
        _tender, document, _run, _item, path = seed_document(session, storage)
        doc_id, tender_id, key = document.id, document.tender_id, document.storage_key
    manifest, _moved = storage.quarantine_documents([(doc_id, tender_id, key)])
    assert manifest is not None
    storage._release_journal_lock()

    recovered = DocumentStorage(storage.root)

    def unavailable():
        raise OSError("database unavailable")

    recovered.recover_pending_deletions(unavailable)
    assert manifest.exists()
    assert not path.exists()
    recovered.recover_pending_deletions(lambda: Session(engine))
    assert path.exists()
    assert not manifest.exists()


def test_recovery_can_resume_after_interrupted_purge(deletion_context, monkeypatch):
    engine, storage = deletion_context
    with Session(engine) as session:
        tender = Tender(name="Interrupted recovery")
        session.add(tender)
        session.flush()
        documents = []
        for index in range(2):
            document = TenderDocument(
                tender=tender,
                original_filename=f"recovery-{index}.pdf",
                storage_key=f"{uuid4().hex}.pdf",
                extension=".pdf",
                content_type="application/pdf",
                size_bytes=4,
                category=DocumentCategory.MAIN_TENDER,
            )
            session.add(document)
            session.flush()
            storage.resolve_key(document.storage_key).write_bytes(b"source")
            documents.append(document)
        session.commit()
        doc_data = [(d.id, d.tender_id, d.storage_key) for d in documents]
        tender_id = tender.id
    manifest, _moved = storage.quarantine_documents(doc_data)
    assert manifest is not None
    with Session(engine) as session:
        session.delete(session.get(Tender, tender_id))
        session.commit()
    storage._release_journal_lock()

    recovered = DocumentStorage(storage.root)
    real_unlink = Path.unlink
    failures = 0

    def fail_once(target, *args, **kwargs):
        nonlocal failures
        if target.suffix == ".held":
            failures += 1
            if failures == 2:
                raise OSError("interrupted purge")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    recovered.recover_pending_deletions(lambda: Session(engine))
    assert manifest.exists()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    recovered.recover_pending_deletions(lambda: Session(engine))
    assert not manifest.exists()
    assert list(storage.quarantine.glob("*.held")) == []


def test_storage_startup_removes_abandoned_leased_work_files(tmp_path):
    root = tmp_path / "storage"
    staging = root / ".staging"
    staging.mkdir(parents=True)
    abandoned = staging / "work-abandoned.part"
    lease = staging / "work-abandoned.lease"
    abandoned.write_bytes(b"private abandoned source")
    lease.write_bytes(b"0")

    storage = DocumentStorage(root)

    assert not abandoned.exists()
    assert not lease.exists()
    assert storage.cleanup_abandoned_source_stages() == 0


def test_storage_cleanup_preserves_active_worker_stage(tmp_path):
    root = tmp_path / "storage"
    storage = DocumentStorage(root)
    source = storage.resolve_key("source.pdf")
    source.write_bytes(b"active source")
    staged = storage.stage_source(source.name)

    recovered_storage = DocumentStorage(root)
    assert staged.exists()
    assert recovered_storage.cleanup_abandoned_source_stages() == 0
    assert staged.exists()

    storage.cleanup_source_stage(staged)
    assert not staged.exists()
    assert not staged.with_suffix(".lease").exists()


def test_storage_cleanup_never_removes_concurrent_worker_stages(tmp_path):
    root = tmp_path / "storage"
    storage = DocumentStorage(root)
    for index in range(2):
        storage.resolve_key(f"source-{index}.pdf").write_bytes(f"source-{index}".encode())

    with ThreadPoolExecutor(max_workers=2) as pool:
        staged = list(pool.map(lambda index: storage.stage_source(f"source-{index}.pdf"), range(2)))

    concurrent_cleaner = DocumentStorage(root)
    with ThreadPoolExecutor(max_workers=1) as pool:
        removed = pool.submit(concurrent_cleaner.cleanup_abandoned_source_stages).result(timeout=5)
    assert removed == 0
    assert all(path.exists() for path in staged)

    for path in staged:
        storage.cleanup_source_stage(path)
    assert list(storage.staging.glob("work-*.part")) == []


def test_storage_cleanup_failure_preserves_file_for_retry(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    staging = root / ".staging"
    staging.mkdir(parents=True)
    abandoned = staging / "work-failure.part"
    lease = staging / "work-failure.lease"
    abandoned.write_bytes(b"private source")
    lease.write_bytes(b"0")
    real_unlink = Path.unlink

    def fail_file_once(path, *args, **kwargs):
        if path == abandoned:
            raise OSError("private filesystem detail")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_file_once)
    storage = DocumentStorage(root)
    assert abandoned.exists()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert storage.cleanup_abandoned_source_stages() == 1
    assert not abandoned.exists()
    assert not lease.exists()
