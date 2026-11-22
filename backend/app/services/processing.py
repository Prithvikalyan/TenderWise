from collections import Counter
from datetime import UTC, datetime
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document_processing import (
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    DocumentExtractionUnit,
)
from app.models.tender import Tender, utc_now
from app.models.tender_document import TenderDocument

ITEM_STATUSES = {"pending", "processing", "completed", "failed"}


class InvalidRunStateError(ValueError):
    """Raised when a run has no items or contains an invalid item status."""


def calculate_run_status(item_statuses: Iterable[str]) -> str:
    """Calculate the approved run aggregate from all item statuses."""
    statuses = list(item_statuses)
    if not statuses:
        raise InvalidRunStateError("A processing run must contain at least one item")
    invalid = set(statuses) - ITEM_STATUSES
    if invalid:
        raise InvalidRunStateError("A processing run contains an invalid item status")

    counts = Counter(statuses)
    if counts["pending"] == len(statuses):
        return "pending"
    if counts["processing"] or (counts["pending"] and (counts["completed"] or counts["failed"])):
        return "processing"
    if counts["completed"] == len(statuses):
        return "completed"
    if counts["pending"] or counts["processing"]:
        # Defensive fallback for states that should have been covered above.
        return "processing"
    if counts["completed"] and counts["failed"]:
        return "partially_completed"
    return "failed"


def aggregate_run_status(session: Session, run_id: UUID) -> str:
    """Recompute and stage a run status inside the caller's transaction.

    Locking the run row serializes aggregations for the same run. Callers that
    transition item state must use this function before committing that same
    transaction.
    """
    with session.no_autoflush:
        run = session.scalar(
            select(DocumentProcessingRun)
            .where(DocumentProcessingRun.id == run_id)
            .with_for_update()
        )
    if run is None:
        raise LookupError("Processing run not found")
    # The application session factory disables autoflush. Flush staged item
    # transitions only after locking the parent run, then read their new state.
    session.flush()
    statuses = list(
        session.scalars(
            select(DocumentProcessingRunItem.status)
            .where(DocumentProcessingRunItem.run_id == run_id)
        ).all()
    )
    now = utc_now()
    run.status = calculate_run_status(statuses)
    run.updated_at = now
    if run.status in {"completed", "partially_completed", "failed"}:
        run.completed_at = run.completed_at or now
    else:
        run.completed_at = None
    return run.status


def create_processing_run(
    session: Session, tender_id: UUID, document_ids: list[UUID]
) -> DocumentProcessingRun:
    """Create a run and all requested items atomically; never start processing."""
    if (
        not 1 <= len(document_ids) <= get_settings().tenderwise_processing_max_documents_per_run
        or len(document_ids) != len(set(document_ids))
    ):
        raise ValueError("Invalid document selection")
    if session.get(Tender, tender_id) is None:
        raise LookupError("Tender not found")

    documents = list(
        session.scalars(
            select(TenderDocument).where(
                TenderDocument.tender_id == tender_id,
                TenderDocument.id.in_(document_ids),
            )
        ).all()
    )
    if len(documents) != len(document_ids):
        raise LookupError("One or more documents were not found")

    now = datetime.now(UTC)
    run = DocumentProcessingRun(
        tender_id=tender_id,
        status="pending",
        requested_document_count=len(document_ids),
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
    )
    run.items = [
        DocumentProcessingRunItem(
            document_id=document_id,
            status="pending",
            attempt_count=0,
            max_attempts=3,
            fencing_token=0,
            next_attempt_at=now,
        )
        for document_id in document_ids
    ]
    session.add(run)
    try:
        session.commit()
        session.refresh(run)
        return run
    except BaseException:
        session.rollback()
        raise


