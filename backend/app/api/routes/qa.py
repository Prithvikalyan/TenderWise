import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.retrieval import QARetrievalLog, TenderQAConversation, TenderQAMessage
from app.schemas.qa import (
    QAConversationDetail, QAConversationMessage, QAConversationSummary,
    QARequest, QAResponse, QARetrievalMetrics,
)
from app.services.ai import AIServiceError
from app.services.qa import answer_tender_question, stream_tender_question_events
from app.services.tenders import find_tender
from app.services.retrieval_chunks import CHUNKER_VERSION

router = APIRouter(prefix="/tenders/{tender_id}", tags=["tender Q&A"])
diagnostics_router = APIRouter(prefix="/qa", tags=["Q&A diagnostics"])


def _require_tender(session: Session, tender_id: UUID) -> None:
    if find_tender(session, tender_id) is None:
        raise HTTPException(status_code=404, detail="Tender not found")


@router.post("/qa", response_model=QAResponse)
def ask_tender_question(
    tender_id: UUID,
    payload: QARequest,
    session: Session = Depends(get_db_session),
) -> QAResponse:
    try:
        return answer_tender_question(session, tender_id, payload.question.strip(), payload.conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/qa/stream")
def ask_tender_question_stream(
    tender_id: UUID,
    payload: QARequest,
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    event_iterator = stream_tender_question_events(
        session, tender_id, payload.question.strip(), payload.conversation_id
    )

    def events():
        for event_name, event_data in event_iterator:
            encoded = json.dumps(event_data, ensure_ascii=False, default=str)
            yield f"event: {event_name}\ndata: {encoded}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/qa/conversations", response_model=list[QAConversationSummary])
def list_qa_conversations(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> list[QAConversationSummary]:
    _require_tender(session, tender_id)
    count_subquery = (
        select(TenderQAMessage.conversation_id, func.count(TenderQAMessage.id).label("message_count"))
        .group_by(TenderQAMessage.conversation_id)
        .subquery()
    )
    rows = session.execute(
        select(TenderQAConversation, func.coalesce(count_subquery.c.message_count, 0))
        .outerjoin(count_subquery, count_subquery.c.conversation_id == TenderQAConversation.id)
        .where(TenderQAConversation.tender_id == tender_id)
        .order_by(TenderQAConversation.updated_at.desc())
        .limit(50)
    ).all()
    return [QAConversationSummary(
        id=conversation.id,
        tender_id=conversation.tender_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=count,
    ) for conversation, count in rows]


@router.post("/qa/conversations", response_model=QAConversationSummary, status_code=status.HTTP_201_CREATED)
def create_qa_conversation(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> QAConversationSummary:
    _require_tender(session, tender_id)
    conversation = TenderQAConversation(tender_id=tender_id, title="New conversation")
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return QAConversationSummary(
        id=conversation.id, tender_id=tender_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at, message_count=0,
    )


@router.get("/qa/conversations/{conversation_id}", response_model=QAConversationDetail)
def read_qa_conversation(
    tender_id: UUID,
    conversation_id: UUID,
    session: Session = Depends(get_db_session),
) -> QAConversationDetail:
    _require_tender(session, tender_id)
    conversation = session.scalar(select(TenderQAConversation).where(
        TenderQAConversation.id == conversation_id,
        TenderQAConversation.tender_id == tender_id,
    ))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Q&A conversation not found")
    messages = list(session.scalars(
        select(TenderQAMessage)
        .where(TenderQAMessage.conversation_id == conversation_id, TenderQAMessage.tender_id == tender_id)
        .order_by(TenderQAMessage.created_at.asc(), TenderQAMessage.id.asc())
        .limit(200)
    ).all())
    return QAConversationDetail(
        id=conversation.id, tender_id=tender_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at,
        message_count=len(messages),
        messages=[QAConversationMessage(
            id=message.id, role=message.role, content=message.content,
            response_json=message.response_json, created_at=message.created_at,
        ) for message in messages],
    )


@router.delete("/qa/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_qa_conversation(
    tender_id: UUID,
    conversation_id: UUID,
    session: Session = Depends(get_db_session),
) -> None:
    _require_tender(session, tender_id)
    conversation = session.scalar(select(TenderQAConversation).where(
        TenderQAConversation.id == conversation_id,
        TenderQAConversation.tender_id == tender_id,
    ))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Q&A conversation not found")
    session.delete(conversation)
    session.commit()


@router.get("/qa/metrics", response_model=QARetrievalMetrics)
def read_qa_metrics(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> QARetrievalMetrics:
    _require_tender(session, tender_id)
    since = datetime.now(UTC) - timedelta(days=30)
    rows = list(session.scalars(select(QARetrievalLog).where(
        QARetrievalLog.tender_id == tender_id,
        QARetrievalLog.created_at >= since,
    )).all())
    total = len(rows)
    return QARetrievalMetrics(
        total_questions=total,
        evidence_found_count=sum(row.evidence_found for row in rows),
        no_evidence_count=sum(not row.evidence_found for row in rows),
        hybrid_count=sum(row.retrieval_mode == "hybrid" for row in rows),
        keyword_fallback_count=sum(row.retrieval_mode == "keyword_fallback" for row in rows),
        average_retrieval_ms=(sum(row.retrieval_ms for row in rows) / total) if total else 0.0,
        average_rerank_ms=(sum(row.rerank_ms for row in rows) / total) if total else 0.0,
        average_generation_ms=(sum(row.generation_ms for row in rows) / total) if total else 0.0,
        average_embedding_coverage=(sum(row.embedding_coverage for row in rows) / total) if total else 0.0,
        citation_needs_review_count=sum(row.citation_status == "needs_review" for row in rows),
    )


@diagnostics_router.get("/retrieval-status")
def read_retrieval_status(session: Session = Depends(get_db_session)) -> dict:
    settings = get_settings()
    pgvector_available = False
    pgvector_version = None
    database_dialect = session.get_bind().dialect.name
    if database_dialect == "postgresql":
        try:
            row = session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).first()
            if row:
                pgvector_available = True
                pgvector_version = row[0]
        except SQLAlchemyError:
            session.rollback()
    return {
        "database_dialect": database_dialect,
        "pgvector_available": pgvector_available,
        "pgvector_version": pgvector_version,
        "semantic_retrieval_enabled": bool(settings.tenderwise_semantic_retrieval_enabled),
        "embedding_provider_configured": bool(settings.nvidia_api_key),
        "embedding_model": settings.nvidia_embedding_model,
        "embedding_dimensions": 2048,
        "dense_retrieval_ready": bool(
            database_dialect == "postgresql" and pgvector_available
            and settings.tenderwise_semantic_retrieval_enabled and settings.nvidia_api_key
        ),
        "reranker_enabled": bool(settings.tenderwise_reranker_enabled),
        "reranker_configured": bool(settings.nvidia_api_key and settings.tenderwise_reranker_url),
        "reranker_model": settings.tenderwise_reranker_model,
        "hnsw_ef_search": int(settings.tenderwise_qa_hnsw_ef_search),
        "candidate_k": int(settings.tenderwise_qa_candidate_k),
        "rrf_k": int(settings.tenderwise_qa_rrf_k),
        "chunker_version": CHUNKER_VERSION,
        "chunk_target_tokens": int(settings.tenderwise_chunk_target_tokens),
    }
