"""Manual extraction queue operations for reusable company-document versions."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.company import Company, CompanyDocument, CompanyDocumentVersion
from app.models.document_processing import (
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
)
from app.services.processing import calculate_run_status


class CompanyNotFoundError(LookupError):
    pass


class CompanyArchivedError(ValueError):
    pass


class CompanyVersionNotFoundError(LookupError):
    pass


def create_company_processing_run(
    session: Session, company_id: UUID, version_ids: list[UUID]
) -> DocumentProcessingRun:
    """Enqueue selected versions; processing is performed later by the worker."""
    maximum = get_settings().tenderwise_processing_max_documents_per_run
    if not 1 <= len(version_ids) <= maximum or len(version_ids) != len(set(version_ids)):
        raise ValueError("Invalid company document version selection")

    company = session.get(Company, company_id)
    if company is None:
        raise CompanyNotFoundError("Company not found")
    if company.is_archived:
        raise CompanyArchivedError("Archived companies cannot start document processing")

    versions = list(
        session.scalars(
            select(CompanyDocumentVersion)
            .join(CompanyDocument, CompanyDocument.id == CompanyDocumentVersion.document_id)
            .where(
                CompanyDocument.company_id == company_id,
                CompanyDocument.is_archived.is_(False),
                CompanyDocumentVersion.id.in_(version_ids),
            )
        ).all()
    )
    if len(versions) != len(version_ids):
        raise CompanyVersionNotFoundError("One or more company document versions were not found")
    supported_extensions = {".pdf", ".docx", ".xlsx", ".csv"}
    if any(version.extension.lower() not in supported_extensions for version in versions):
        raise ValueError("Only PDF, DOCX, XLSX and CSV versions can be processed")

    now = datetime.now(UTC)
    run = DocumentProcessingRun(
        tender_id=None,
        company_id=company_id,
        status="pending",
        requested_document_count=len(version_ids),
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
    )
    run.items = [
        DocumentProcessingRunItem(
            company_document_version_id=version_id,
            status="pending",
            attempt_count=0,
            max_attempts=3,
            fencing_token=0,
            next_attempt_at=now,
        )
        for version_id in version_ids
    ]
    session.add(run)
    try:
        session.commit()
        session.refresh(run)
        return run
    except BaseException:
        session.rollback()
        raise


def list_company_processing_runs(
    session: Session, company_id: UUID, *, page: int, limit: int
) -> tuple[list[DocumentProcessingRun], int]:
    offset = (page - 1) * limit
    filters = (DocumentProcessingRun.company_id == company_id,)
    total = session.scalar(
        select(func.count()).select_from(DocumentProcessingRun).where(*filters)
    ) or 0
    runs = list(
        session.scalars(
            select(DocumentProcessingRun)
            .where(*filters)
            .order_by(DocumentProcessingRun.created_at.desc(), DocumentProcessingRun.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return runs, total


def get_company_processing_run(
    session: Session,
    company_id: UUID,
    run_id: UUID,
    *,
    page: int,
    limit: int,
) -> tuple[DocumentProcessingRun, str, list[DocumentProcessingRunItem], int, Counter[str]] | None:
    run = session.scalar(
        select(DocumentProcessingRun).where(
            DocumentProcessingRun.id == run_id,
            DocumentProcessingRun.company_id == company_id,
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
    aggregated = calculate_run_status(
        status for status, count in counts.items() for _ in range(count)
    )
    total = sum(counts.values())
    items = list(
        session.scalars(
            select(DocumentProcessingRunItem)
            .where(DocumentProcessingRunItem.run_id == run_id)
            .order_by(DocumentProcessingRunItem.created_at.asc(), DocumentProcessingRunItem.id.asc())
            .limit(limit)
            .offset((page - 1) * limit)
        ).all()
    )
    return run, aggregated, items, total, counts


def get_company_run_item_unit_counts(
    session: Session, item_ids: list[UUID]
) -> dict[UUID, int]:
    if not item_ids:
        return {}
    rows = session.execute(
        select(DocumentExtractionUnit.run_item_id, func.count(DocumentExtractionUnit.id))
        .where(DocumentExtractionUnit.run_item_id.in_(item_ids))
        .group_by(DocumentExtractionUnit.run_item_id)
    ).all()
    return {item_id: count for item_id, count in rows}


def list_company_version_extraction_units(
    session: Session,
    company_id: UUID,
    document_id: UUID,
    version_id: UUID,
    *,
    page: int,
    limit: int,
) -> tuple[UUID, list[DocumentExtractionUnit], int] | None:
    """Return the latest successful extraction for a scoped immutable version."""
    version_exists = session.scalar(
        select(CompanyDocumentVersion.id)
        .join(CompanyDocument, CompanyDocument.id == CompanyDocumentVersion.document_id)
        .where(
            CompanyDocumentVersion.id == version_id,
            CompanyDocumentVersion.document_id == document_id,
            CompanyDocument.company_id == company_id,
        )
    )
    if version_exists is None:
        return None

    item_row = session.execute(
        select(DocumentProcessingRunItem.id, DocumentProcessingRun.id)
        .join(DocumentProcessingRun, DocumentProcessingRun.id == DocumentProcessingRunItem.run_id)
        .where(
            DocumentProcessingRun.company_id == company_id,
            DocumentProcessingRunItem.company_document_version_id == version_id,
            DocumentProcessingRunItem.status == "completed",
        )
        .order_by(DocumentProcessingRun.created_at.desc(), DocumentProcessingRun.id.desc())
        .limit(1)
    ).first()
    if item_row is None:
        return None
    item_id, run_id = item_row

    total = session.scalar(
        select(func.count()).select_from(DocumentExtractionUnit).where(
            DocumentExtractionUnit.run_item_id == item_id
        )
    ) or 0
    units = list(
        session.scalars(
            select(DocumentExtractionUnit)
            .where(DocumentExtractionUnit.run_item_id == item_id)
            .order_by(DocumentExtractionUnit.unit_index)
            .limit(limit)
            .offset((page - 1) * limit)
        ).all()
    )
    return run_id, units, total
