import logging
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.company import CompanyDocumentCategory
from app.schemas.company import (
    CompanyCreate,
    CompanyDocumentListResponse,
    CompanyDocumentResponse,
    CompanyDocumentUpdate,
    CompanyDocumentVersionListResponse,
    CompanyDocumentVersionMetadataUpdate,
    CompanyDocumentVersionResponse,
    CompanyListResponse,
    CompanyResponse,
    CompanyUpdate,
)
from app.services import company_documents as company_service
from app.services.document_storage import DocumentStorage, StorageError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["companies"])


def get_company_storage() -> DocumentStorage:
    try:
        return DocumentStorage()
    except OSError as exc:
        logger.exception("Company document storage initialization failed")
        raise HTTPException(status_code=500, detail="Document storage is unavailable") from exc


def _company_or_404(session: Session, company_id: UUID):
    company = company_service.get_company(session, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _translate_upload_error(exc: Exception) -> HTTPException:
    if isinstance(exc, company_service.CompanyNotFoundError):
        return HTTPException(status_code=404, detail="Company not found")
    if isinstance(exc, company_service.CompanyArchivedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, company_service.CompanyDocumentNotFoundError):
        return HTTPException(status_code=404, detail="Company document not found")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, TypeError):
        return HTTPException(status_code=415, detail=str(exc))
    if isinstance(exc, OverflowError):
        return HTTPException(status_code=413, detail="Upload size limit exceeded")
    if isinstance(exc, (OSError, StorageError, SQLAlchemyError)):
        logger.exception("Company document operation failed")
        return HTTPException(status_code=500, detail="Company document operation failed")
    raise exc


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyCreate,
    session: Session = Depends(get_db_session),
) -> CompanyResponse:
    try:
        company = company_service.create_company(session, **payload.model_dump())
        return CompanyResponse.model_validate(company)
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="Company creation failed") from exc


