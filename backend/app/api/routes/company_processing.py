from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.models.company import Company
from app.schemas.company_processing import (
    CompanyExtractionUnitListResponse,
    CompanyExtractionUnitResponse,
    CompanyProcessingRunCounts,
    CompanyProcessingRunCreate,
    CompanyProcessingRunCreatedResponse,
    CompanyProcessingRunDetailResponse,
    CompanyProcessingRunItemResponse,
    CompanyProcessingRunListResponse,
    CompanyProcessingRunResponse,
)
from app.services import company_processing as processing_service
from app.services.processing import InvalidRunStateError

router = APIRouter(prefix="/companies/{company_id}", tags=["company document processing"])
MAX_PROCESSING_RUN_PAGE = 1_000_000


def _require_company(session: Session, company_id: UUID) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _items_response(session: Session, items) -> list[CompanyProcessingRunItemResponse]:
    counts = processing_service.get_company_run_item_unit_counts(
        session, [item.id for item in items]
    )
    return [
        CompanyProcessingRunItemResponse(
            id=item.id,
            company_document_version_id=item.company_document_version_id,
            status=item.status,
            outcome_code=item.outcome_code,
            failure_class=item.failure_class,
            attempt_count=item.attempt_count,
            max_attempts=item.max_attempts,
            safe_message=item.safe_message,
            extraction_unit_count=counts.get(item.id, 0),
            created_at=item.created_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
        )
        for item in items
    ]


@router.post(
    "/processing-runs",
    response_model=CompanyProcessingRunCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_company_processing_run(
    company_id: UUID,
    payload: CompanyProcessingRunCreate,
    session: Session = Depends(get_db_session),
) -> CompanyProcessingRunCreatedResponse:
    try:
        run = processing_service.create_company_processing_run(
            session, company_id, payload.version_ids
        )
    except processing_service.CompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc
    except processing_service.CompanyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Company or document version not found") from exc
    except processing_service.CompanyArchivedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="Company processing request failed") from exc

    return CompanyProcessingRunCreatedResponse(
        **CompanyProcessingRunResponse.model_validate(run).model_dump(),
        items=_items_response(session, list(run.items)),
    )


@router.get("/processing-runs", response_model=CompanyProcessingRunListResponse)
def list_company_processing_runs(
    company_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PROCESSING_RUN_PAGE),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> CompanyProcessingRunListResponse:
    _require_company(session, company_id)
    runs, total = processing_service.list_company_processing_runs(
        session, company_id, page=page, limit=limit
    )
    return CompanyProcessingRunListResponse(
        items=[CompanyProcessingRunResponse.model_validate(run) for run in runs],
        total=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )


@router.get(
    "/processing-runs/{run_id}",
    response_model=CompanyProcessingRunDetailResponse,
)
def read_company_processing_run(
    company_id: UUID,
    run_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PROCESSING_RUN_PAGE),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> CompanyProcessingRunDetailResponse:
    _require_company(session, company_id)
    try:
        result = processing_service.get_company_processing_run(
            session, company_id, run_id, page=page, limit=limit
        )
    except InvalidRunStateError as exc:
        raise HTTPException(status_code=409, detail="Processing run state is inconsistent") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Company processing run not found")
    run, aggregated_status, items, total, counts = result
    base = CompanyProcessingRunResponse.model_validate(run).model_dump()
    base["status"] = aggregated_status
    return CompanyProcessingRunDetailResponse(
        **base,
        item_counts=CompanyProcessingRunCounts(
            pending=counts["pending"],
            processing=counts["processing"],
            completed=counts["completed"],
            failed=counts["failed"],
        ),
        items=_items_response(session, items),
        total_items=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/extraction-units",
    response_model=CompanyExtractionUnitListResponse,
)
def list_company_extraction_units(
    company_id: UUID,
    document_id: UUID,
    version_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PROCESSING_RUN_PAGE),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> CompanyExtractionUnitListResponse:
    _require_company(session, company_id)
    result = processing_service.list_company_version_extraction_units(
        session, company_id, document_id, version_id, page=page, limit=limit
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Company document version or completed extraction not found",
        )
    run_id, units, total = result
    return CompanyExtractionUnitListResponse(
        run_id=run_id,
        company_document_id=document_id,
        company_document_version_id=version_id,
        items=[CompanyExtractionUnitResponse.model_validate(unit) for unit in units],
        total=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )
