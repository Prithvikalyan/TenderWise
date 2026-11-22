from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy import Column, MetaData, Table

from app.db.vector_types import Vector
from app.evaluation.metrics import ranking_metrics
from app.services.retrieval_chunks import build_retrieval_chunks, estimate_tokens
from app.services.advanced_retrieval import _rrf_merge


def _unit(index: int, text: str = "", *, source_ref=None, unit_type="text", table_data=None):
    return SimpleNamespace(
        id=uuid4(),
        unit_index=index,
        unit_type=unit_type,
        source_ref=source_ref or {"page": 1, "block_index": index},
        content_text=text,
        table_data=table_data,
    )


def test_structure_chunker_keeps_heading_context_and_source_refs():
    tender_id, document_id, run_item_id = uuid4(), uuid4(), uuid4()
    units = [
        _unit(0, "ELIGIBILITY REQUIREMENTS", source_ref={"page": 2, "heading_level": 1}),
        _unit(1, "The bidder must have at least five years of experience.", source_ref={"page": 2, "block_index": 1}),
        _unit(2, "Average annual turnover must exceed INR 50 lakh.", source_ref={"page": 2, "block_index": 2}),
        _unit(3, "TECHNICAL SPECIFICATIONS", source_ref={"page": 2, "heading_level": 1}),
        _unit(4, "The equipment must support a 4K display.", source_ref={"page": 2, "block_index": 4}),
    ]

    chunks = build_retrieval_chunks(
        tender_id=tender_id,
        document_id=document_id,
        run_item_id=run_item_id,
        units=units,
        target_tokens=200,
        overlap_tokens=40,
    )

    assert len(chunks) == 2
    eligibility, technical = chunks
    assert eligibility.section_heading == "ELIGIBILITY REQUIREMENTS"
    assert "five years of experience" in eligibility.content_text
    assert "INR 50 lakh" in eligibility.content_text
    assert eligibility.source_refs[0]["page"] == 2
    assert set(eligibility.source_unit_ids) == {str(units[1].id), str(units[2].id)}
    assert technical.section_heading == "TECHNICAL SPECIFICATIONS"
    assert "4K display" in technical.content_text


def test_table_chunks_repeat_headers_and_preserve_row_locations():
    tender_id, document_id, run_item_id = uuid4(), uuid4(), uuid4()
    rows = [{"row_index": 0, "cells": [{"text": "Item"}, {"text": "Quantity"}, {"text": "Specification"}]}]
    rows.extend(
        {"row_index": idx, "cells": [{"text": f"Item {idx}"}, {"text": str(idx * 2)}, {"text": f"Detailed specification {idx}"}]}
        for idx in range(1, 14)
    )
    unit = _unit(0, unit_type="table", source_ref={"page": 7, "table_index": 0}, table_data={"rows": rows})

    chunks = build_retrieval_chunks(
        tender_id=tender_id,
        document_id=document_id,
        run_item_id=run_item_id,
        units=[unit],
        target_tokens=25,
        overlap_tokens=5,
    )

    assert len(chunks) > 1
    assert all("Table columns: Item | Quantity | Specification" in chunk.content_text for chunk in chunks)
    assert all(chunk.chunk_type == "table" for chunk in chunks)
    assert all(chunk.source_refs[0]["page"] == 7 for chunk in chunks)
    assert all("row_start" in chunk.source_refs[0] and "row_end" in chunk.source_refs[0] for chunk in chunks)


def test_chunker_caps_overlap_and_rejects_invalid_target():
    units = [_unit(0, " ".join(["procurement"] * 400))]
    chunks = build_retrieval_chunks(
        tender_id=uuid4(), document_id=uuid4(), run_item_id=uuid4(),
        units=units, target_tokens=40, overlap_tokens=200,
    )
    assert len(chunks) > 1
    assert all(estimate_tokens(chunk.content_text) <= 40 for chunk in chunks)
    with pytest.raises(ValueError, match="target_tokens"):
        build_retrieval_chunks(
            tender_id=uuid4(), document_id=uuid4(), run_item_id=uuid4(), units=units, target_tokens=0,
        )


