"""Read and update persistent manual pre-submission verification checklists."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.verification import VerificationChecklistResponse, VerificationItemUpdate
from app.services.verification import (
    get_latest_verification_checklist,
    get_verification_checklist,
    update_verification_item,
)

router = APIRouter(prefix="/tenders/{tender_id}/verification-checklists", tags=["pre-submission verification"])


@router.get("/latest", response_model=VerificationChecklistResponse | None)
def read_latest_checklist(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
):
    return get_latest_verification_checklist(session, tender_id)


@router.get("/{checklist_id}", response_model=VerificationChecklistResponse)
def read_checklist(
    tender_id: UUID,
    checklist_id: UUID,
    session: Session = Depends(get_db_session),
):
    try:
        return get_verification_checklist(session, tender_id, checklist_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{checklist_id}/items/{item_id}", response_model=VerificationChecklistResponse)
def patch_checklist_item(
    tender_id: UUID,
    checklist_id: UUID,
    item_id: UUID,
    payload: VerificationItemUpdate,
    session: Session = Depends(get_db_session),
):
    try:
        return update_verification_item(
            session,
            tender_id=tender_id,
            checklist_id=checklist_id,
            item_id=item_id,
            verification_status=payload.verification_status,
            verification_notes=payload.verification_notes,
            reviewer_name=payload.reviewer_name,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
