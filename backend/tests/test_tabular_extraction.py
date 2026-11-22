from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from app.core.config import Settings
from app.services.processing_handler import ProcessingContext, ProcessingFailure
from app.services.processing_handlers import FormatAwareProcessingHandler
from app.services.pdf_extraction import PdfTextExtractionHandler, _serialized_unit_bytes
from app.services.tabular_extraction import (
    MAX_CSV_COLUMNS,
    MAX_CSV_ROWS,
    MAX_XLSX_NONEMPTY_CELLS,
    MAX_XLSX_WORKSHEETS,
    TabularExtractionHandler,
)


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def context(path: Path, *, extension=None, filename=None):
    extension = extension or path.suffix
    filename = filename or path.name
    return ProcessingContext(uuid4(), path, threading.Event(), filename, extension)


def save_workbook(path, build):
    workbook = Workbook()
    build(workbook)
    workbook.save(path)
    return path


def test_xlsx_worksheets_sparse_cells_and_source_references(tmp_path):
    def build(workbook):
        first = workbook.active
        first.title = "Prices"
        first["A1"] = "Item"
        first["C1"] = "Rate"
        first["A3"] = "Bolt"
        first["C3"] = 1.25
        workbook.create_sheet("Empty")
        third = workbook.create_sheet("Unicode")
        third["B2"] = "नमस्ते 🌏"

    path = save_workbook(tmp_path / "sparse.xlsx", build)
    units = TabularExtractionHandler(settings()).process(context(path))
    assert [unit.source_ref["worksheet_name"] for unit in units] == ["Prices", "Empty", "Unicode"]
    assert [unit.source_ref["worksheet_index"] for unit in units] == [0, 1, 2]
    assert units[0].source_ref["cell_range"] == "A1:C3"
    assert units[0].table_data["column_count"] == 3
    assert units[0].table_data["rows"][0]["row_index"] == 0
    assert units[0].table_data["rows"][0]["cells"][1] == {
        "cell_index": 1, "coordinate": "B1", "data_type": "empty", "value": None
    }
    assert units[0].table_data["rows"][1]["row_index"] == 2
    assert units[1].table_data == {"column_count": 0, "rows": []}
    assert units[2].table_data["rows"][0]["cells"][1]["value"] == "नमस्ते 🌏"
    assert all(unit.source_ref["document_id"] for unit in units)


def _inject_cached_formula_value(path: Path, coordinate: str, value: str) -> None:
    temporary = path.with_suffix(".rewritten.xlsx")
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as source, ZipFile(temporary, "w", ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(payload)
                cell = root.find(f".//x:c[@r='{coordinate}']", namespace)
                assert cell is not None
                cached = cell.find("x:v", namespace)
                assert cached is not None
                cached.text = value
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, payload)
    temporary.replace(path)


def test_xlsx_formula_cached_and_unavailable_values_are_separate(tmp_path):
    path = save_workbook(
        tmp_path / "formulas.xlsx",
        lambda workbook: (
            setattr(workbook.active, "title", "BOQ"),
            setattr(workbook.active["A1"], "value", 3),
            setattr(workbook.active["B1"], "value", 4),
            setattr(workbook.active["C1"], "value", "=SUM(A1:B1)"),
            setattr(workbook.active["D1"], "value", "=A1*B1"),
        ),
    )
    _inject_cached_formula_value(path, "C1", "7")
    unit = TabularExtractionHandler(settings()).process(context(path))[0]
    first_formula, second_formula = unit.table_data["rows"][0]["cells"][2:]
    assert first_formula == {
        "cell_index": 2,
        "coordinate": "C1",
        "data_type": "formula",
        "formula": "=SUM(A1:B1)",
        "cached_value": 7,
        "cached_value_available": True,
    }
    assert second_formula["formula"] == "=A1*B1"
    assert second_formula["cached_value"] is None
    assert second_formula["cached_value_available"] is False


def test_xlsx_extracts_from_worker_staged_part_path_using_original_extension(tmp_path):
    source = save_workbook(
        tmp_path / "staged-source.xlsx",
        lambda workbook: (
            setattr(workbook.active["A1"], "value", "=1+1"),
        ),
    )
    _inject_cached_formula_value(source, "A1", "2")
    staged = tmp_path / "work-random.part"
    staged.write_bytes(source.read_bytes())
    units = TabularExtractionHandler(settings()).process(
        context(staged, extension=".xlsx", filename="calculation.xlsx")
    )
    formula = units[0].table_data["rows"][0]["cells"][0]
    assert formula["formula"] == "=1+1"
    assert formula["cached_value"] == 2
    assert units[0].source_ref["filename"] == "calculation.xlsx"


