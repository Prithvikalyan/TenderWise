from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pdfplumber
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.pdf_extraction import (
    PdfTextExtractionHandler,
    _TextBlock,
    _TableRecord,
    _coordinates_aligned,
    _find_tables,
    _retained_block,
    _table_owned_words,
)
from app.services.processing_handler import ProcessingContext, ProcessingFailure
from app.services.processing_handlers import FormatAwareProcessingHandler


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_extraction_limits_have_single_validated_defaults():
    configured = settings()
    assert configured.tenderwise_max_pdf_pages == 500
    assert configured.tenderwise_max_extraction_result_bytes == 25 * 1024 * 1024
    assert configured.tenderwise_max_extraction_text_unit_bytes == 256 * 1024
    with pytest.raises(ValidationError):
        settings(tenderwise_max_pdf_pages=501)
    with pytest.raises(ValidationError):
        settings(tenderwise_max_extraction_result_bytes=25 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError):
        settings(tenderwise_max_extraction_text_unit_bytes=256 * 1024 + 1)


def context(path: Path, *, extension: str = ".pdf", filename: str = "original.pdf") -> ProcessingContext:
    return ProcessingContext(uuid4(), path, threading.Event(), filename, extension)


def write_pdf(path: Path, pages: list[list[str]]) -> Path:
    document = pymupdf.open()
    for page_texts in pages:
        page = document.new_page(width=612, height=792)
        for index, text in enumerate(page_texts):
            page.insert_text((72, 72 + index * 40), text, fontsize=11)
    document.save(path)
    document.close()
    return path


def draw_table(page, xs, ys, values):
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]), color=(0, 0, 0), width=1)
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y), color=(0, 0, 0), width=1)
    for row_index, row in enumerate(values):
        for column_index, value in enumerate(row):
            if value:
                page.insert_text(
                    (xs[column_index] + 5, ys[row_index] + 24),
                    value,
                    fontsize=10,
                )


def write_tabular_pdf(path: Path, page_tables, page_text=None):
    document = pymupdf.open()
    for page_index, tables in enumerate(page_tables):
        page = document.new_page(width=612, height=792)
        for table in tables:
            draw_table(page, *table)
        for x, y, text in (page_text or {}).get(page_index, []):
            page.insert_text((x, y), text, fontsize=11)
    document.save(path)
    document.close()
    return path


def test_extracts_multiple_pages_and_blocks_with_stable_references(tmp_path):
    source = write_pdf(tmp_path / "work-random.part", [["first block", "second block"], ["third block"]])
    units = PdfTextExtractionHandler(settings()).process(context(source))

    assert [unit.content_text.strip() for unit in units] == ["first block", "second block", "third block"]
    assert [unit.source_ref["page"] for unit in units] == [1, 1, 2]
    assert [unit.source_ref["block_index"] for unit in units] == [0, 1, 0]
    assert [unit.source_ref["filename"] for unit in units] == ["original.pdf"] * 3
    assert all(len(unit.source_ref["bbox"]) == 4 for unit in units)
    assert all(unit.unit_type == "text" and unit.table_data is None for unit in units)
    assert [unit.metadata["format"] for unit in units] == ["pdf"] * 3


def test_extracts_table_structure_empty_cells_and_text_outside_cells(tmp_path):
    source = write_tabular_pdf(
        tmp_path / "one-table.pdf",
        [[(
            [100, 250, 400],
            [100, 140, 180, 220],
            [["Item", "Quantity"], ["Widget", ""], ["Bolt", "4"]],
        )]],
        page_text={0: [(50, 75, "Outside table heading")]},
    )
    units = PdfTextExtractionHandler(settings()).process(context(source))

    tables = [unit for unit in units if unit.unit_type == "table"]
    texts = [unit for unit in units if unit.unit_type == "text"]
    assert len(tables) == 1
    assert tables[0].source_ref["page"] == 1
    assert tables[0].source_ref["table_index"] == 0
    assert len(tables[0].source_ref["bbox"]) == 4
    assert tables[0].table_data == {
        "column_count": 2,
        "rows": [
            {"row_index": 0, "cells": [{"cell_index": 0, "text": "Item"}, {"cell_index": 1, "text": "Quantity"}]},
            {"row_index": 1, "cells": [{"cell_index": 0, "text": "Widget"}, {"cell_index": 1, "text": ""}]},
            {"row_index": 2, "cells": [{"cell_index": 0, "text": "Bolt"}, {"cell_index": 1, "text": "4"}]},
        ],
    }
    assert len(texts) == 1
    assert texts[0].content_text.strip() == "Outside table heading"


