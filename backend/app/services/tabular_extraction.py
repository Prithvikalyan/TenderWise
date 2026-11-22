"""Incremental XLSX and CSV extraction into structured table units."""

from __future__ import annotations

import codecs
import csv
import io
import json
from datetime import date, datetime, time, timedelta
from itertools import zip_longest
from pathlib import Path
from typing import Iterator, Sequence

from openpyxl import load_workbook
from openpyxl.worksheet._reader import WorkSheetParser
from openpyxl.utils import get_column_letter

from app.services.pdf_extraction import _serialized_unit_bytes
from app.services.processing_handler import ExtractionUnitData, ProcessingContext, ProcessingFailure


MAX_XLSX_WORKSHEETS = 100
MAX_XLSX_NONEMPTY_CELLS = 250_000
MAX_CSV_ROWS = 250_000
MAX_CSV_COLUMNS = 500
CSV_FIELD_CHARACTER_LIMIT = 256 * 1024
_MISSING = object()
csv.field_size_limit(CSV_FIELD_CHARACTER_LIMIT)


class TabularExtractionHandler:
    """Route XLSX and CSV sources to their structured extractors."""

    def __init__(self, settings) -> None:
        self.max_result_bytes = settings.tenderwise_max_extraction_result_bytes
        self.max_text_unit_bytes = settings.tenderwise_max_extraction_text_unit_bytes
        self.csv_fallback_encodings = _parse_fallback_encodings(
            settings.tenderwise_csv_fallback_encodings
        )

    def process(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]:
        extension = context.extension.lower()
        if extension == ".xlsx":
            return self._process_xlsx(context)
        if extension == ".csv":
            return self._process_csv(context)
        raise ProcessingFailure("permanent", "unsupported_format", "This document format is not supported.")

    def _process_xlsx(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]:
        if context.cancellation_requested.is_set():
            raise _cancelled()
        formula_book = cached_book = None
        formula_source = cached_source = None
        try:
            # Two streaming views are needed because openpyxl exposes formulas
            # and cached results through separate data_only modes. The staged
            # path suffix is intentionally opaque (.part), so supply file
            # objects and dispatch by the validated context extension instead.
            formula_source = context.source_path.open("rb")
            cached_source = context.source_path.open("rb")
            formula_book = load_workbook(
                formula_source, read_only=True, data_only=False, keep_links=False
            )
            cached_book = load_workbook(
                cached_source, read_only=True, data_only=True, keep_links=False
            )
            if len(formula_book.worksheets) > MAX_XLSX_WORKSHEETS:
                raise _resource_limit()

            units: list[ExtractionUnitData] = []
            total_size = 2  # Canonical JSON array brackets.
            nonempty_cells = 0
            version = __import__("openpyxl").__version__
            for worksheet_index, (formula_sheet, cached_sheet) in enumerate(
                zip(formula_book.worksheets, cached_book.worksheets)
            ):
                if context.cancellation_requested.is_set():
                    raise _cancelled()
                max_column, sheet_nonempty, min_row, max_row, min_column, max_used_column = (
                    _inspect_worksheet(
                        formula_sheet,
                        formula_book,
                        MAX_XLSX_NONEMPTY_CELLS - nonempty_cells,
                        context,
                    )
                )
                nonempty_cells += sheet_nonempty
                if nonempty_cells > MAX_XLSX_NONEMPTY_CELLS:
                    raise _resource_limit()

                source_ref = {
                    "document_id": str(context.document_id),
                    "filename": context.original_filename,
                    "worksheet_name": formula_sheet.title,
                    "worksheet_index": worksheet_index,
                    "cell_range": (
                        f"{get_column_letter(min_column)}{min_row}:"
                        f"{get_column_letter(max_used_column)}{max_row}"
                        if sheet_nonempty
                        else None
                    ),
                }
                unit = ExtractionUnitData(
                    unit_type="table",
                    source_ref=source_ref,
                    table_data={"column_count": max_column, "rows": []},
                    metadata={"format": "xlsx", "extractor": "openpyxl", "extractor_version": version},
                )
                index = len(units)
                unit_size = _unit_size_with_empty_rows(unit, index)
                rows: list[dict] = []
                if sheet_nonempty:
                    formula_rows = _iter_raw_rows(formula_sheet, formula_book, data_only=False)
                    cached_rows = _iter_raw_rows(cached_sheet, cached_book, data_only=True)
                    for formula_row, cached_row in zip_longest(formula_rows, cached_rows, fillvalue=_MISSING):
                        if context.cancellation_requested.is_set():
                            raise _cancelled()
                        if formula_row is _MISSING or cached_row is _MISSING:
                            raise _malformed()
                        row_index, formula_cells = formula_row
                        cached_row_index, cached_cells = cached_row
                        if row_index != cached_row_index:
                            raise _malformed()
                        cached_by_column = {cell["column"]: cell for cell in cached_cells}
                        has_value = any(cell["value"] is not None for cell in formula_cells)
                        if not has_value:
                            continue
                        cells_by_column = {cell["column"]: cell for cell in formula_cells}
                        cell_records = []
                        for column_index in range(1, max_column + 1):
                            cell = cells_by_column.get(column_index)
                            cached_cell = cached_by_column.get(column_index)
                            cell_ref = f"{get_column_letter(column_index)}{row_index}"
                            if cell is None:
                                record = {
                                    "cell_index": column_index - 1,
                                    "coordinate": cell_ref,
                                    "data_type": "empty",
                                    "value": None,
                                }
                            elif cell["data_type"] == "f":
                                cached_value = cached_cell["value"] if cached_cell else None
                                _check_cell_text(cached_value, self.max_text_unit_bytes)
                                record = {
                                    "cell_index": column_index - 1,
                                    "coordinate": cell_ref,
                                    "data_type": "formula",
                                    "formula": _json_value(cell["value"]),
                                    "cached_value": _json_value(cached_value),
                                    "cached_value_available": cached_value is not None,
                                }
                            else:
                                value = _json_value(cell["value"])
                                _check_cell_text(value, self.max_text_unit_bytes)
                                record = {
                                    "cell_index": column_index - 1,
                                    "coordinate": cell_ref,
                                    "data_type": cell["data_type"],
                                    "value": value,
                                }
                            cell_records.append(record)
                        row_record = {"row_index": row_index - 1, "cells": cell_records}
                        row_size = _json_bytes(row_record)
                        unit_size += row_size + (1 if rows else 0)
                        if total_size + unit_size + (1 if units else 0) > self.max_result_bytes:
                            raise _resource_limit()
                        rows.append(row_record)

                final_unit = ExtractionUnitData(
                    unit_type=unit.unit_type,
                    source_ref=unit.source_ref,
                    table_data={"column_count": max_column, "rows": rows},
                    metadata=unit.metadata,
                )
                if total_size + unit_size + (1 if units else 0) > self.max_result_bytes:
                    raise _resource_limit()
                units.append(final_unit)
                total_size += unit_size + (1 if index else 0)
            return units
        except ProcessingFailure:
            raise
        except OSError as exc:
            raise _temporary_io() from exc
        except Exception as exc:
            raise _malformed() from exc
        finally:
            if formula_book is not None:
                formula_book.close()
            if cached_book is not None:
                cached_book.close()
            if formula_source is not None:
                formula_source.close()
            if cached_source is not None:
                cached_source.close()

    def _process_csv(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]:
        if context.cancellation_requested.is_set():
            raise _cancelled()
        try:
            selected_encoding = _detect_csv_encoding(
                context.source_path,
                self.csv_fallback_encodings,
                context.cancellation_requested,
            )
            unit = ExtractionUnitData(
                unit_type="table",
                source_ref={
                    "document_id": str(context.document_id),
                    "filename": context.original_filename,
                    "row_start": 0,
                    "row_end": None,
                    "column_count": 0,
                },
                table_data={"column_count": 0, "rows": []},
                metadata={"format": "csv", "encoding": selected_encoding},
            )
            unit_size = 0
            rows: list[dict] = []
            max_columns = 0
            with context.source_path.open("rb") as raw:
                text_stream = io.TextIOWrapper(raw, encoding=selected_encoding, errors="strict", newline="")
                reader = csv.reader(text_stream, strict=True)
                for row_index, values in enumerate(reader):
                    if context.cancellation_requested.is_set():
                        raise _cancelled()
                    if row_index >= MAX_CSV_ROWS:
                        raise _resource_limit()
                    if len(values) > MAX_CSV_COLUMNS:
                        raise _resource_limit()
                    cells = []
                    for column_index, value in enumerate(values):
                        _check_cell_text(value, self.max_text_unit_bytes)
                        cells.append({"cell_index": column_index, "value": value})
                    max_columns = max(max_columns, len(values))
                    row_record = {"row_index": row_index, "cells": cells}
                    unit_size += _json_bytes(row_record) + (1 if rows else 0)
                    current_ref = {**unit.source_ref, "row_end": row_index, "column_count": max_columns}
                    current_unit = ExtractionUnitData(
                        unit_type="table",
                        source_ref=current_ref,
                        table_data={"column_count": max_columns, "rows": []},
                        metadata=unit.metadata,
                    )
                    if 2 + _unit_size_with_empty_rows(current_unit, 0) + unit_size > self.max_result_bytes:
                        raise _resource_limit()
                    rows.append(row_record)
            source_ref = {**unit.source_ref, "row_end": len(rows) - 1 if rows else None, "column_count": max_columns}
            result = ExtractionUnitData(
                unit_type="table",
                source_ref=source_ref,
                table_data={"column_count": max_columns, "rows": rows},
                metadata=unit.metadata,
            )
            final_size = _serialized_unit_bytes(result, 0) + 2
            if final_size > self.max_result_bytes:
                raise _resource_limit()
            return [result]
        except ProcessingFailure:
            raise
        except UnicodeDecodeError as exc:
            raise _malformed() from exc
        except csv.Error as exc:
            if "field larger than field limit" in str(exc).lower():
                raise _resource_limit() from exc
            raise _malformed() from exc
        except OSError as exc:
            raise _temporary_io() from exc
        except Exception as exc:
            raise _malformed() from exc


