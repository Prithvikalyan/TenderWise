from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    DocumentCategory,
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    Tender,
    TenderDocument,
)
from app.services import qa


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_tender_evidence(session: Session, content: str = "Earnest money deposit is INR 80,000.") -> Tender:
    tender = Tender(name="Streaming QA tender")
    document = TenderDocument(
        tender=tender,
        original_filename="tender_notice.pdf",
        storage_key="streaming-qa-document",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=1024,
        category=DocumentCategory.MAIN_TENDER,
    )
    run = DocumentProcessingRun(tender=tender, status="completed", requested_document_count=1)
    item = DocumentProcessingRunItem(run=run, document=document, status="completed")
    DocumentExtractionUnit(
        run_item=item,
        unit_index=0,
        unit_type="text",
        source_ref={"filename": "tender_notice.pdf", "page": 1, "block_index": 3},
        content_text=content,
    )
    session.add(tender)
    session.commit()
    return tender


def test_stream_events_include_deltas_and_final_citation_validation(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    tender = add_tender_evidence(session)
    monkeypatch.setattr(
        qa,
        "generate_response_stream",
        lambda *_args, **_kwargs: iter(["EMD is INR 80,000 [S1]. Unsupported claim [S99]."]),
    )

    events = list(qa.stream_tender_question_events(session, tender.id, "What is the EMD amount?"))

    assert [name for name, _payload in events] == ["meta", "delta", "done"]
    assert events[0][1]["sources"][0]["source_id"] == "S1"
    assert "INR 80,000 [S1]" in events[-1][1]["answer"]
    assert "[S99]" not in events[-1][1]["answer"]
    assert events[-1][1]["evidence_found"] is True


def test_stream_without_evidence_returns_a_complete_no_evidence_answer(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    tender = Tender(name="No evidence tender")
    session.add(tender)
    session.commit()
    monkeypatch.setattr(
        qa,
        "generate_response_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Provider must not run without evidence")),
    )

    events = list(qa.stream_tender_question_events(session, tender.id, "What is the EMD amount?"))

    assert len(events) == 1
    assert events[0][0] == "done"
    assert events[0][1]["evidence_found"] is False
    assert events[0][1]["sources"] == []


def test_stream_for_missing_tender_returns_error_event(session: Session) -> None:
    from uuid import uuid4

    events = list(qa.stream_tender_question_events(session, uuid4(), "What is the EMD amount?"))

    assert events == [("error", {"message": "Tender not found."})]
