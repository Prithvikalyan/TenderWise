from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document

from app.core.config import Settings
from app.services.docx_extraction import DocxTextExtractionHandler
from app.services.processing_handler import ProcessingContext, ProcessingFailure
from app.services.processing_handlers import FormatAwareProcessingHandler
from app.services.pdf_extraction import PdfTextExtractionHandler


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def context(path: Path, *, extension=".docx", filename="tender.docx"):
    return ProcessingContext(uuid4(), path, threading.Event(), filename, extension)


def save_doc(path, build):
    document = Document()
    build(document)
    document.save(path)
    return path


def test_paragraphs_heading_levels_and_empty_documents(tmp_path):
    def build(document):
        document.add_paragraph("before")
        document.add_heading("Overview", level=2)
        custom_heading = document.styles.add_style("Custom section", 1)
        custom_heading.base_style = document.styles["Heading 3"]
        document.add_paragraph("Inherited heading", style=custom_heading)
        document.add_paragraph("नमस्ते 🌏")

    path = save_doc(tmp_path / "paragraphs.docx", build)
    units = DocxTextExtractionHandler(settings()).process(context(path))
    assert [unit.content_text for unit in units] == [
        "before", "Overview", "Inherited heading", "नमस्ते 🌏"
    ]
    assert units[1].source_ref["heading_level"] == 2
    assert units[1].source_ref["paragraph_style"] == "Heading 2"
    assert units[2].source_ref["heading_level"] == 3
    assert units[2].source_ref["paragraph_style"] == "Custom section"
    assert [unit.source_ref["paragraph_index"] for unit in units] == [0, 1, 2, 3]
    assert all(unit.source_ref["document_id"] and unit.source_ref["filename"] == "tender.docx" for unit in units)
    empty = save_doc(tmp_path / "empty.docx", lambda _d: None)
    assert DocxTextExtractionHandler(settings()).process(context(empty)) == []


def test_paragraphs_and_multiple_tables_keep_body_order_and_references(tmp_path):
    def build(document):
        document.add_paragraph("first")
        first = document.add_table(rows=2, cols=2)
        first.cell(0, 0).text = "Item"
        first.cell(0, 1).text = ""
        first.cell(1, 0).text = "Bolt"
        first.cell(1, 1).text = "4"
        document.add_paragraph("middle")
        second = document.add_table(rows=1, cols=1)
        second.cell(0, 0).text = "尾"
        document.add_paragraph("last")

    path = save_doc(tmp_path / "ordered.docx", build)
    units = DocxTextExtractionHandler(settings()).process(context(path))
    assert [unit.unit_type for unit in units] == ["text", "table", "text", "table", "text"]
    assert [unit.source_ref["body_order_index"] for unit in units] == [0, 1, 2, 3, 4]
    assert units[1].source_ref["table_index"] == 0
    assert units[3].source_ref["table_index"] == 1
    assert units[1].table_data["rows"][0]["cells"] == [
        {"cell_index": 0, "text": "Item"}, {"cell_index": 1, "text": ""}
    ]


def test_ragged_table_and_empty_cell_positions_are_retained(tmp_path):
    path = save_doc(tmp_path / "ragged.docx", lambda d: d.add_table(rows=2, cols=3))
    # Produce a valid ragged Word grid by removing one cell from the final row XML.
    document = Document(path)
    row = document.tables[0].rows[1]._tr
    row.remove(row.tc_lst[-1])
    document.save(path)
    table_unit = DocxTextExtractionHandler(settings()).process(context(path))[0]
    assert table_unit.table_data["column_count"] == 3
    assert len(table_unit.table_data["rows"][0]["cells"]) == 3
    assert len(table_unit.table_data["rows"][1]["cells"]) == 2
    assert [cell["text"] for cell in table_unit.table_data["rows"][1]["cells"]] == ["", ""]


def test_text_and_total_result_limits_reject_without_partial_results(tmp_path):
    large_text = save_doc(tmp_path / "large-text.docx", lambda d: d.add_paragraph("x" * 32))
    with pytest.raises(ProcessingFailure) as raised:
        DocxTextExtractionHandler(settings(tenderwise_max_extraction_text_unit_bytes=16)).process(context(large_text))
    assert raised.value.outcome_code == "resource_limit_exceeded"

    many = save_doc(tmp_path / "many.docx", lambda d: [d.add_paragraph("abc") for _ in range(8)])
    with pytest.raises(ProcessingFailure) as raised:
        DocxTextExtractionHandler(settings(tenderwise_max_extraction_result_bytes=300)).process(context(many))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_late_parser_failure_is_safely_mapped_after_content_seen(tmp_path, monkeypatch):
    source = save_doc(tmp_path / "valid.docx", lambda d: d.add_paragraph("already parsed"))
    handler = DocxTextExtractionHandler(settings())
    real_document = __import__("app.services.docx_extraction", fromlist=["Document"]).Document

    class BrokenBody:
        def iterchildren(self):
            yield from real_document(source).element.body.iterchildren()
            raise ValueError("secret parser path")

    class BrokenDocument:
        element = type("Element", (), {"body": BrokenBody()})()

    monkeypatch.setattr("app.services.docx_extraction.Document", lambda _path: BrokenDocument())
    with pytest.raises(ProcessingFailure) as raised:
        handler.process(context(source))
    assert raised.value.outcome_code == "malformed_file"
    assert str(source) not in raised.value.safe_message
    assert "secret" not in raised.value.safe_message


@pytest.mark.parametrize("payload", [b"not a zip", b"PK\x03\x04truncated"])
def test_malformed_docx_is_a_safe_permanent_failure(tmp_path, payload):
    path = tmp_path / "bad.docx"
    path.write_bytes(payload)
    with pytest.raises(ProcessingFailure) as raised:
        DocxTextExtractionHandler(settings()).process(context(path))
    assert raised.value.failure_class == "permanent"
    assert raised.value.outcome_code == "malformed_file"
    assert str(path) not in raised.value.safe_message


def test_dispatcher_routes_only_docx_extension(tmp_path):
    path = save_doc(tmp_path / "dispatch.docx", lambda d: d.add_paragraph("ok"))
    dispatcher = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings()), DocxTextExtractionHandler(settings()))
    assert dispatcher.process(context(path))[0].content_text == "ok"
    with pytest.raises(ProcessingFailure) as raised:
        dispatcher.process(context(path, extension=".doc"))
    assert raised.value.outcome_code == "unsupported_format"