@router.get("", response_model=CompanyListResponse)
def list_companies(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=255),
    include_archived: bool = Query(False),
    session: Session = Depends(get_db_session),
) -> CompanyListResponse:
    items, total = company_service.list_companies(
        session, limit=limit, offset=offset, search=search, include_archived=include_archived
    )
    return CompanyListResponse(
        items=[CompanyResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{company_id}", response_model=CompanyResponse)
def read_company(
    company_id: UUID,
    session: Session = Depends(get_db_session),
) -> CompanyResponse:
    return CompanyResponse.model_validate(_company_or_404(session, company_id))


@router.patch("/{company_id}", response_model=CompanyResponse)
def patch_company(
    company_id: UUID,
    payload: CompanyUpdate,
    session: Session = Depends(get_db_session),
) -> CompanyResponse:
    changes = payload.model_dump(exclude_unset=True)
    if "legal_name" in changes and changes["legal_name"] is None:
        raise HTTPException(status_code=422, detail="Legal name cannot be null")
    try:
        company = company_service.update_company(session, company_id, changes)
        return CompanyResponse.model_validate(company)
    except company_service.CompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="Company update failed") from exc


@router.post(
    "/{company_id}/documents",
    response_model=CompanyDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_company_document(
    company_id: UUID,
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=255),
    category: CompanyDocumentCategory = Form(...),
    issue_date: date | None = Form(None),
    expiry_date: date | None = Form(None),
    financial_year: str | None = Form(None, max_length=20),
    issuer: str | None = Form(None, max_length=255),
    reference_number: str | None = Form(None, max_length=255),
    notes: str | None = Form(None, max_length=4000),
    session: Session = Depends(get_db_session),
    storage: DocumentStorage = Depends(get_company_storage),
) -> CompanyDocumentResponse:
    try:
        document = company_service.create_company_document(
            session,
            company_id,
            title=title,
            category=category,
            upload=file,
            storage=storage,
            max_file=get_settings().tenderwise_max_file_size_bytes,
            issue_date=issue_date,
            expiry_date=expiry_date,
            financial_year=financial_year,
            issuer=issuer,
            reference_number=reference_number,
            notes=notes,
        )
        return CompanyDocumentResponse.model_validate(document)
    except (ValueError, TypeError, OverflowError, LookupError, OSError, StorageError, SQLAlchemyError) as exc:
        session.rollback()
        raise _translate_upload_error(exc) from exc
    finally:
        file.file.close()


@router.get("/{company_id}/documents", response_model=CompanyDocumentListResponse)
def list_company_documents(
    company_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: CompanyDocumentCategory | None = Query(None),
    include_archived: bool = Query(False),
    session: Session = Depends(get_db_session),
) -> CompanyDocumentListResponse:
    _company_or_404(session, company_id)
    items, total = company_service.list_company_documents(
        session,
        company_id,
        limit=limit,
        offset=offset,
        category=category,
        include_archived=include_archived,
    )
    return CompanyDocumentListResponse(
        items=[CompanyDocumentResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{company_id}/documents/{document_id}", response_model=CompanyDocumentResponse)
def patch_company_document(
    company_id: UUID,
    document_id: UUID,
    payload: CompanyDocumentUpdate,
    session: Session = Depends(get_db_session),
) -> CompanyDocumentResponse:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one field must be provided")
    if "title" in changes and changes["title"] is None:
        raise HTTPException(status_code=422, detail="Document title cannot be null")
    try:
        document = company_service.update_company_document(session, company_id, document_id, changes)
        return CompanyDocumentResponse.model_validate(document)
    except company_service.CompanyDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company document not found") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="Company document update failed") from exc


@router.post(
    "/{company_id}/documents/{document_id}/versions",
    response_model=CompanyDocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_company_document_version(
    company_id: UUID,
    document_id: UUID,
    file: UploadFile = File(...),
    issue_date: date | None = Form(None),
    expiry_date: date | None = Form(None),
    financial_year: str | None = Form(None, max_length=20),
    issuer: str | None = Form(None, max_length=255),
    reference_number: str | None = Form(None, max_length=255),
    notes: str | None = Form(None, max_length=4000),
    session: Session = Depends(get_db_session),
    storage: DocumentStorage = Depends(get_company_storage),
) -> CompanyDocumentVersionResponse:
    try:
        version = company_service.add_company_document_version(
            session,
            company_id,
            document_id,
            upload=file,
            storage=storage,
            max_file=get_settings().tenderwise_max_file_size_bytes,
            issue_date=issue_date,
            expiry_date=expiry_date,
            financial_year=financial_year,
            issuer=issuer,
            reference_number=reference_number,
            notes=notes,
        )
        return CompanyDocumentVersionResponse.model_validate(version)
    except (ValueError, TypeError, OverflowError, LookupError, OSError, StorageError, SQLAlchemyError) as exc:
        session.rollback()
        raise _translate_upload_error(exc) from exc
    finally:
        file.file.close()


@router.get(
    "/{company_id}/documents/{document_id}/versions",
    response_model=CompanyDocumentVersionListResponse,
)
def list_company_document_versions(
    company_id: UUID,
    document_id: UUID,
    session: Session = Depends(get_db_session),
) -> CompanyDocumentVersionListResponse:
    _company_or_404(session, company_id)
    try:
        items = company_service.list_company_document_versions(session, company_id, document_id)
        return CompanyDocumentVersionListResponse(
            items=[CompanyDocumentVersionResponse.model_validate(item) for item in items],
            total=len(items),
        )
    except company_service.CompanyDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company document not found") from exc


@router.patch(
    "/{company_id}/documents/{document_id}/versions/{version_id}",
    response_model=CompanyDocumentVersionResponse,
)
def patch_company_document_version(
    company_id: UUID,
    document_id: UUID,
    version_id: UUID,
    payload: CompanyDocumentVersionMetadataUpdate,
    session: Session = Depends(get_db_session),
) -> CompanyDocumentVersionResponse:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one metadata field must be provided")
    try:
        version = company_service.update_company_document_version(
            session, company_id, document_id, version_id, changes
        )
        return CompanyDocumentVersionResponse.model_validate(version)
    except company_service.CompanyDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company document not found") from exc
    except company_service.CompanyDocumentVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company document version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="Company document metadata update failed") from exc


@router.get(
    "/{company_id}/documents/{document_id}/versions/{version_id}/download",
)
def download_company_document_version(
    company_id: UUID,
    document_id: UUID,
    version_id: UUID,
    session: Session = Depends(get_db_session),
    storage: DocumentStorage = Depends(get_company_storage),
):
    _company_or_404(session, company_id)
    version = company_service.get_company_document_version(
        session, company_id, document_id, version_id
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Company document version not found")
    try:
        path = storage.resolve_key(version.storage_key)
        if not path.is_file():
            raise FileNotFoundError
    except (OSError, StorageError) as exc:
        raise HTTPException(status_code=404, detail="Company document file not found") from exc
    safe_name = version.original_filename.replace("\r", "").replace("\n", "").replace('"', "_")
    return FileResponse(
        path,
        media_type=version.content_type,
        filename=safe_name,
        headers={"X-Content-Type-Options": "nosniff"},
    )