def test_xlsx_merged_cells_do_not_duplicate_values_and_boq_is_unmodified(tmp_path):
    def build(workbook):
        sheet = workbook.active
        sheet.title = "BOQ"
        sheet.append(["Item", "Description", "Unit", "Qty", "Rate", "Amount"])
        sheet.append(["01", "Galvanized bolt", "Nos", 4, 12.5, "=D2*E2"])
        sheet.merge_cells("B2:B3")
        sheet["A3"] = "02"
        sheet["C3"] = "Kg"
        sheet["D3"] = 0
        sheet["E3"] = 8.0
        sheet["F3"] = "=D3*E3"

    path = save_workbook(tmp_path / "boq.xlsx", build)
    unit = TabularExtractionHandler(settings()).process(context(path))[0]
    header, item, merged_row = unit.table_data["rows"]
    assert [cell["value"] for cell in header["cells"]] == ["Item", "Description", "Unit", "Qty", "Rate", "Amount"]
    assert [cell.get("value") for cell in item["cells"][:5]] == ["01", "Galvanized bolt", "Nos", 4, 12.5]
    assert item["cells"][5]["formula"] == "=D2*E2"
    assert item["cells"][5]["cached_value_available"] is False
    assert merged_row["cells"][1]["value"] is None
    assert merged_row["cells"][3]["value"] == 0


def test_csv_encodings_quoted_fields_duplicate_headers_and_ragged_rows(tmp_path):
    contents = "Name,Name,Description\r\nJosé,2,\"line one,\nline two\"\r\n,,tail,\r\n"
    path = tmp_path / "quoted.csv"
    path.write_bytes(b"\xef\xbb\xbf" + contents.encode("utf-8"))
    unit = TabularExtractionHandler(settings()).process(context(path))[0]
    rows = unit.table_data["rows"]
    assert unit.metadata["encoding"] == "utf-8-sig"
    assert [cell["value"] for cell in rows[0]["cells"]] == ["Name", "Name", "Description"]
    assert rows[1]["cells"][0]["value"] == "José"
    assert rows[1]["cells"][2]["value"] == "line one,\nline two"
    assert [cell["value"] for cell in rows[2]["cells"]] == ["", "", "tail", ""]
    assert rows[2]["cells"][3]["cell_index"] == 3

    fallback = tmp_path / "legacy.csv"
    fallback.write_bytes("name,city\nAndré,Québec\n".encode("cp1252"))
    fallback_unit = TabularExtractionHandler(settings()).process(context(fallback))[0]
    assert fallback_unit.metadata["encoding"] == "cp1252"
    assert fallback_unit.table_data["rows"][1]["cells"][0]["value"] == "André"


def test_csv_without_headers_preserves_positions_and_empty_fields(tmp_path):
    path = tmp_path / "no-header.csv"
    path.write_text("A,,C\n\n1,2\n", encoding="utf-8")
    unit = TabularExtractionHandler(settings()).process(context(path))[0]
    assert unit.table_data["column_count"] == 3
    assert [row["row_index"] for row in unit.table_data["rows"]] == [0, 1, 2]
    assert [cell["value"] for cell in unit.table_data["rows"][0]["cells"]] == ["A", "", "C"]
    assert unit.table_data["rows"][1]["cells"] == []
    assert [cell["value"] for cell in unit.table_data["rows"][2]["cells"]] == ["1", "2"]


@pytest.mark.parametrize("payload", [b"a,b\n1,\x81\n", b'a,b\n"unfinished,quote\n'])
def test_csv_invalid_encoding_and_malformed_quotes_fail_safely(tmp_path, payload):
    path = tmp_path / "bad.csv"
    path.write_bytes(payload)
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(path))
    assert raised.value.failure_class == "permanent"
    assert raised.value.outcome_code == "malformed_file"
    assert str(path) not in raised.value.safe_message


def test_csv_large_field_and_unicode_utf8_byte_limit(tmp_path):
    path = tmp_path / "large.csv"
    path.write_text("🌏" * 12, encoding="utf-8")
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings(tenderwise_max_extraction_text_unit_bytes=32)).process(context(path))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_csv_and_xlsx_canonical_result_exact_and_over_limit(tmp_path):
    csv_path = tmp_path / "size.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    normal_settings = settings()
    csv_unit = TabularExtractionHandler(normal_settings).process(context(csv_path))[0]
    exact_size = _serialized_unit_bytes(csv_unit, 0) + 2
    assert TabularExtractionHandler(
        settings(tenderwise_max_extraction_result_bytes=exact_size)
    ).process(context(csv_path))
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(
            settings(tenderwise_max_extraction_result_bytes=exact_size - 1)
        ).process(context(csv_path))
    assert raised.value.outcome_code == "resource_limit_exceeded"

    xlsx_path = save_workbook(tmp_path / "size.xlsx", lambda workbook: setattr(workbook.active["A1"], "value", "value"))
    xlsx_unit = TabularExtractionHandler(normal_settings).process(context(xlsx_path))[0]
    exact_size = _serialized_unit_bytes(xlsx_unit, 0) + 2
    assert TabularExtractionHandler(
        settings(tenderwise_max_extraction_result_bytes=exact_size)
    ).process(context(xlsx_path))
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(
            settings(tenderwise_max_extraction_result_bytes=exact_size - 1)
        ).process(context(xlsx_path))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_csv_row_and_column_limit_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.tabular_extraction.MAX_CSV_ROWS", 2)
    monkeypatch.setattr("app.services.tabular_extraction.MAX_CSV_COLUMNS", 2)
    path = tmp_path / "two-rows.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    assert len(TabularExtractionHandler(settings()).process(context(path))[0].table_data["rows"]) == 2
    path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(path))
    assert raised.value.outcome_code == "resource_limit_exceeded"
    path.write_text("a,b,c\n", encoding="utf-8")
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(path))
    assert raised.value.outcome_code == "resource_limit_exceeded"
    assert (MAX_CSV_ROWS, MAX_CSV_COLUMNS) == (250_000, 500)