def test_extracts_multiple_tables_on_page_in_geometric_order(tmp_path):
    source = write_tabular_pdf(
        tmp_path / "multiple-tables.pdf",
        [[
                ([80, 150, 220], [100, 140, 180], [["upper-left", "x"], ["y", "z"]]),
                ([80, 150, 220], [300, 340, 380], [["lower-left", "x"], ["y", "z"]]),
        ]],
    )
    units = PdfTextExtractionHandler(settings()).process(context(source))
    tables = [unit for unit in units if unit.unit_type == "table"]
    assert [unit.source_ref["table_index"] for unit in tables] == [0, 1]
    assert [unit.table_data["rows"][0]["cells"][0]["text"] for unit in tables] == [
        "upper-left",
        "lower-left",
    ]
    assert [units.index(unit) for unit in tables] == sorted(units.index(unit) for unit in tables)


def test_extracts_tables_across_pages_with_document_wide_order(tmp_path):
    source = write_tabular_pdf(
        tmp_path / "two-pages-two-tables.pdf",
        [
            [([80, 150, 220], [100, 140, 180], [["page one", "x"], ["y", "z"]])],
            [([80, 150, 220], [100, 140, 180], [["page two", "x"], ["y", "z"]])],
        ],
    )
    units = PdfTextExtractionHandler(settings()).process(context(source))
    tables = [unit for unit in units if unit.unit_type == "table"]
    assert [unit.source_ref["page"] for unit in tables] == [1, 2]
    assert [unit.source_ref["table_index"] for unit in tables] == [0, 0]


def test_empty_and_image_only_pages_produce_no_text_units(tmp_path):
    document = pymupdf.open()
    document.new_page()
    image_page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 2, 2), 0)
    pixmap.clear_with(255)
    image_page.insert_image(image_page.rect, stream=pixmap.tobytes("png"))
    document.new_page()
    document[2].insert_text((72, 72), "text after empty page")
    source = tmp_path / "empty-pages.pdf"
    document.save(source)
    document.close()

    units = PdfTextExtractionHandler(settings()).process(context(source))
    assert len(units) == 1
    assert units[0].content_text.strip() == "text after empty page"
    assert units[0].source_ref["page"] == 3


def test_image_only_pdf_completes_with_zero_units(tmp_path):
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 2, 2), 0)
    pixmap.clear_with(127)
    page.insert_image(page.rect, stream=pixmap.tobytes("png"))
    source = tmp_path / "image-only.pdf"
    document.save(source)
    document.close()

    assert PdfTextExtractionHandler(settings()).process(context(source)) == []