def _inspect_worksheet(worksheet, workbook, remaining_cells: int, context) -> tuple[int, int, int, int, int, int]:
    """Stream once to count populated cells and locate the sparse used range."""
    count = 0
    min_row = min_column = None
    max_row = max_column = 0
    for row_index, cells in _iter_raw_rows(worksheet, workbook, data_only=False):
        if context.cancellation_requested.is_set():
            raise _cancelled()
        for cell in cells:
            if cell["value"] is None:
                continue
            count += 1
            if count > remaining_cells:
                raise _resource_limit()
            column_index = cell["column"]
            min_row = row_index if min_row is None else min(min_row, row_index)
            min_column = column_index if min_column is None else min(min_column, column_index)
            max_row = max(max_row, row_index)
            max_column = max(max_column, column_index)
    return max_column, count, min_row or 0, max_row, min_column or 0, max_column


def _iter_raw_rows(worksheet, workbook, *, data_only: bool) -> Iterator:
    """Yield physical worksheet rows without expanding sparse row gaps.

    The bounded openpyxl 3.1 dependency lets this use its own streaming XML
    converter while avoiding iter_rows() expansion of absent worksheet rows.
    """
    with worksheet._get_source() as source:
        parser = WorkSheetParser(
            source,
            workbook.shared_strings,
            data_only=data_only,
            epoch=workbook.epoch,
            date_formats=workbook._date_formats,
            timedelta_formats=workbook._timedelta_formats,
        )
        yield from parser.parse()