def test_xlsx_sheet_and_cell_limit_boundaries(tmp_path, monkeypatch):
    path = save_workbook(
        tmp_path / "limits.xlsx",
        lambda workbook: (
            setattr(workbook.active["A1"], "value", "1"),
            workbook.create_sheet("Second").__setitem__("A1", "2"),
        ),
    )
    monkeypatch.setattr("app.services.tabular_extraction.MAX_XLSX_WORKSHEETS", 2)
    monkeypatch.setattr("app.services.tabular_extraction.MAX_XLSX_NONEMPTY_CELLS", 2)
    assert len(TabularExtractionHandler(settings()).process(context(path))) == 2
    assert (MAX_XLSX_WORKSHEETS, MAX_XLSX_NONEMPTY_CELLS) == (100, 250_000)
    path = save_workbook(
        tmp_path / "too-many-sheets.xlsx",
        lambda workbook: [workbook.create_sheet(f"S{i}") for i in range(2)],
    )
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(path))
    assert raised.value.outcome_code == "resource_limit_exceeded"

    path = save_workbook(
        tmp_path / "too-many-cells.xlsx",
        lambda workbook: (
            setattr(workbook.active["A1"], "value", "1"),
            setattr(workbook.active["B1"], "value", "2"),
            setattr(workbook.active["C1"], "value", "3"),
        ),
    )
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(path))
    assert raised.value.outcome_code == "resource_limit_exceeded"


def test_malformed_xlsx_and_late_csv_failure_return_no_partial_units(tmp_path):
    malformed = tmp_path / "broken.xlsx"
    malformed.write_bytes(b"not an xlsx zip")
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(malformed))
    assert raised.value.outcome_code == "malformed_file"

    late_csv = tmp_path / "late-failure.csv"
    late_csv.write_text('first,ok\nsecond,ok\nthird,"broken\n', encoding="utf-8")
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(late_csv))
    assert raised.value.outcome_code == "malformed_file"


def test_xlsx_failure_after_an_earlier_sheet_returns_no_partial_units(tmp_path, monkeypatch):
    path = save_workbook(
        tmp_path / "later-sheet-fails.xlsx",
        lambda workbook: (
            setattr(workbook.active["A1"], "value", "already extracted"),
            setattr(workbook.create_sheet("Later")["A1"], "value", "fails while reading"),
        ),
    )
    real_iterator = __import__("app.services.tabular_extraction", fromlist=["_iter_raw_rows"])._iter_raw_rows

    def fail_on_later_sheet(worksheet, workbook, *, data_only):
        if worksheet.title == "Later":
            raise ValueError("private workbook parser detail")
        yield from real_iterator(worksheet, workbook, data_only=data_only)

    monkeypatch.setattr("app.services.tabular_extraction._iter_raw_rows", fail_on_later_sheet)
    with pytest.raises(ProcessingFailure) as raised:
        TabularExtractionHandler(settings()).process(context(path))
    assert raised.value.outcome_code == "malformed_file"
    assert "private" not in raised.value.safe_message


def test_dispatch_supports_xlsx_and_csv_but_not_xls(tmp_path):
    xlsx = save_workbook(tmp_path / "dispatch.xlsx", lambda workbook: setattr(workbook.active["A1"], "value", "ok"))
    csv = tmp_path / "dispatch.csv"
    csv.write_text("ok\n", encoding="utf-8")
    dispatcher = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings()))
    assert dispatcher.process(context(xlsx))[0].table_data["rows"][0]["cells"][0]["value"] == "ok"
    assert dispatcher.process(context(csv))[0].table_data["rows"][0]["cells"][0]["value"] == "ok"
    with pytest.raises(ProcessingFailure) as raised:
        dispatcher.process(context(csv, extension=".xls", filename="legacy.xls"))
    assert raised.value.outcome_code == "unsupported_format"

