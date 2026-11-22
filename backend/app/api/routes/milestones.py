from datetime import date, UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.models.milestone import TenderMilestone
from app.models.tender import Tender
from app.schemas.milestone import MilestoneCreate, MilestoneListResponse, MilestoneResponse, MilestoneUpdate

router = APIRouter(tags=["tender milestones"])


def _response(milestone: TenderMilestone, tender: Tender) -> MilestoneResponse:
    return MilestoneResponse(
        id=milestone.id,
        tender_id=milestone.tender_id,
        tender_name=tender.name,
        tender_reference=tender.reference_number,
        title=milestone.title,
        category=str(milestone.category.value if hasattr(milestone.category, "value") else milestone.category),
        due_date=milestone.due_date,
        notes=milestone.notes,
        source_reference=milestone.source_reference,
        is_completed=milestone.is_completed,
        created_at=milestone.created_at,
        updated_at=milestone.updated_at,
    )


@router.get("/milestones", response_model=MilestoneListResponse)
def list_milestones(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    include_completed: bool = Query(default=True),
    limit: int = Query(default=250, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> MilestoneListResponse:
    if from_date and to_date and to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date must be on or after from_date")

    filters = []
    if from_date:
        filters.append(TenderMilestone.due_date >= from_date)
    if to_date:
        filters.append(TenderMilestone.due_date <= to_date)
    if not include_completed:
        filters.append(TenderMilestone.is_completed.is_(False))

    total = session.scalar(select(func.count()).select_from(TenderMilestone).where(*filters)) or 0
    rows = session.execute(
        select(TenderMilestone, Tender)
        .join(Tender, Tender.id == TenderMilestone.tender_id)
        .where(*filters)
        .order_by(TenderMilestone.due_date.asc(), TenderMilestone.created_at.asc())
        .limit(limit)
    ).all()
    return MilestoneListResponse(items=[_response(milestone, tender) for milestone, tender in rows], total=total)


@router.post(
    "/tenders/{tender_id}/milestones",
    response_model=MilestoneResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_milestone(
    tender_id: UUID,
    payload: MilestoneCreate,
    session: Session = Depends(get_db_session),
) -> MilestoneResponse:
    tender = session.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    milestone = TenderMilestone(tender_id=tender_id, **payload.model_dump())
    session.add(milestone)
    try:
        session.commit()
        session.refresh(milestone)
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="Milestone could not be saved") from exc
    return _response(milestone, tender)


@router.patch("/milestones/{milestone_id}", response_model=MilestoneResponse)
def update_milestone(
    milestone_id: UUID,
    payload: MilestoneUpdate,
    session: Session = Depends(get_db_session),
) -> MilestoneResponse:
    milestone = session.get(TenderMilestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    tender = session.get(Tender, milestone.tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"] is not None:
        changes["title"] = changes["title"].strip()
    for field, value in changes.items():
        setattr(milestone, field, value)
    milestone.updated_at = datetime.now(UTC)
    try:
        session.commit()
        session.refresh(milestone)
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="Milestone could not be updated") from exc
    return _response(milestone, tender)


@router.delete("/milestones/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_milestone(milestone_id: UUID, session: Session = Depends(get_db_session)) -> None:
    milestone = session.get(TenderMilestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    try:
        session.delete(milestone)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="Milestone could not be deleted") from exc
