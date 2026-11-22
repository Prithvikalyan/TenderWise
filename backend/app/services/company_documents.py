from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.company import (
    Company,
    CompanyDocument,
    CompanyDocumentCategory,
    CompanyDocumentVersion,
)
from app.models.tender import utc_now
from app.services.document_storage import DocumentStorage
from app.services.documents import canonical_type, validate_signature, validate_upload_name

logger = logging.getLogger(__name__)


class CompanyNotFoundError(LookupError):
    pass


class CompanyArchivedError(ValueError):
    pass


class CompanyDocumentNotFoundError(LookupError):
    pass


class CompanyDocumentVersionNotFoundError(LookupError):
    pass


def _validate_dates(issue_date: date | None, expiry_date: date | None) -> None:
    if issue_date is not None and expiry_date is not None and expiry_date < issue_date:
        raise ValueError("Expiry date cannot be earlier than issue date")


_METADATA_PLACEHOLDERS = {"string", "null", "none", "n/a", "na", "not provided", "not applicable", "-"}


def _clean_optional_metadata(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return None if not cleaned or cleaned.casefold() in _METADATA_PLACEHOLDERS else cleaned


def create_company(
    session: Session,
    *,
    legal_name: str,
    display_name: str | None,
    registration_number: str | None,
    notes: str | None,
) -> Company:
    company = Company(
        legal_name=legal_name.strip(),
        display_name=display_name.strip() if display_name else None,
        registration_number=registration_number.strip() if registration_number else None,
        notes=notes,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def get_company(session: Session, company_id: UUID) -> Company | None:
    return session.get(Company, company_id)


def list_companies(
    session: Session, *, limit: int, offset: int, search: str | None, include_archived: bool
) -> tuple[list[Company], int]:
    filters = []
    if not include_archived:
        filters.append(Company.is_archived.is_(False))
    if search and search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        filters.append(
            (Company.legal_name.ilike(pattern, escape="\\"))
            | (Company.display_name.ilike(pattern, escape="\\"))
            | (Company.registration_number.ilike(pattern, escape="\\"))
        )
    total = session.scalar(select(func.count()).select_from(Company).where(*filters)) or 0
    items = list(
        session.scalars(
            select(Company)
            .where(*filters)
            .order_by(Company.updated_at.desc(), Company.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return items, total


def update_company(session: Session, company_id: UUID, changes: dict) -> Company:
    company = session.scalar(select(Company).where(Company.id == company_id).with_for_update())
    if company is None:
        raise CompanyNotFoundError
    for field, value in changes.items():
        setattr(company, field, value.strip() if isinstance(value, str) else value)
    company.updated_at = utc_now()
    session.commit()
    session.refresh(company)
    return company


def _company_for_upload(session: Session, company_id: UUID) -> Company:
    company = session.scalar(select(Company).where(Company.id == company_id).with_for_update())
    if company is None:
        raise CompanyNotFoundError
    if company.is_archived:
        raise CompanyArchivedError("Archived companies cannot receive new documents")
    return company


def create_company_document(
    session: Session,
    company_id: UUID,
    *,
    title: str,
    category: CompanyDocumentCategory,
    upload: UploadFile,
    storage: DocumentStorage,
    max_file: int,
    issue_date: date | None = None,
    expiry_date: date | None = None,
    financial_year: str | None = None,
    issuer: str | None = None,
    reference_number: str | None = None,
    notes: str | None = None,
) -> CompanyDocument:
    _validate_dates(issue_date, expiry_date)
    staged: list[Path] = []
    promoted_key: str | None = None
    try:
        _company_for_upload(session, company_id)
        original_name, extension = validate_upload_name(upload.filename)
        path, digest, size, prefix = storage.stage(upload, max_file)
        staged.append(path)
        validate_signature(extension, prefix)
        promoted_key = storage.promote(path, extension)
        staged.remove(path)

        document = CompanyDocument(
            company_id=company_id,
            title=title.strip(),
            category=category,
        )
        document.versions.append(
            CompanyDocumentVersion(
                version_number=1,
                original_filename=original_name,
                storage_key=promoted_key,
                extension=extension,
                content_type=canonical_type(extension),
                size_bytes=size,
                sha256=digest,
                issue_date=issue_date,
                expiry_date=expiry_date,
                financial_year=_clean_optional_metadata(financial_year),
                issuer=_clean_optional_metadata(issuer),
                reference_number=_clean_optional_metadata(reference_number),
                notes=_clean_optional_metadata(notes),
            )
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        return document
    except BaseException:
        session.rollback()
        storage.cleanup_staging(staged)
        if promoted_key:
            try:
                storage.remove(promoted_key)
            except OSError:
                logger.exception("Failed to remove an unregistered company document")
        raise


def list_company_documents(
    session: Session,
    company_id: UUID,
    *,
    limit: int,
    offset: int,
    category: CompanyDocumentCategory | None,
    include_archived: bool,
) -> tuple[list[CompanyDocument], int]:
    filters = [CompanyDocument.company_id == company_id]
    if category is not None:
        filters.append(CompanyDocument.category == category)
    if not include_archived:
        filters.append(CompanyDocument.is_archived.is_(False))
    total = session.scalar(select(func.count()).select_from(CompanyDocument).where(*filters)) or 0
    items = list(
        session.scalars(
            select(CompanyDocument)
            .options(selectinload(CompanyDocument.versions))
            .where(*filters)
            .order_by(CompanyDocument.updated_at.desc(), CompanyDocument.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return items, total


def get_company_document(
    session: Session, company_id: UUID, document_id: UUID
) -> CompanyDocument | None:
    return session.scalar(
        select(CompanyDocument)
        .options(selectinload(CompanyDocument.versions))
        .where(CompanyDocument.id == document_id, CompanyDocument.company_id == company_id)
    )


def update_company_document(
    session: Session, company_id: UUID, document_id: UUID, changes: dict
) -> CompanyDocument:
    document = session.scalar(
        select(CompanyDocument)
        .options(selectinload(CompanyDocument.versions))
        .where(
            CompanyDocument.id == document_id,
            CompanyDocument.company_id == company_id,
        )
        .with_for_update()
    )
    if document is None:
        raise CompanyDocumentNotFoundError
    for field, value in changes.items():
        setattr(document, field, value.strip() if isinstance(value, str) else value)
    document.updated_at = utc_now()
    session.commit()
    session.refresh(document)
    return document


def add_company_document_version(
    session: Session,
    company_id: UUID,
    document_id: UUID,
    *,
    upload: UploadFile,
    storage: DocumentStorage,
    max_file: int,
    issue_date: date | None = None,
    expiry_date: date | None = None,
    financial_year: str | None = None,
    issuer: str | None = None,
    reference_number: str | None = None,
    notes: str | None = None,
) -> CompanyDocumentVersion:
    _validate_dates(issue_date, expiry_date)
    promoted_key: str | None = None
    staged: list[Path] = []
    try:
        _company_for_upload(session, company_id)
        document = session.scalar(
            select(CompanyDocument)
            .where(
                CompanyDocument.id == document_id,
                CompanyDocument.company_id == company_id,
            )
            .with_for_update()
        )
        if document is None:
            raise CompanyDocumentNotFoundError
        if document.is_archived:
            raise ValueError("Archived documents cannot receive new versions")

        original_name, extension = validate_upload_name(upload.filename)
        path, digest, size, prefix = storage.stage(upload, max_file)
        staged.append(path)
        validate_signature(extension, prefix)
        promoted_key = storage.promote(path, extension)
        staged.remove(path)

        current_number = session.scalar(
            select(func.max(CompanyDocumentVersion.version_number)).where(
                CompanyDocumentVersion.document_id == document_id
            )
        ) or 0
        version = CompanyDocumentVersion(
            document_id=document_id,
            version_number=current_number + 1,
            original_filename=original_name,
            storage_key=promoted_key,
            extension=extension,
            content_type=canonical_type(extension),
            size_bytes=size,
            sha256=digest,
            issue_date=issue_date,
            expiry_date=expiry_date,
            financial_year=_clean_optional_metadata(financial_year),
            issuer=_clean_optional_metadata(issuer),
            reference_number=_clean_optional_metadata(reference_number),
            notes=_clean_optional_metadata(notes),
        )
        document.updated_at = utc_now()
        session.add(version)
        session.commit()
        session.refresh(version)
        return version
    except BaseException:
        session.rollback()
        storage.cleanup_staging(staged)
        if promoted_key:
            try:
                storage.remove(promoted_key)
            except OSError:
                logger.exception("Failed to remove unregistered company document version")
        raise


def list_company_document_versions(
    session: Session, company_id: UUID, document_id: UUID
) -> list[CompanyDocumentVersion]:
    document_exists = session.scalar(
        select(CompanyDocument.id).where(
            CompanyDocument.id == document_id,
            CompanyDocument.company_id == company_id,
        )
    )
    if document_exists is None:
        raise CompanyDocumentNotFoundError
    return list(
        session.scalars(
            select(CompanyDocumentVersion)
            .where(CompanyDocumentVersion.document_id == document_id)
            .order_by(CompanyDocumentVersion.version_number.desc())
        ).all()
    )


def update_company_document_version(
    session: Session,
    company_id: UUID,
    document_id: UUID,
    version_id: UUID,
    changes: dict,
) -> CompanyDocumentVersion:
    document = session.scalar(
        select(CompanyDocument)
        .where(CompanyDocument.id == document_id, CompanyDocument.company_id == company_id)
        .with_for_update()
    )
    if document is None:
        raise CompanyDocumentNotFoundError
    version = session.scalar(
        select(CompanyDocumentVersion).where(
            CompanyDocumentVersion.id == version_id,
            CompanyDocumentVersion.document_id == document_id,
        )
    )
    if version is None:
        raise CompanyDocumentVersionNotFoundError
    values = {key: value for key, value in changes.items() if value is not None or key in changes}
    for field, value in values.items():
        if field in {"financial_year", "issuer", "reference_number", "notes"}:
            value = _clean_optional_metadata(value)
        elif isinstance(value, str):
            value = value.strip() or None
        setattr(version, field, value)
    _validate_dates(version.issue_date, version.expiry_date)
    document.updated_at = utc_now()
    session.commit()
    session.refresh(version)
    return version


def get_company_document_version(
    session: Session, company_id: UUID, document_id: UUID, version_id: UUID
) -> CompanyDocumentVersion | None:
    return session.scalar(
        select(CompanyDocumentVersion)
        .join(CompanyDocument, CompanyDocumentVersion.document_id == CompanyDocument.id)
        .where(
            CompanyDocument.company_id == company_id,
            CompanyDocument.id == document_id,
            CompanyDocumentVersion.id == version_id,
        )
    )