def list_processing_runs(
    session: Session, tender_id: UUID, *, page: int, limit: int
) -> tuple[list[DocumentProcessingRun], int]:
    offset = (page - 1) * limit
    total = session.scalar(
        select(func.count())
        .select_from(DocumentProcessingRun)
        .where(DocumentProcessingRun.tender_id == tender_id)
    ) or 0
    runs = list(
        session.scalars(
            select(DocumentProcessingRun)
            .where(DocumentProcessingRun.tender_id == tender_id)
            .order_by(DocumentProcessingRun.created_at.desc(), DocumentProcessingRun.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return runs, total


def get_processing_run(
    session: Session, tender_id: UUID, run_id: UUID, *, page: int, limit: int
) -> tuple[DocumentProcessingRun, str, list[DocumentProcessingRunItem], int, Counter[str]] | None:
    run = session.scalar(
        select(DocumentProcessingRun).where(
            DocumentProcessingRun.id == run_id,
            DocumentProcessingRun.tender_id == tender_id,
        )
    )
    if run is None:
        return None

    status_rows = session.execute(
        select(DocumentProcessingRunItem.status, func.count())
        .where(DocumentProcessingRunItem.run_id == run_id)
        .group_by(DocumentProcessingRunItem.status)
    ).all()
    counts: Counter[str] = Counter({status: count for status, count in status_rows})
    # Validate the complete state and report its aggregate independently from
    # the pagination window.
    aggregated = calculate_run_status(
        status for status, count in counts.items() for _ in range(count)
    )
    total = sum(counts.values())
    offset = (page - 1) * limit
    items = list(
        session.scalars(
            select(DocumentProcessingRunItem)
            .where(DocumentProcessingRunItem.run_id == run_id)
            .order_by(DocumentProcessingRunItem.created_at.asc(), DocumentProcessingRunItem.id.asc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return run, aggregated, items, total, counts


def list_document_processing_history(
    session: Session,
    tender_id: UUID,
    document_id: UUID,
    *,
    page: int,
    limit: int,
) -> tuple[list[tuple[DocumentProcessingRun, DocumentProcessingRunItem]], int] | None:
    document_exists = session.scalar(
        select(TenderDocument.id).where(
            TenderDocument.id == document_id,
            TenderDocument.tender_id == tender_id,
        )
    )
    if document_exists is None:
        return None

    filters = (
        DocumentProcessingRunItem.document_id == document_id,
        DocumentProcessingRun.tender_id == tender_id,
    )
    total = session.scalar(
        select(func.count())
        .select_from(DocumentProcessingRunItem)
        .join(DocumentProcessingRun, DocumentProcessingRun.id == DocumentProcessingRunItem.run_id)
        .where(*filters)
    ) or 0
    rows = list(
        session.execute(
            select(DocumentProcessingRun, DocumentProcessingRunItem)
            .join(DocumentProcessingRun, DocumentProcessingRun.id == DocumentProcessingRunItem.run_id)
            .where(*filters)
            .order_by(
                DocumentProcessingRun.created_at.desc(),
                DocumentProcessingRun.id.desc(),
            )
            .limit(limit)
            .offset((page - 1) * limit)
        ).all()
    )
    return rows, total


def _extraction_unit_preview_text(unit: DocumentExtractionUnit) -> str:
    """Convert extracted text/table payloads to a compact human-readable preview."""
    if unit.unit_type != "table":
        return (unit.content_text or "").strip()

    def flatten(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [part for item in value for part in flatten(item) if part]
        if isinstance(value, dict):
            parts: list[str] = []
            rows = value.get("rows")
            if isinstance(rows, list):
                for row in rows:
                    cells = row.get("cells", []) if isinstance(row, dict) else []
                    values = []
                    for cell in cells if isinstance(cells, list) else []:
                        if isinstance(cell, dict):
                            cell_value = (
                                cell.get("text") or cell.get("value")
                                or cell.get("cached_value") or cell.get("formula")
                            )
                        else:
                            cell_value = cell
                        if cell_value is not None and str(cell_value).strip():
                            values.append(str(cell_value).strip())
                    if values:
                        parts.append(" | ".join(values))
                return parts
            for key in ("text", "value", "cached_value", "formula"):
                item = value.get(key)
                if item is not None and str(item).strip():
                    parts.append(str(item).strip())
            return parts
        return [str(value).strip()] if str(value).strip() else []

    return "\n".join(flatten(unit.table_data)).strip()


def get_processing_item_extraction(
    session: Session,
    tender_id: UUID,
    run_id: UUID,
    item_id: UUID,
) -> tuple[DocumentProcessingRun, DocumentProcessingRunItem, str, list[DocumentExtractionUnit]] | None:
    """Return extracted units for one item, strictly scoped to its tender run."""
    row = session.execute(
        select(DocumentProcessingRun, DocumentProcessingRunItem, TenderDocument.original_filename)
        .join(DocumentProcessingRunItem, DocumentProcessingRunItem.run_id == DocumentProcessingRun.id)
        .join(TenderDocument, TenderDocument.id == DocumentProcessingRunItem.document_id)
        .where(
            DocumentProcessingRun.id == run_id,
            DocumentProcessingRun.tender_id == tender_id,
            DocumentProcessingRunItem.id == item_id,
            DocumentProcessingRunItem.status == "completed",
            DocumentProcessingRunItem.document_id.is_not(None),
        )
    ).one_or_none()
    if row is None:
        return None

    run, item, filename = row
    units = list(
        session.scalars(
            select(DocumentExtractionUnit)
            .where(DocumentExtractionUnit.run_item_id == item.id)
            .order_by(DocumentExtractionUnit.unit_index.asc())
        ).all()
    )
    return run, item, filename, units


def format_extraction_unit_preview(unit: DocumentExtractionUnit) -> str:
    """Public service helper used by the processing API response."""
    return _extraction_unit_preview_text(unit)
