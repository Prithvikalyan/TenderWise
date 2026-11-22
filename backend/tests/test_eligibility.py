from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    DocumentCategory,
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    Tender,
    TenderDocument,
)
from app.schemas.eligibility import EligibilitySource
from app.services import eligibility


@pytest.fixture
def eligibility_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


def _add_processed_document(session, tender, filename, category, contents, *, created_at):
    document = TenderDocument(
        tender=tender,
        original_filename=filename,
        storage_key=f"{uuid4()}.bin",
        extension=".pdf" if filename.endswith(".pdf") else ".docx",
        content_type="application/pdf" if filename.endswith(".pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=100,
        category=category,
    )
    run = DocumentProcessingRun(
        tender=tender,
        status="completed",
        requested_document_count=1,
        created_at=created_at,
        completed_at=created_at,
        updated_at=created_at,
    )
    item = DocumentProcessingRunItem(
        run=run,
        document=document,
        status="completed",
        attempt_count=1,
        max_attempts=3,
        created_at=created_at,
        completed_at=created_at,
    )
    for index, (unit_type, content, source_ref) in enumerate(contents):
        kwargs = {
            "run_item": item,
            "unit_index": index,
            "unit_type": unit_type,
            "source_ref": source_ref,
            "created_at": created_at,
        }
        if unit_type == "table":
            kwargs["table_data"] = content
        else:
            kwargs["content_text"] = content
        session.add(DocumentExtractionUnit(**kwargs))
    session.add_all([document, run])
    session.flush()
    return document


def test_eligibility_fallback_joins_clause_fragments_within_same_page():
    source_list = [
        EligibilitySource(
            source_id="T1", document_name="Notice.pdf", document_role="tender",
            unit_type="text", location={"page": 1, "block_index": 0}, citation="Page 1, block 1",
        ),
        EligibilitySource(
            source_id="T2", document_name="Notice.pdf", document_role="tender",
            unit_type="text", location={"page": 1, "block_index": 1}, citation="Page 1, block 2",
        ),
        EligibilitySource(
            source_id="T3", document_name="Annexure.docx", document_role="tender",
            unit_type="table", location={"table_index": 0}, citation="Table 1",
        ),
    ]
    evidence = {
        "T1": "The bidder must have completed at least two similar supply-and-installation contracts during the",
        "T2": "five financial years preceding the bid submission deadline. Purchase orders and completion certificates are required.",
        "T3": (
            "Legal registration in India | Registration certificate\n"
            "Average annual turnover of INR 40,00,000 over the last three completed financial years\n"
            "Signed self-declaration that the bidder has not been blacklisted"
        ),
    }

    requirements = eligibility._deterministic_fallback(evidence, source_list)

    categories = {item.category for item in requirements}
    assert categories == {"registration", "experience", "declaration", "financial"}
    experience = next(item for item in requirements if item.category == "experience")
    assert experience.tender_source_ids == ["T1", "T2"]
    assert all(item.tender_source_ids for item in requirements)


def test_analysis_prioritises_eligibility_evidence_over_newer_unrelated_units(
    eligibility_session, monkeypatch
):
    session = eligibility_session
    tender = Tender(name="Sample tender")
    session.add(tender)
    session.flush()

    earlier = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    later = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    eligibility_table = {
        "column_count": 3,
        "rows": [
            {"row_index": 0, "cells": [{"cell_index": 0, "text": "Legal registration in India"}, {"cell_index": 1, "text": "Registration certificate"}]},
            {"row_index": 1, "cells": [{"cell_index": 0, "text": "At least two similar supply-and-installation contracts during the five financial years"}, {"cell_index": 1, "text": "Purchase orders and completion certificates"}]},
            {"row_index": 2, "cells": [{"cell_index": 0, "text": "Average annual turnover INR 40,00,000 over the last three completed financial years"}]},
            {"row_index": 3, "cells": [{"cell_index": 0, "text": "Signed self-declaration that the bidder has not been blacklisted"}]},
        ],
    }
    _add_processed_document(
        session, tender, "Eligibility_Annexure.docx", DocumentCategory.ANNEXURE_FORM,
        [("table", eligibility_table, {"table_index": 0})], created_at=earlier,
    )
    # A more recently processed BOQ/technical document contains many irrelevant
    # units. It must not consume the complete 40-source budget.
    _add_processed_document(
        session, tender, "Recent_BOQ.pdf", DocumentCategory.MAIN_TENDER,
        [
            ("text", f"Commercial pricing notes and item description {index}.", {"page": 1, "block_index": index})
            for index in range(55)
        ], created_at=later,
    )
    session.commit()

    monkeypatch.setattr(
        eligibility,
        "generate_response",
        lambda *_args, **_kwargs: json.dumps({"requirements": []}),
    )

    result = eligibility.analyze_eligibility(session, tender.id)

    assert result.overall_status == "incomplete"
    assert len(result.requirements) == 4
    assert {item.category for item in result.requirements} == {
        "registration", "experience", "financial", "declaration"
    }
    assert result.extracted_unit_count == 56
    assert result.reviewed_source_count <= 40
    assert all(item.tender_source_ids for item in result.requirements)