def test_rrf_fuses_two_independent_rankings_without_duplicate_entries():
    from app.models.retrieval import DocumentRetrievalChunk

    a, b, c = (DocumentRetrievalChunk(id=uuid4(), content_text=value) for value in ("a", "b", "c"))
    merged = _rrf_merge([[a, b], [b, c]], rrf_k=60)
    assert [chunk.id for chunk, _ in merged] == [b.id, a.id, c.id]
    assert len({chunk.id for chunk, _ in merged}) == 3
    assert merged[0][1] > merged[1][1]


def test_vector_sqlalchemy_type_compiles_to_postgresql_vector_2048():
    table = Table(
        "vector_test", MetaData(),
        Column("embedding", Vector(2048), nullable=False),
    )
    rendered = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "VECTOR(2048)" in rendered


def test_ranking_metrics_known_values():
    result = ranking_metrics(["irrelevant", "right", "also-right"], {"right", "also-right"}, k=3)
    assert result["recall@3"] == 1.0
    assert result["precision@3"] == pytest.approx(2 / 3)
    assert result["mrr"] == 0.5
    assert 0.0 < result["ndcg@3"] < 1.0


def test_claim_level_citation_check_flags_missing_or_mismatched_source_support():
    from app.schemas.qa import QASource
    from app.services.qa import _citation_status

    source = QASource(
        source_id="S1",
        document_name="notice.pdf",
        unit_type="text",
        location={"page": 3},
        citation="notice.pdf — Page 3",
    )
    source_texts = {"S1": "The average annual turnover requirement is INR 50 lakhs for the previous three financial years."}

    supported_status, supported_details = _citation_status(
        "The average annual turnover requirement is INR 50 lakhs [S1].", [source], source_texts,
    )
    assert supported_status == "valid_structure"
    assert supported_details[0]["status"] == "support_overlap_found"

    wrong_value_status, wrong_value_details = _citation_status(
        "The average annual turnover requirement is INR 500 lakhs [S1].", [source], source_texts,
    )
    assert wrong_value_status == "needs_review"
    assert wrong_value_details[0]["numeric_values_match"] is False

    missing_citation_status, missing_citation_details = _citation_status(
        "The average annual turnover requirement is INR 50 lakhs.", [source], source_texts,
    )
    assert missing_citation_status == "needs_review"
    assert missing_citation_details[0]["source_ids"] == []



def test_claim_numeric_validation_uses_complete_tokens_not_substrings():
    from app.schemas.qa import QASource
    from app.services.qa import _citation_status

    source = QASource(
        source_id="S1",
        document_name="notice.pdf",
        unit_type="text",
        location={"page": 4},
        citation="notice.pdf — Page 4",
    )
    source_texts = {"S1": "The earnest money deposit is INR 18,000."}

    status, details = _citation_status(
        "The earnest money deposit is INR 8,000 [S1].",
        [source],
        source_texts,
    )

    assert status == "needs_review"
    assert details[0]["numeric_values_match"] is False


def test_claim_numeric_validation_accepts_indian_thousands_formatting():
    from app.schemas.qa import QASource
    from app.services.qa import _citation_status

    source = QASource(
        source_id="S1",
        document_name="notice.pdf",
        unit_type="text",
        location={"page": 4},
        citation="notice.pdf — Page 4",
    )
    source_texts = {"S1": "The earnest money deposit is INR 1,50,000."}

    status, details = _citation_status(
        "The earnest money deposit is INR 150000 [S1].",
        [source],
        source_texts,
    )

    assert status == "valid_structure"
    assert details[0]["numeric_values_match"] is True



def test_evaluation_source_matching_requires_all_location_coordinates():
    from app.evaluation.rag_evaluate import _matches_reference

    expected = {
        "document_name": "notice.pdf",
        "location": {"page": 4, "table_index": 2},
    }
    wrong_page = {
        "document_name": "notice.pdf",
        "source_refs": [{"page": 5, "table_index": 2}],
    }
    wrong_table = {
        "document_name": "notice.pdf",
        "source_refs": [{"page": 4, "table_index": 1}],
    }
    exact = {
        "document_name": "notice.pdf",
        "source_refs": [
            {"page": 4, "table_index": 1},
            {"page": 4, "table_index": 2},
        ],
    }

    assert _matches_reference(wrong_page, expected) is False
    assert _matches_reference(wrong_table, expected) is False
    assert _matches_reference(exact, expected) is True
