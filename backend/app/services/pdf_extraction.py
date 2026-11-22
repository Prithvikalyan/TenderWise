"""All-or-nothing PDF text and table extraction.

PyMuPDF and pdfplumber materialize page-level parsing structures. The output
limits bound the canonical logical result, not peak parser memory for a page.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pdfplumber
import pymupdf
from pdfminer.pdfdocument import PDFException
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import PSEOF
from pdfplumber.utils.exceptions import PdfminerException

from app.services.processing_handler import ExtractionUnitData, ProcessingContext, ProcessingFailure


@dataclass(frozen=True)
class _TextBlock:
    block_index: int
    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class _TableRecord:
    bbox: tuple[float, float, float, float]
    rows: list[dict]
    cell_bboxes: tuple[tuple[float, float, float, float], ...]
    signature: tuple
    table_index: int = -1


@dataclass(frozen=True)
class _PageUnit:
    top: float
    left: float
    kind_order: int
    source_order: int
    unit: ExtractionUnitData


class PdfTextExtractionHandler:
    """Extract PyMuPDF text blocks and pdfplumber tables page by page."""

    def __init__(self, settings) -> None:
        self.max_pages = settings.tenderwise_max_pdf_pages
        self.max_result_bytes = settings.tenderwise_max_extraction_result_bytes
        self.max_text_unit_bytes = settings.tenderwise_max_extraction_text_unit_bytes

    def process(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]:
        if context.extension != ".pdf":
            raise ProcessingFailure(
                "permanent", "unsupported_format", "This document format is not supported."
            )
        if context.cancellation_requested.is_set():
            raise _cancelled()

        try:
            document = pymupdf.open(str(context.source_path), filetype="pdf")
        except OSError as exc:
            raise _temporary_io() from exc
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, ValueError, RuntimeError) as exc:
            raise _malformed() from exc

        units: list[ExtractionUnitData] = []
        serialized_bytes = 2  # Canonical JSON array brackets, including empty output.
        try:
            with document:
                page_count = document.page_count
                if page_count > self.max_pages:
                    raise _resource_limit()

                version = str(pymupdf.VersionBind)
                # Check the approved limit before opening/processing pdfplumber pages.
                with pdfplumber.open(str(context.source_path)) as pdf:
                    if len(pdf.pages) != page_count:
                        raise _malformed()
                    for page_index in range(page_count):
                        if context.cancellation_requested.is_set():
                            raise _cancelled()
                        try:
                            mupdf_page = document.load_page(page_index)
                            plumber_page = pdf.pages[page_index]
                            blocks = _get_text_blocks(mupdf_page)
                            tables = _find_tables(
                                plumber_page, cancellation_requested=context.cancellation_requested
                            )
                        except OSError as exc:
                            raise _temporary_io() from exc
                        except ProcessingFailure:
                            raise
                        except (PDFException, PDFSyntaxError, PSEOF, PdfminerException, pymupdf.FileDataError,
                                ValueError, RuntimeError, IndexError, TypeError) as exc:
                            raise _malformed() from exc

                        aligned = _coordinates_aligned(mupdf_page, plumber_page)
                        suppressed_words: set[tuple[int, int, int]] = set()
                        words_by_block: dict[int, list[tuple]] = {}
                        if aligned and tables:
                            if context.cancellation_requested.is_set():
                                raise _cancelled()
                            words = _get_words(mupdf_page)
                            suppressed_words = _table_owned_words(words, tables)
                            for word in words:
                                if len(word) >= 8:
                                    words_by_block.setdefault(int(word[5]), []).append(word)

                        page_units: list[_PageUnit] = []
                        for block in blocks:
                            if context.cancellation_requested.is_set():
                                raise _cancelled()
                            retained = _retained_block(
                                block, suppressed_words, aligned, words_by_block
                            )
                            if retained is None:
                                continue
                            text, bbox, fragment_index = retained
                            if len(text.encode("utf-8")) > self.max_text_unit_bytes:
                                raise _resource_limit()
                            unit = ExtractionUnitData(
                                unit_type="text",
                                source_ref={
                                    "document_id": str(context.document_id),
                                    "filename": context.original_filename,
                                    "page": page_index + 1,
                                    "block_index": block.block_index,
                                    "bbox": list(bbox),
                                    **({"fragment_index": fragment_index} if fragment_index is not None else {}),
                                },
                                content_text=text,
                                table_data=None,
                                metadata={
                                    "format": "pdf",
                                    "extractor": "pymupdf",
                                    "extractor_version": version,
                                },
                            )
                            page_units.append(
                                _PageUnit(bbox[1], bbox[0], 0, block.block_index, unit)
                            )

                        for table in tables:
                            if context.cancellation_requested.is_set():
                                raise _cancelled()
                            unit = ExtractionUnitData(
                                unit_type="table",
                                source_ref={
                                    "document_id": str(context.document_id),
                                    "filename": context.original_filename,
                                    "page": page_index + 1,
                                    "table_index": table.table_index,
                                    "bbox": list(table.bbox),
                                },
                                content_text=None,
                                table_data={"column_count": _column_count(table.rows), "rows": table.rows},
                                metadata={
                                    "format": "pdf",
                                    "extractor": "pdfplumber",
                                    "extractor_version": pdfplumber.__version__,
                                    "overlap_alignment_verified": aligned,
                                },
                            )
                            page_units.append(
                                _PageUnit(table.bbox[1], table.bbox[0], 1, table.table_index, unit)
                            )

                        page_units.sort(
                            key=lambda entry: (
                                entry.top,
                                entry.left,
                                entry.kind_order,
                                entry.source_order,
                            )
                        )
                        for entry in page_units:
                            index = len(units)
                            serialized_unit = _serialized_unit_bytes(entry.unit, index)
                            next_size = serialized_bytes + serialized_unit + (1 if index else 0)
                            if next_size > self.max_result_bytes:
                                raise _resource_limit()
                            units.append(entry.unit)
                            serialized_bytes = next_size
            return units
        except ProcessingFailure:
            raise
        except OSError as exc:
            raise _temporary_io() from exc
        except (PDFException, PDFSyntaxError, PSEOF, PdfminerException, pymupdf.FileDataError,
                ValueError, RuntimeError, IndexError, TypeError, KeyError) as exc:
            raise _malformed() from exc


def _get_text_blocks(page) -> list[_TextBlock]:
    blocks = []
    for block in page.get_text("blocks", sort=False):
        if len(block) < 7 or block[6] != 0:
            continue
        text = block[4]
        if not isinstance(text, str) or not text.strip():
            continue
        blocks.append(
            _TextBlock(
                int(block[5]),
                text,
                tuple(float(value) for value in block[:4]),
            )
        )
    return blocks


def _find_tables(page, cancellation_requested=None) -> list[_TableRecord]:
    detected = []
    found = page.find_tables()
    tables = found.tables if hasattr(found, "tables") else found
    for table in tables:
        if cancellation_requested is not None and cancellation_requested.is_set():
            raise _cancelled()
        bbox = tuple(float(value) for value in table.bbox)
        if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
            raise _malformed()
        extracted = table.extract()
        source_rows = table.rows
        count = max(len(source_rows), len(extracted))
        rows = []
        cells = []
        for row_index in range(count):
            source_row = source_rows[row_index] if row_index < len(source_rows) else None
            extracted_row = extracted[row_index] if row_index < len(extracted) else []
            row_cells = []
            for cell_index in range(max(len(source_row.cells) if source_row else 0, len(extracted_row))):
                raw_value = extracted_row[cell_index] if cell_index < len(extracted_row) else None
                value = "" if raw_value is None else str(raw_value)
                row_cells.append({"cell_index": cell_index, "text": value})
                cell_bbox = source_row.cells[cell_index] if source_row and cell_index < len(source_row.cells) else None
                if cell_bbox is not None and len(cell_bbox) == 4:
                    normalized_bbox = tuple(float(value) for value in cell_bbox)
                    if all(math.isfinite(value) for value in normalized_bbox):
                        cells.append(normalized_bbox)
            rows.append({"row_index": row_index, "cells": row_cells})
        column_count = max((len(row["cells"]) for row in rows), default=0)
        for row in rows:
            while len(row["cells"]) < column_count:
                row["cells"].append({"cell_index": len(row["cells"]), "text": ""})
        signature = (
            bbox,
            tuple(tuple(cell["text"] for cell in row["cells"]) for row in rows),
            tuple(cells),
        )
        detected.append(_TableRecord(bbox, rows, tuple(cells), signature))

    detected.sort(key=lambda item: (item.bbox[1], item.bbox[0], item.bbox[3], item.bbox[2], item.signature))
    unique: list[_TableRecord] = []
    seen = set()
    for table in detected:
        if table.signature in seen:
            continue
        seen.add(table.signature)
        unique.append(table)
    return [
        _TableRecord(table.bbox, table.rows, table.cell_bboxes, table.signature, index)
        for index, table in enumerate(unique)
    ]


def _column_count(rows: list[dict]) -> int:
    return max((len(row["cells"]) for row in rows), default=0)


def _coordinates_aligned(mupdf_page, plumber_page, tolerance: float = 0.5) -> bool:
    """Only compare boxes for unrotated pages with matching full-page boxes."""
    if mupdf_page.rotation != 0:
        return False
    crop = mupdf_page.cropbox
    media = mupdf_page.mediabox
    values = (crop.x0, crop.y0, media.x0, media.y0)
    if any(abs(value) > tolerance for value in values):
        return False
    if any(
        abs(left - right) > tolerance
        for left, right in (
            (crop.width, media.width),
            (crop.height, media.height),
            (crop.width, plumber_page.width),
            (crop.height, plumber_page.height),
            (plumber_page.bbox[0], 0.0),
            (plumber_page.bbox[1], 0.0),
            (plumber_page.bbox[2], plumber_page.width),
            (plumber_page.bbox[3], plumber_page.height),
        )
    ):
        return False
    return True


def _get_words(page) -> list[tuple]:
    return list(page.get_text("words", sort=False))


def _inside_bbox(word: tuple, bbox: tuple[float, float, float, float]) -> bool:
    center_x = (float(word[0]) + float(word[2])) / 2
    center_y = (float(word[1]) + float(word[3])) / 2
    return bbox[0] <= center_x < bbox[2] and bbox[1] <= center_y < bbox[3]


def _table_owned_words(words: list[tuple], tables: list[_TableRecord]) -> set[tuple[int, int, int]]:
    cell_boxes = [bbox for table in tables for bbox in table.cell_bboxes]
    return {
        (int(word[5]), int(word[6]), int(word[7]))
        for word in words
        if len(word) >= 8 and any(_inside_bbox(word, cell) for cell in cell_boxes)
    }


def _retained_block(
    block: _TextBlock,
    suppressed_words: set[tuple[int, int, int]],
    alignment_verified: bool,
    words_by_block: dict[int, list[tuple]],
) -> tuple[str, tuple[float, float, float, float], int | None] | None:
    if not alignment_verified or not suppressed_words:
        return block.text, block.bbox, None
    # Inspect words only for blocks that intersect any suppressed word's block
    # number. This keeps the existing block text unchanged otherwise.
    if not any(block_id == block.block_index for block_id, _line_id, _word_id in suppressed_words):
        return block.text, block.bbox, None
    words = words_by_block.get(block.block_index, [])
    kept = [
        word
        for word in words
        if (int(word[5]), int(word[6]), int(word[7])) not in suppressed_words
    ]
    if not kept:
        return None
    lines: dict[int, list[tuple]] = {}
    for word in kept:
        lines.setdefault(int(word[6]), []).append(word)
    text = "\n".join(
        " ".join(str(word[4]) for word in sorted(lines[line_index], key=lambda item: int(item[7])))
        for line_index in sorted(lines)
    )
    bbox = (
        min(float(word[0]) for word in kept),
        min(float(word[1]) for word in kept),
        max(float(word[2]) for word in kept),
        max(float(word[3]) for word in kept),
    )
    return text, bbox, 0


def _serialized_unit_bytes(unit: ExtractionUnitData, unit_index: int) -> int:
    """Count one unit in the canonical logical result envelope.

    The result is a compact UTF-8 JSON array of unit objects with
    ``unit_index``, ``unit_type``, ``source_ref``, ``content_text``,
    ``table_data``, and ``metadata`` fields. This logical size is independent
    of the database driver's physical serialization.
    """
    payload = {
        "unit_index": unit_index,
        "unit_type": unit.unit_type,
        "source_ref": unit.source_ref,
        "content_text": unit.content_text,
        "table_data": unit.table_data,
        "metadata": unit.metadata,
    }
    return len(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    )


def _resource_limit() -> ProcessingFailure:
    return ProcessingFailure(
        "permanent", "resource_limit_exceeded", "The document exceeds the processing size limit."
    )


def _malformed() -> ProcessingFailure:
    return ProcessingFailure("permanent", "malformed_file", "The document is malformed or incomplete.")


def _temporary_io() -> ProcessingFailure:
    return ProcessingFailure(
        "retryable", "temporary_io", "The source document could not be read temporarily."
    )


def _cancelled() -> ProcessingFailure:
    return ProcessingFailure(
        "retryable", "worker_shutdown", "Processing will resume when a worker is available."
    )
