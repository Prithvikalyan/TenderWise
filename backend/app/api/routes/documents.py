import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.tender_document import DocumentCategory
from app.schemas.document import DocumentCategoryUpdate, DocumentListResponse, DocumentResponse
from app.services import documents as document_service
from app.services.document_storage import DocumentStorage, StorageError
from app.services.tenders import find_tender

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tenders/{tender_id}/documents", tags=["documents"])


def get_storage(session: Session = Depends(get_db_session)) -> DocumentStorage:
    try:
        storage = DocumentStorage()
        storage.recover_pending_deletions(
            sessionmaker(bind=session.get_bind(), class_=Session, autoflush=False, autocommit=False)
        )
        return storage
    except OSError as exc:
        logger.exception("Document storage initialization failed")
        raise HTTPException(status_code=500, detail="Document storage is unavailable") from exc


def require_tender(session: Session, tender_id: UUID) -> None:
    if find_tender(session, tender_id) is None:
        raise HTTPException(status_code=404, detail="Tender not found")


def find_document(session: Session, tender_id: UUID, doc_id: UUID):
    document = document_service.get_document(session, tender_id, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.post("", response_model=list[DocumentResponse], status_code=status.HTTP_201_CREATED)
def upload_documents(
    tender_id: UUID,
    files: list[UploadFile] = File(
        ...,
        json_schema_extra={"items": {"type": "string", "format": "binary"}},
    ),
    categories: list[str] = Form(...),
    session: Session = Depends(get_db_session),
    storage: DocumentStorage = Depends(get_storage),
) -> list[DocumentResponse]:
    require_tender(session, tender_id)
    try:
        records = document_service.upload_documents(session, tender_id, files, categories, storage, get_settings().tenderwise_max_file_size_bytes, get_settings().tenderwise_max_batch_size_bytes)
        return [DocumentResponse.model_validate(record) for record in records]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except document_service.TenderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tender not found") from exc
    except LookupError as exc:
        raise HTTPException(status_code=422, detail="Invalid document category") from exc
    except TypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail="Upload size limit exceeded") from exc
    except (OSError, StorageError, SQLAlchemyError) as exc:
        logger.exception("Document upload failed")
        raise HTTPException(status_code=500, detail="Document storage operation failed") from exc


@router.get("", response_model=DocumentListResponse)
def list_documents(
    tender_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: DocumentCategory | None = Query(None),
    session: Session = Depends(get_db_session),
) -> DocumentListResponse:
    require_tender(session, tender_id)
    items, total = document_service.list_documents(session, tender_id, limit=limit, offset=offset, category=category)
    return DocumentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.patch("/{doc_id}", response_model=DocumentResponse)
def update_document_category(
    tender_id: UUID,
    doc_id: UUID,
    payload: DocumentCategoryUpdate,
    session: Session = Depends(get_db_session),
) -> DocumentResponse:
    require_tender(session, tender_id)
    document = find_document(session, tender_id, doc_id)
    document.category = payload.category
    try:
        session.commit()
        session.refresh(document)
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception("Document category update failed")
        raise HTTPException(status_code=500, detail="Document update failed") from exc
    return DocumentResponse.model_validate(document)


@router.get("/{doc_id}/download")
def download_document(
    tender_id: UUID,
    doc_id: UUID,
    session: Session = Depends(get_db_session),
    storage: DocumentStorage = Depends(get_storage),
) -> FileResponse:
    require_tender(session, tender_id)
    document = find_document(session, tender_id, doc_id)
    try:
        path = storage.resolve_key(document.storage_key)
        if not path.is_file():
            raise FileNotFoundError
    except (OSError, StorageError) as exc:
        raise HTTPException(status_code=404, detail="Document file not found") from exc
    safe_name = document.original_filename.replace("\r", "").replace("\n", "").replace('"', "_")
    return FileResponse(path, media_type=document.content_type, filename=safe_name, headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    tender_id: UUID,
    doc_id: UUID,
    session: Session = Depends(get_db_session),
    storage: DocumentStorage = Depends(get_storage),
) -> Response:
    require_tender(session, tender_id)
    document = find_document(session, tender_id, doc_id)
    try:
        document_service.delete_document(session, document, storage)
    except LookupError as exc:
        # The row may have disappeared after the initial lookup but before the
        # deletion service acquired its document lock (for example, a race with
        # another delete request).
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except (OSError, StorageError) as exc:
        logger.exception("Document deletion failed")
        raise HTTPException(status_code=500, detail="Document deletion failed") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="Document deletion failed") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
