"""Coordinate physical source deletion with processing ownership and results."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.document_processing import (
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
)
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services.document_storage import DocumentStorage, StorageError
from app.services.processing import aggregate_run_status


def _invalidate_items(session: Session, items: list[DocumentProcessingRunItem]) -> set[UUID]:
    run_ids: set[UUID] = set()
    for item in items:
        run_ids.add(item.run_id)
        item.fencing_token += 1
        item.worker_id = None
        item.claimed_at = None
        item.lease_expires_at = None
        item.heartbeat_at = None
        item.document_id = None
        item.status = "failed"
        item.outcome_code = "source_deleted"
        item.failure_class = "permanent"
        item.safe_message = None
    if items:
        session.execute(
            delete(DocumentExtractionUnit).where(
                DocumentExtractionUnit.run_item_id.in_([item.id for item in items])
            )
        )
    return run_ids


def delete_source_document(session: Session, document_id: UUID, storage: DocumentStorage) -> None:
    manifest = None
    try:
        locked_document = session.scalar(
            select(TenderDocument)
            .where(TenderDocument.id == document_id)
            .with_for_update()
        )
        if locked_document is None:
            raise LookupError("Document not found")
        manifest, _moved = storage.quarantine_documents(
            [(locked_document.id, locked_document.tender_id, locked_document.storage_key)]
        )
        items = list(
            session.scalars(
                select(DocumentProcessingRunItem)
                .where(DocumentProcessingRunItem.document_id == document_id)
                .order_by(DocumentProcessingRunItem.id)
                .with_for_update()
            ).all()
        )
        run_ids = _invalidate_items(session, items)
        for run_id in sorted(run_ids):
            aggregate_run_status(session, run_id)
        session.delete(locked_document)
        session.commit()
    except BaseException:
        session.rollback()
        if manifest is not None:
            storage.resolve_operation(manifest, sessionmaker_for_session(session))
        raise
    if manifest is not None:
        storage.finish_operation(manifest, committed=True)


def delete_tender_sources(
    session: Session,
    tender_id: UUID,
    *,
    storage_factory: Callable[[], DocumentStorage] = DocumentStorage,
) -> None:
    manifest = None
    storage = None
    document_ids: list[UUID] = []
    try:
        # The tender row serializes upload and deletion; both lock parent first.
        tender = session.scalar(
            select(Tender).where(Tender.id == tender_id).with_for_update()
        )
        if tender is None:
            raise LookupError("Tender not found")
        source_rows = list(
            session.execute(
                select(TenderDocument.id, TenderDocument.tender_id, TenderDocument.storage_key)
                .where(TenderDocument.tender_id == tender_id)
                .order_by(TenderDocument.id)
            ).all()
        )
        document_ids = [row.id for row in source_rows]
        documents = list(
            session.scalars(
                select(TenderDocument)
                .where(TenderDocument.tender_id == tender_id)
                .order_by(TenderDocument.id)
                .with_for_update()
            ).all()
        )
        if {document.id for document in documents} != set(document_ids):
            raise StorageError("Tender documents changed during deletion")
        if documents:
            storage = storage_factory()
            manifest, _moved = storage.quarantine_documents(
                [(document.id, tender_id, document.storage_key) for document in documents]
            )

        items = list(
            session.scalars(
                select(DocumentProcessingRunItem)
                .where(DocumentProcessingRunItem.document_id.in_(document_ids))
                .order_by(DocumentProcessingRunItem.id)
                .with_for_update()
            ).all()
        ) if document_ids else []
        run_ids = _invalidate_items(session, items)
        for run_id in sorted(run_ids):
            aggregate_run_status(session, run_id)
        session.delete(tender)
        session.commit()
    except BaseException:
        session.rollback()
        if storage is not None and manifest is not None:
            storage.resolve_operation(manifest, sessionmaker_for_session(session))
        raise

    if storage is not None and manifest is not None:
        storage.finish_operation(manifest, committed=True)


def sessionmaker_for_session(session: Session):
    """Build a fresh-session factory on the same bind for commit-outcome checks."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=session.get_bind(), class_=Session, autoflush=False, autocommit=False)