def _parse_fallback_encodings(value: str) -> tuple[str, ...]:
    encodings = []
    for name in value.split(","):
        name = name.strip()
        if not name:
            continue
        canonical = codecs.lookup(name).name
        if canonical not in {"utf-8", "utf-8-sig"} and canonical not in encodings:
            encodings.append(canonical)
    return tuple(encodings)


def _detect_csv_encoding(path: Path, fallback_encodings: tuple[str, ...], cancellation_requested=None) -> str:
    with path.open("rb") as source:
        prefix = source.read(4)
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise _malformed()
    candidates = ("utf-8-sig",) if prefix.startswith(codecs.BOM_UTF8) else ("utf-8-sig", *fallback_encodings)
    for encoding in candidates:
        try:
            decoder = codecs.getincrementaldecoder(encoding)("strict")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    if cancellation_requested is not None and cancellation_requested.is_set():
                        raise _cancelled()
                    decoder.decode(chunk, final=False)
                decoder.decode(b"", final=True)
            return encoding
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise _temporary_io() from exc
    raise _malformed()


def _json_value(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _check_cell_text(value, maximum_bytes: int) -> None:
    if isinstance(value, str) and len(value.encode("utf-8")) > maximum_bytes:
        raise _resource_limit()


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def _unit_size_with_empty_rows(unit: ExtractionUnitData, index: int) -> int:
    return _serialized_unit_bytes(unit, index)


def _resource_limit() -> ProcessingFailure:
    return ProcessingFailure("permanent", "resource_limit_exceeded", "The document exceeds the processing size limit.")


def _malformed() -> ProcessingFailure:
    return ProcessingFailure("permanent", "malformed_file", "The document is malformed or incomplete.")


def _temporary_io() -> ProcessingFailure:
    return ProcessingFailure("retryable", "temporary_io", "The source document could not be read temporarily.")


def _cancelled() -> ProcessingFailure:
    return ProcessingFailure("retryable", "worker_shutdown", "Processing will resume when a worker is available.")
