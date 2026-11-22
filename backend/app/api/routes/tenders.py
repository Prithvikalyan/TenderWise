from uuid import UUID
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.models.tender import Tender, TenderStatus
from app.schemas.tender import (
    TenderCreate,
    TenderDetailResponse,
    TenderListResponse,
    TenderResponse,
    TenderUpdate,
)
from app.services import tenders as tender_service
from app.services.document_storage import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenders", tags=["tenders"])


def get_existing_tender(session: Session, tender_id: UUID) -> Tender:
    tender = tender_service.find_tender(session, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender


@router.post("", response_model=TenderResponse, status_code=status.HTTP_201_CREATED)
def create_tender(
    payload: TenderCreate,
    session: Session = Depends(get_db_session),
) -> TenderResponse:
    return tender_service.create_tender(session, payload)


@router.get("", response_model=TenderListResponse)
def list_tenders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    status_filter: TenderStatus | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> TenderListResponse:
    items, total = tender_service.list_tenders(
        session,
        limit=limit,
        offset=offset,
        search=search,
        status=status_filter,
    )
    return TenderListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{tender_id}", response_model=TenderDetailResponse)
def read_tender(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> TenderDetailResponse:
    result = tender_service.get_tender(session, tender_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    tender, document_count = result
    response_data = TenderResponse.model_validate(tender).model_dump()
    response_data["document_count"] = document_count
    return TenderDetailResponse.model_validate(response_data)


@router.patch("/{tender_id}", response_model=TenderResponse)
def update_tender(
    tender_id: UUID,
    payload: TenderUpdate,
    session: Session = Depends(get_db_session),
) -> TenderResponse:
    tender = get_existing_tender(session, tender_id)
    return tender_service.update_tender(session, tender, payload)


@router.delete("/{tender_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tender(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> Response:
    tender = get_existing_tender(session, tender_id)
    try:
        tender_service.delete_tender(session, tender)
    except (StorageError, OSError) as exc:
        logger.exception("Tender deletion storage cleanup failed")
        raise HTTPException(status_code=500, detail="Tender deletion cleanup failed") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
