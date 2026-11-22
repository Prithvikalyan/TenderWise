from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.processing import (
    ProcessingDocumentHistoryEntry,
    ProcessingDocumentHistoryResponse,
    ProcessingRunCounts,
    ProcessingRunCreate,
    ProcessingRunCreatedResponse,
    ProcessingRunDetailResponse,
    ProcessingRunItemResponse,
    ProcessingRunListResponse,
    ProcessingRunResponse,
    ExtractionPreviewUnit,
    ProcessingItemExtractionResponse,
)
from app.services import processing as processing_service
from app.services.tenders import find_tender
from app.services.qa import _citation_label

router = APIRouter(prefix="/tenders/{tender_id}", tags=["document processing"])
# At the maximum limit of 100 this caps offsets at 99,999,900, below int32 max.
MAX_PROCESSING_RUN_PAGE = 1_000_000


def require_tender(session: Session, tender_id: UUID) -> None:
    if find_tender(session, tender_id) is None:
        raise HTTPException(status_code=404, detail="Tender not found")


@router.post(
    "/processing-runs",
    response_model=ProcessingRunCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_processing_run(
    tender_id: UUID,
    payload: ProcessingRunCreate,
    session: Session = Depends(get_db_session),
) -> ProcessingRunCreatedResponse:
    try:
        run = processing_service.create_processing_run(session, tender_id, payload.document_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        # Use the same response for absent tenders and missing/out-of-scope
        # documents so document existence is not disclosed.
        raise HTTPException(status_code=404, detail="Tender or document not found") from exc
    return ProcessingRunCreatedResponse.model_validate(run)


@router.get("/processing-runs", response_model=ProcessingRunListResponse)
def list_processing_runs(
    tender_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PROCESSING_RUN_PAGE),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> ProcessingRunListResponse:
    require_tender(session, tender_id)
    runs, total = processing_service.list_processing_runs(session, tender_id, page=page, limit=limit)
    return ProcessingRunListResponse(
        items=[ProcessingRunResponse.model_validate(run) for run in runs],
        total=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )


@router.get("/processing-runs/{run_id}", response_model=ProcessingRunDetailResponse)
def read_processing_run(
    tender_id: UUID,
    run_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PROCESSING_RUN_PAGE),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> ProcessingRunDetailResponse:
    require_tender(session, tender_id)
    try:
        result = processing_service.get_processing_run(
            session, tender_id, run_id, page=page, limit=limit
        )
    except processing_service.InvalidRunStateError as exc:
        raise HTTPException(status_code=409, detail="Processing run state is inconsistent") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Processing run not found")
    run, aggregated_status, items, total, counts = result
    run_response = ProcessingRunResponse.model_validate(run).model_dump()
    run_response["status"] = aggregated_status
    return ProcessingRunDetailResponse(
        **run_response,
        item_counts=ProcessingRunCounts(
            pending=counts["pending"],
            processing=counts["processing"],
            completed=counts["completed"],
            failed=counts["failed"],
        ),
        items=[ProcessingRunItemResponse.model_validate(item) for item in items],
        total_items=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )


@router.get(
    "/processing-runs/{run_id}/items/{item_id}/extraction",
    response_model=ProcessingItemExtractionResponse,
)
def read_processing_item_extraction(
    tender_id: UUID,
    run_id: UUID,
    item_id: UUID,
    session: Session = Depends(get_db_session),
) -> ProcessingItemExtractionResponse:
    require_tender(session, tender_id)
    result = processing_service.get_processing_item_extraction(
        session, tender_id, run_id, item_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Processed document output not found")

    run, item, filename, all_units = result
    # Keep the interactive preview useful and bounded for large files. The
    # exact count and truncation flag tell the user when more units exist.
    preview_units = all_units[:40]
    formatted_units = []
    has_extracted_text = False
    total_chars = 0
    character_limit_reached = False
    for unit in preview_units:
        text = processing_service.format_extraction_unit_preview(unit)
        if not text:
            continue
        has_extracted_text = True
        remaining = max(0, 12000 - total_chars)
        if remaining <= 0:
            character_limit_reached = True
            break
        content = text[:min(1800, remaining)]
        total_chars += len(content)
        if len(content) < len(text):
            character_limit_reached = True
        formatted_units.append(
            ExtractionPreviewUnit(
                unit_index=unit.unit_index,
                unit_type=unit.unit_type,
                citation=_citation_label(filename, unit.source_ref or {}),
                content_text=content,
            )
        )

    return ProcessingItemExtractionResponse(
        run_id=run.id,
        item_id=item.id,
        document_id=item.document_id,
        filename=filename,
        item_status=item.status,
        outcome_code=item.outcome_code,
        extraction_unit_count=len(all_units),
        has_extracted_text=has_extracted_text,
        units_truncated=(len(all_units) > len(preview_units)) or character_limit_reached,
        units=formatted_units,
    )


@router.get(
    "/documents/{document_id}/processing-runs",
    response_model=ProcessingDocumentHistoryResponse,
)
def list_document_processing_history(
    tender_id: UUID,
    document_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PROCESSING_RUN_PAGE),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> ProcessingDocumentHistoryResponse:
    require_tender(session, tender_id)
    result = processing_service.list_document_processing_history(
        session, tender_id, document_id, page=page, limit=limit
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    rows, total = result
    entries = [
        ProcessingDocumentHistoryEntry(
            run_id=run.id,
            run_status=run.status,
            requested_document_count=run.requested_document_count,
            run_created_at=run.created_at,
            run_started_at=run.started_at,
            run_completed_at=run.completed_at,
            run_updated_at=run.updated_at,
            item_id=item.id,
            document_id=document_id,
            item_status=item.status,
            outcome_code=item.outcome_code,
            failure_class=item.failure_class,
            attempt_count=item.attempt_count,
            max_attempts=item.max_attempts,
            safe_message=item.safe_message,
            item_created_at=item.created_at,
            item_started_at=item.started_at,
            item_completed_at=item.completed_at,
        )
        for run, item in rows
    ]
    return ProcessingDocumentHistoryResponse(
        items=entries,
        total=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )
