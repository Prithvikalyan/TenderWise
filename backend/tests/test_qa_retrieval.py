from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.document_processing import (
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
)
from app.models.retrieval import DocumentRetrievalChunkEmbedding
from app.models.tender import Tender, TenderStatus
from app.models.tender_document import DocumentCategory, TenderDocument
from app.services import qa, advanced_retrieval


@pytest.fixture
def qa_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _add_unit(session: Session, tender: Tender, filename: str, content: str) -> DocumentExtractionUnit:
    document = TenderDocument(
        tender_id=tender.id,
        original_filename=filename,
        storage_key=f"{uuid4().hex}.pdf",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=100,
        category=DocumentCategory.MAIN_TENDER,
    )
    session.add(document)
    session.flush()
    run = DocumentProcessingRun(
        tender_id=tender.id,
        company_id=None,
        requested_document_count=1,
        status="completed",
    )
    session.add(run)
    session.flush()
    run_item = DocumentProcessingRunItem(
        run_id=run.id,
        document_id=document.id,
        status="completed",
    )
    session.add(run_item)
    session.flush()
    unit = DocumentExtractionUnit(
        run_item_id=run_item.id,
        unit_index=0,
        unit_type="text",
        source_ref={"page": 1, "block_index": 0},
        content_text=content,
        metadata_json={"format": "pdf"},
    )
    session.add(unit)
    session.flush()
    return unit


def _semantic_settings():
    return SimpleNamespace(
        tenderwise_semantic_retrieval_enabled=True,
        tenderwise_reranker_enabled=False,
        nvidia_api_key="test-key",
        nvidia_embedding_model="nvidia/nemotron-3-embed-1b",
        tenderwise_chunk_target_tokens=600,
        tenderwise_chunk_overlap_tokens=80,
        tenderwise_qa_max_vector_backfill_per_query=256,
        tenderwise_qa_rrf_k=60,
    )


def test_hybrid_retrieval_uses_structure_chunks_pgvector_cache_and_independent_dense_candidates(qa_session, monkeypatch) -> None:
    tender = Tender(name="Demo Tender", status=TenderStatus.ACTIVE)
    qa_session.add(tender)
    qa_session.flush()
    relevant = _add_unit(
        qa_session,
        tender,
        "tender_security.pdf",
        "Earnest money deposit EMD is INR 80,000 and shall be paid using a banker's cheque or demand draft valid for 90 days.",
    )
    _add_unit(
        qa_session,
        tender,
        "unrelated_scope.pdf",
        "The bidder shall supply and install interactive display equipment with warranty and onsite support.",
    )
    qa_session.commit()

    monkeypatch.setattr(advanced_retrieval, "get_settings", _semantic_settings)
    calls = []
    positive = [1.0] + [0.0] * 2047
    negative = [0.0, 1.0] + [0.0] * 2046

    def fake_embeddings(texts, *, input_type, model=None):
        calls.append((input_type, list(texts)))
        if input_type == "query":
            return [positive[:] for _ in texts]
        return [positive[:] if "earnest money" in text.lower() else negative[:] for text in texts]

    monkeypatch.setattr(advanced_retrieval, "generate_embeddings", fake_embeddings)

    first, mode, semantic_count = qa._retrieve_evidence(
        qa_session,
        tender.id,
        "What is the EMD amount and which payment instrument is accepted?",
    )
    assert first
    assert str(relevant.id) in first[0]["chunk"].source_unit_ids
    assert mode == "hybrid"
    assert semantic_count >= 1
    assert any(call[0] == "passage" for call in calls)
    assert any(call[0] == "query" for call in calls)

    passage_calls_before = sum(call[0] == "passage" for call in calls)
    second, second_mode, _ = qa._retrieve_evidence(
        qa_session,
        tender.id,
        "What is the EMD amount and which payment instrument is accepted?",
    )
    assert second and str(relevant.id) in second[0]["chunk"].source_unit_ids
    assert second_mode == "hybrid"
    assert sum(call[0] == "passage" for call in calls) == passage_calls_before

    cached = qa_session.scalars(select(DocumentRetrievalChunkEmbedding)).all()
    assert cached
    assert all(item.embedding_model == "nvidia/nemotron-3-embed-1b" for item in cached)
    assert all(len(item.embedding) == 2048 for item in cached)


def test_retrieval_falls_back_to_keyword_when_embeddings_fail(qa_session, monkeypatch) -> None:
    tender = Tender(name="EMD Tender", status=TenderStatus.ACTIVE)
    qa_session.add(tender)
    qa_session.flush()
    unit = _add_unit(
        qa_session,
        tender,
        "emd.pdf",
        "Earnest money deposit must be INR 20,000 through demand draft.",
    )
    qa_session.commit()

    monkeypatch.setattr(advanced_retrieval, "get_settings", _semantic_settings)
    monkeypatch.setattr(
        advanced_retrieval,
        "generate_embeddings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(qa.EmbeddingServiceError("offline")),
    )

    results, mode, semantic_count = qa._retrieve_evidence(
        qa_session, tender.id, "What is the EMD amount?"
    )
    assert results and str(unit.id) in results[0]["chunk"].source_unit_ids
    assert mode == "keyword_fallback"
    assert semantic_count == 0