@pytest.mark.parametrize("content", [b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_malformed_and_truncated_pdf_fail_permanently(tmp_path, content):
    source = tmp_path / "invalid.pdf.part"
    source.write_bytes(content)
    with pytest.raises(ProcessingFailure) as raised:
        PdfTextExtractionHandler(settings()).process(context(source))
    assert raised.value.failure_class == "permanent"
    assert raised.value.outcome_code == "malformed_file"
    assert "private" not in raised.value.safe_message
    assert str(source) not in raised.value.safe_message


def test_exactly_500_pages_is_allowed_and_501_is_rejected(tmp_path):
    source = write_pdf(tmp_path / "500.pdf", [[] for _ in range(500)])
    assert PdfTextExtractionHandler(settings()).process(context(source)) == []

    too_many = write_pdf(tmp_path / "501.pdf", [[] for _ in range(501)])
    with pytest.raises(ProcessingFailure) as raised:
        PdfTextExtractionHandler(settings()).process(context(too_many))
    assert raised.value.outcome_code == "resource_limit_exceeded"
    assert raised.value.failure_class == "permanent"


class _FakePage:
    def __init__(self, blocks):
        self.blocks = blocks
        self.rotation = 90

    def get_text(self, *_args, **_kwargs):
        return self.blocks


class _FakePdf:
    def __init__(self, blocks):
        self.page_count = 1
        self.page = _FakePage(blocks)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def load_page(self, _index):
        return self.page


class _FakePlumberPage:
    width = 612
    height = 792
    bbox = (0.0, 0.0, 612.0, 792.0)

    def __init__(self, tables=None):
        self._tables = tables or []

    def find_tables(self):
        return SimpleNamespace(tables=self._tables)


class _FakePlumberPdf:
    def __init__(self, tables=None):
        self.pages = [_FakePlumberPage(tables)]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _block(text: str, block_index: int = 0):
    return (1.0, 2.0, 3.0, 4.0, text, block_index, 0)


class _FakeRow:
    def __init__(self, cells):
        self.cells = cells


class _FakeTable:
    def __init__(self, bbox, rows, values):
        self.bbox = bbox
        self.rows = [_FakeRow(row) for row in rows]
        self.values = values

    def extract(self):
        return self.values


def test_ragged_table_rows_keep_grid_and_normalize_missing_cells():
    table = _FakeTable(
        (10.0, 20.0, 50.0, 60.0),
        [
            [(10, 20, 20, 30), (20, 20, 30, 30), (30, 20, 40, 30)],
            [(10, 30, 20, 40), (20, 30, 30, 40)],
        ],
        [[None, "A"], ["B"]],
    )
    result = _find_tables(SimpleNamespace(find_tables=lambda: [table]))
    assert len(result) == 1
    assert result[0].rows == [
        {"row_index": 0, "cells": [
            {"cell_index": 0, "text": ""},
            {"cell_index": 1, "text": "A"},
            {"cell_index": 2, "text": ""},
        ]},
        {"row_index": 1, "cells": [
            {"cell_index": 0, "text": "B"},
            {"cell_index": 1, "text": ""},
            {"cell_index": 2, "text": ""},
        ]},
    ]


def test_exact_duplicate_table_detections_are_removed_but_overlaps_are_kept():
    cell = (10.0, 20.0, 30.0, 40.0)
    first = _FakeTable((10.0, 20.0, 30.0, 40.0), [[cell]], [["first"]])
    duplicate = _FakeTable((10.0, 20.0, 30.0, 40.0), [[cell]], [["first"]])
    overlapping = _FakeTable(
        (15.0, 25.0, 35.0, 45.0), [[(15.0, 25.0, 35.0, 45.0)]], [["overlap"]]
    )
    results = _find_tables(
        SimpleNamespace(find_tables=lambda: [first, duplicate, overlapping])
    )
    assert len(results) == 2
    assert [table.table_index for table in results] == [0, 1]
    assert [table.rows[0]["cells"][0]["text"] for table in results] == ["first", "overlap"]


def test_word_center_assignment_uses_half_open_cell_boundaries():
    table = _TableRecord(
        (0, 0, 20, 10),
        [],
        ((0, 0, 10, 10), (10, 0, 20, 10)),
        (),
    )
    words = [
        (8, 4, 12, 6, "boundary", 1, 0, 0),  # center x=10 belongs to cell two
        (18, 4, 22, 6, "outside", 1, 0, 1),
    ]
    assert _table_owned_words(words, [table]) == {(1, 0, 0)}


def test_partially_overlapping_block_keeps_words_outside_table_cells():
    block = _TextBlock(4, "left table right", (0, 0, 30, 10))
    words = {
        4: [
            (0, 2, 9, 8, "left", 4, 0, 0),
            (11, 2, 19, 8, "table", 4, 0, 1),
            (21, 2, 30, 8, "right", 4, 0, 2),
        ]
    }
    retained = _retained_block(block, {(4, 0, 1)}, True, words)
    assert retained is not None
    assert retained[0] == "left right"
    assert retained[1] == (0.0, 2.0, 30.0, 8.0)
    assert retained[2] == 0


def test_unverified_rotation_keeps_original_text_alongside_table(monkeypatch, tmp_path):
    source = tmp_path / "rotated.pdf.part"
    source.touch()
    table = _FakeTable(
        (10, 10, 40, 40), [[(10, 10, 40, 40)]], [["table text"]]
    )
    fake_pdf(monkeypatch, [_block("table text")], [table])
    units = PdfTextExtractionHandler(settings()).process(context(source))
    assert [unit.unit_type for unit in units] == ["text", "table"]
    assert units[0].content_text == "table text"
    assert units[1].table_data["rows"][0]["cells"][0]["text"] == "table text"
    assert units[1].metadata["overlap_alignment_verified"] is False


def test_same_position_tie_break_is_text_then_table(monkeypatch, tmp_path):
    source = tmp_path / "same-position.pdf.part"
    source.touch()
    table = _FakeTable((1, 2, 3, 4), [[(1, 2, 3, 4)]], [["table"]])
    fake_pdf(monkeypatch, [_block("ordinary")], [table])
    units = PdfTextExtractionHandler(settings()).process(context(source))
    assert [unit.unit_type for unit in units] == ["text", "table"]


def test_later_table_failure_returns_no_accumulated_units(monkeypatch, tmp_path):
    source = tmp_path / "later-table-failure.pdf.part"
    source.touch()
    cell = (10.0, 10.0, 40.0, 40.0)
    first_table = _FakeTable((10, 10, 40, 40), [[cell]], [["valid first table"]])

    class FakeMupdfPage(_FakePage):
        def __init__(self):
            super().__init__([_block("text before later failure")])

    class FakeDocument(_FakePdf):
        def __init__(self):
            super().__init__([])
            self.page_count = 2

        def load_page(self, _index):
            return FakeMupdfPage()

    class FailingPlumberPage(_FakePlumberPage):
        def find_tables(self):
            raise RuntimeError("private parser detail")

    class TwoPagePlumberPdf:
        pages = [_FakePlumberPage([first_table]), FailingPlumberPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "app.services.pdf_extraction.pymupdf.open",
        lambda *_args, **_kwargs: FakeDocument(),
    )
    monkeypatch.setattr(
        "app.services.pdf_extraction.pdfplumber.open",
        lambda *_args, **_kwargs: TwoPagePlumberPdf(),
    )

    with pytest.raises(ProcessingFailure) as raised:
        PdfTextExtractionHandler(settings()).process(context(source))
    assert raised.value.failure_class == "permanent"
    assert raised.value.outcome_code == "malformed_file"
    assert "private parser detail" not in raised.value.safe_message
    assert str(source) not in raised.value.safe_message


def test_rotated_and_cropped_page_coordinates_are_not_declared_aligned(tmp_path):
    for name, rotate, crop in (("rotated", True, False), ("cropped", False, True)):
        source = tmp_path / f"{name}.pdf"
        document = pymupdf.open()
        page = document.new_page(width=612, height=792)
        if rotate:
            page.set_rotation(90)
        if crop:
            page.set_cropbox(pymupdf.Rect(0, 0, 400, 500))
        document.save(source)
        document.close()
        with pymupdf.open(source) as mupdf_document, pdfplumber.open(source) as plumber_document:
            assert not _coordinates_aligned(mupdf_document[0], plumber_document.pages[0])


def fake_pdf(monkeypatch, blocks, tables=None):
    monkeypatch.setattr("app.services.pdf_extraction.pymupdf.open", lambda *_args, **_kwargs: _FakePdf(blocks))
    monkeypatch.setattr(
        "app.services.pdf_extraction.pdfplumber.open",
        lambda *_args, **_kwargs: _FakePlumberPdf(tables),
    )


def test_text_unit_limit_accepts_exact_boundary_and_rejects_over(monkeypatch, tmp_path):
    handler = PdfTextExtractionHandler(settings())
    source = tmp_path / "unit.pdf.part"
    source.touch()
    fake_pdf(monkeypatch, [_block("a" * (256 * 1024))])
    assert len(handler.process(context(source))) == 1

    fake_pdf(monkeypatch, [_block("a" * (256 * 1024 + 1))])
    with pytest.raises(ProcessingFailure) as raised:
        handler.process(context(source))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_text_unit_limit_counts_multibyte_utf8_bytes(monkeypatch, tmp_path):
    handler = PdfTextExtractionHandler(settings())
    source = tmp_path / "unicode-unit.pdf.part"
    source.touch()
    exact = "é" * (256 * 1024 // 2)
    fake_pdf(monkeypatch, [_block(exact)])
    assert len(handler.process(context(source))) == 1
    assert len(exact.encode("utf-8")) == 256 * 1024

    fake_pdf(monkeypatch, [_block(exact + "a")])
    with pytest.raises(ProcessingFailure) as raised:
        handler.process(context(source))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def _serialized_size(units) -> int:
    payloads = []
    for index, unit in enumerate(units):
        payloads.append(
            {
                "unit_index": index,
                "unit_type": unit.unit_type,
                "source_ref": unit.source_ref,
                "content_text": unit.content_text,
                "table_data": unit.table_data,
                "metadata": unit.metadata,
            }
        )
    return len(json.dumps(payloads, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_total_serialized_result_exact_limit_and_one_byte_over(monkeypatch, tmp_path):
    cap = 25 * 1024 * 1024
    source = tmp_path / "result.pdf.part"
    source.touch()
    text_size = 256 * 1024
    blocks = [_block("a" * text_size, index) for index in range(99)] + [_block("a", 99)]
    # Source references and JSON framing count toward the result limit. Reduce
    # the last block by that measured overhead so the serialized list is exact.
    fake_pdf(monkeypatch, blocks)
    trial = PdfTextExtractionHandler(settings()).process(context(source))
    overhead = _serialized_size(trial) - sum(len(unit.content_text.encode("utf-8")) for unit in trial)
    last_text_size = cap - 99 * text_size - overhead
    assert 0 < last_text_size <= text_size
    blocks[-1] = _block("a" * last_text_size, 99)
    fake_pdf(monkeypatch, blocks)

    units = PdfTextExtractionHandler(
        settings(tenderwise_max_extraction_result_bytes=cap)
    ).process(context(source))
    assert _serialized_size(units) == cap

    blocks[-1] = _block("a" * (last_text_size + 1), 99)
    fake_pdf(monkeypatch, blocks)
    with pytest.raises(ProcessingFailure) as raised:
        PdfTextExtractionHandler(
            settings(tenderwise_max_extraction_result_bytes=cap)
        ).process(context(source))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_total_result_boundary_counts_non_ascii_text_as_utf8(monkeypatch, tmp_path):
    cap = 25 * 1024 * 1024
    source = tmp_path / "unicode-result.pdf.part"
    source.touch()
    text_size = 256 * 1024
    blocks = [_block("é" * (text_size // 2), index) for index in range(99)] + [_block("a", 99)]
    fake_pdf(monkeypatch, blocks)
    trial = PdfTextExtractionHandler(settings()).process(context(source))
    text_bytes = sum(len(unit.content_text.encode("utf-8")) for unit in trial)
    overhead = _serialized_size(trial) - text_bytes
    last_text_bytes = cap - 99 * text_size - overhead
    last_text = "é" * (last_text_bytes // 2) + ("a" if last_text_bytes % 2 else "")
    assert len(last_text.encode("utf-8")) == last_text_bytes
    blocks[-1] = _block(last_text, 99)
    fake_pdf(monkeypatch, blocks)

    units = PdfTextExtractionHandler(settings()).process(context(source))
    assert _serialized_size(units) == cap

    blocks[-1] = _block(last_text + "a", 99)
    fake_pdf(monkeypatch, blocks)
    with pytest.raises(ProcessingFailure) as raised:
        PdfTextExtractionHandler(settings()).process(context(source))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_table_data_counts_toward_exact_and_over_canonical_result_limit(monkeypatch, tmp_path):
    cap = 25 * 1024 * 1024
    source = tmp_path / "table-result.pdf.part"
    source.touch()

    def detected_table(value):
        cell = (10.0, 10.0, 600.0, 700.0)
        return _FakeTable((10.0, 10.0, 600.0, 700.0), [[cell]], [[value]])

    fake_pdf(monkeypatch, [], [detected_table("a")])
    trial = PdfTextExtractionHandler(settings()).process(context(source))
    overhead = _serialized_size(trial) - len(trial[0].table_data["rows"][0]["cells"][0]["text"].encode("utf-8"))
    exact_text = "é" * ((cap - overhead) // 2)
    if len(exact_text.encode("utf-8")) < cap - overhead:
        exact_text += "a"
    # Ensure an exact 25 MiB envelope, adjusting by one byte through ASCII.
    if len(exact_text.encode("utf-8")) > cap - overhead:
        exact_text = exact_text[:-1]
        exact_text += "a"
    fake_pdf(monkeypatch, [], [detected_table(exact_text)])
    units = PdfTextExtractionHandler(settings()).process(context(source))
    assert _serialized_size(units) == cap

    fake_pdf(monkeypatch, [], [detected_table(exact_text + "a")])
    with pytest.raises(ProcessingFailure) as raised:
        PdfTextExtractionHandler(settings()).process(context(source))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_dispatch_uses_original_extension_not_staged_part_suffix(tmp_path):
    calls = []

    class PdfHandler:
        def process(self, value):
            calls.append(value)
            return []

    dispatcher = FormatAwareProcessingHandler(PdfHandler())
    staged = tmp_path / "work-random.part"
    pdf_context = context(staged, extension=".pdf", filename="notice.pdf")
    dispatcher.process(pdf_context)
    assert calls == [pdf_context]

    # DOCX now has its own handler; legacy .doc remains unsupported.
    docx_context = context(staged, extension=".doc", filename="annex.doc")
    with pytest.raises(ProcessingFailure) as raised:
        dispatcher.process(docx_context)
    assert raised.value.outcome_code == "unsupported_format"
    assert calls == [pdf_context]

