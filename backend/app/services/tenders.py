from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.tender import Tender, TenderStatus, utc_now
from app.models.tender_document import TenderDocument
from app.schemas.tender import TenderCreate, TenderUpdate
from app.services.document_storage import DocumentStorage
from app.services.source_deletion import delete_tender_sources


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise


def create_tender(session: Session, data: TenderCreate) -> Tender:
    tender = Tender(**data.model_dump())
    session.add(tender)
    _commit(session)
    session.refresh(tender)
    return tender


def find_tender(session: Session, tender_id: UUID) -> Tender | None:
    return session.get(Tender, tender_id)


def list_tenders(
    session: Session,
    *,
    limit: int,
    offset: int,
    search: str | None,
    status: TenderStatus | None,
) -> tuple[list[Tender], int]:
    filters = []
    if search and search.strip():
        escaped_search = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_search}%"
        filters.append(
            or_(
                Tender.name.ilike(pattern, escape="\\"),
                Tender.reference_number.ilike(pattern, escape="\\"),
                Tender.issuing_organization.ilike(pattern, escape="\\"),
            )
        )
    if status is not None:
        filters.append(Tender.status == status)

    total = session.scalar(select(func.count()).select_from(Tender).where(*filters)) or 0
    items = list(
        session.scalars(
            select(Tender)
            .where(*filters)
            .order_by(Tender.updated_at.desc(), Tender.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return items, total


def get_tender(session: Session, tender_id: UUID) -> tuple[Tender, int] | None:
    tender = find_tender(session, tender_id)
    if tender is None:
        return None
    document_count = session.scalar(
        select(func.count()).select_from(TenderDocument).where(TenderDocument.tender_id == tender_id)
    ) or 0
    return tender, document_count


def update_tender(session: Session, tender: Tender, data: TenderUpdate) -> Tender:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(tender, field, value)
    tender.updated_at = utc_now()
    _commit(session)
    session.refresh(tender)
    return tender


def delete_tender(session: Session, tender: Tender) -> None:
    delete_tender_sources(session, tender.id, storage_factory=DocumentStorage)