def test_stream_qa_emits_final_citation_validated_answer(qa_session, monkeypatch):
    tender = Tender(name="Streamed Tender", status=TenderStatus.ACTIVE)
    qa_session.add(tender)
    qa_session.flush()
    unit = _add_unit(
        qa_session,
        tender,
        "emd_notice.pdf",
        "Earnest money deposit is INR 80,000.",
    )
    qa_session.commit()

    monkeypatch.setattr(qa, "_retrieve_evidence", lambda **_kwargs: ([{
        "unit": unit,
        "filename": "emd_notice.pdf",
        "content": "Earnest money deposit is INR 80,000.",
    }], "hybrid", 4))
    monkeypatch.setattr(qa, "generate_response_stream", lambda *_args, **_kwargs: iter([
        "The EMD is INR 80,000 [S1]. An unsupported claim is INR 5,000 [S9]."
    ]))

    events = list(qa.stream_tender_question_events(
        qa_session, tender.id, "What is the EMD amount?"
    ))

    event_names = [name for name, _payload in events]
    final_payload = events[-1][1]
    assert event_names == ["meta", "delta", "done"]
    assert final_payload["evidence_found"] is True
    assert final_payload["retrieval_mode"] == "hybrid"
    assert "[S1]" in final_payload["answer"]
    assert "[S9]" not in final_payload["answer"]
    assert final_payload["sources"][0]["document_name"] == "emd_notice.pdf"


def test_stream_qa_returns_grounded_no_evidence_message_without_calling_model(qa_session, monkeypatch):
    tender = Tender(name="Unprocessed Tender", status=TenderStatus.ACTIVE)
    qa_session.add(tender)
    qa_session.commit()
    monkeypatch.setattr(qa, "_retrieve_evidence", lambda **_kwargs: ([], "none", 0))
    monkeypatch.setattr(qa, "generate_response_stream", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("Model should not run without retrieved evidence")
    ))

    events = list(qa.stream_tender_question_events(
        qa_session, tender.id, "What is the EMD amount?"
    ))

    assert len(events) == 1
    assert events[0][0] == "done"
    assert events[0][1]["evidence_found"] is False
    assert events[0][1]["sources"] == []


def test_retrieval_uses_latest_successful_processing_item_per_document(qa_session):
    from datetime import UTC, datetime, timedelta

    tender = Tender(name="Reprocessing Tender", status=TenderStatus.ACTIVE)
    qa_session.add(tender)
    qa_session.flush()
    document = TenderDocument(
        tender_id=tender.id,
        original_filename="reprocessed.pdf",
        storage_key=f"{uuid4().hex}.pdf",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=100,
        category=DocumentCategory.MAIN_TENDER,
    )
    qa_session.add(document)
    qa_session.flush()
    old_time = datetime.now(UTC) - timedelta(days=2)
    new_time = datetime.now(UTC) - timedelta(days=1)
    old_run = DocumentProcessingRun(
        tender_id=tender.id, company_id=None, requested_document_count=1,
        status="completed", created_at=old_time, completed_at=old_time,
    )
    qa_session.add(old_run)
    qa_session.flush()
    old_item = DocumentProcessingRunItem(run_id=old_run.id, document_id=document.id, status="completed", completed_at=old_time)
    qa_session.add(old_item)
    qa_session.flush()
    old_unit = DocumentExtractionUnit(
        run_item_id=old_item.id, unit_index=0, unit_type="text",
        source_ref={"page": 4}, content_text="The valid previous successful extraction states EMD is INR 25,000.",
        metadata_json={"format": "pdf"},
    )
    qa_session.add(old_unit)

    failed_run = DocumentProcessingRun(
        tender_id=tender.id, company_id=None, requested_document_count=1,
        status="failed", created_at=new_time,
    )
    qa_session.add(failed_run)
    qa_session.flush()
    failed_item = DocumentProcessingRunItem(
        run_id=failed_run.id, document_id=document.id, status="failed", safe_message="Parser failed",
    )
    qa_session.add(failed_item)
    qa_session.commit()

    chunks, run_item_ids = advanced_retrieval._load_current_chunks(
        qa_session, tender.id, _semantic_settings(),
    )
    assert run_item_ids == [old_item.id]
    assert chunks
    assert all(chunk.run_item_id == old_item.id for chunk in chunks)
    assert str(old_unit.id) in chunks[0].source_unit_ids
