"""All-or-nothing DOCX text and table extraction in XML body order."""

from __future__ import annotations

import re
from typing import Sequence

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.pdf_extraction import _serialized_unit_bytes
from app.services.processing_handler import ExtractionUnitData, ProcessingContext, ProcessingFailure


class DocxTextExtractionHandler:
    """Extract top-level paragraphs and tables in their original body order."""

    def __init__(self, settings) -> None:
        self.max_result_bytes = settings.tenderwise_max_extraction_result_bytes
        self.max_text_unit_bytes = settings.tenderwise_max_extraction_text_unit_bytes

    def process(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]:
        if context.extension.lower() != ".docx":
            raise ProcessingFailure("permanent", "unsupported_format", "This document format is not supported.")
        if context.cancellation_requested.is_set():
            raise _cancelled()
        try:
            document = Document(str(context.source_path))
            version = __import__("docx").__version__
            units: list[ExtractionUnitData] = []
            serialized_bytes = 2
            paragraph_index = 0
            table_index = 0
            for body_order_index, child in enumerate(document.element.body.iterchildren()):
                if context.cancellation_requested.is_set():
                    raise _cancelled()
                if isinstance(child, CT_P):
                    paragraph = Paragraph(child, document)
                    current_paragraph_index = paragraph_index
                    paragraph_index += 1
                    text = paragraph.text
                    style = paragraph.style.name if paragraph.style is not None else None
                    heading_level = _heading_level(paragraph.style)
                    # Retain empty heading paragraphs because their style is structural information.
                    if not text.strip() and heading_level is None:
                        continue
                    if len(text.encode("utf-8")) > self.max_text_unit_bytes:
                        raise _resource_limit()
                    unit = ExtractionUnitData(
                        unit_type="text",
                        source_ref={
                            "document_id": str(context.document_id),
                            "filename": context.original_filename,
                            "paragraph_index": current_paragraph_index,
                            "body_order_index": body_order_index,
                            "paragraph_style": style,
                            **({"heading_level": heading_level} if heading_level is not None else {}),
                        },
                        content_text=text,
                        metadata={"format": "docx", "extractor": "python-docx", "extractor_version": version},
                    )
                elif isinstance(child, CT_Tbl):
                    table = Table(child, document)
                    current_table_index = table_index
                    table_index += 1
                    rows = []
                    # The grid can declare columns that are absent from a ragged
                    # row, so use it as a lower bound for the structured table.
                    column_count = len(table._tbl.tblGrid.gridCol_lst)
                    for row_index, row in enumerate(table.rows):
                        cells = []
                        for cell_index, cell in enumerate(row.cells):
                            value = cell.text
                            cells.append({"cell_index": cell_index, "text": value})
                        column_count = max(column_count, len(cells))
                        rows.append({"row_index": row_index, "cells": cells})
                    unit = ExtractionUnitData(
                        unit_type="table",
                        source_ref={
                            "document_id": str(context.document_id),
                            "filename": context.original_filename,
                            "table_index": current_table_index,
                            "body_order_index": body_order_index,
                        },
                        table_data={"column_count": column_count, "rows": rows},
                        metadata={"format": "docx", "extractor": "python-docx", "extractor_version": version},
                    )
                else:
                    # SectPr and any future body-level constructs have no supported unit form.
                    continue

                index = len(units)
                encoded_size = _serialized_unit_bytes(unit, index)
                next_size = serialized_bytes + encoded_size + (1 if index else 0)
                if next_size > self.max_result_bytes:
                    raise _resource_limit()
                units.append(unit)
                serialized_bytes = next_size
            return units
        except ProcessingFailure:
            raise
        except OSError as exc:
            raise ProcessingFailure("retryable", "temporary_io", "The source document could not be read temporarily.") from exc
        except Exception as exc:
            # Parser internals and paths are intentionally kept out of safe worker/API errors.
            raise _malformed() from exc


def _heading_level(style) -> int | None:
    """Find an explicit outline level or built-in heading level in a style chain."""
    visited: set[str] = set()
    while style is not None:
        style_id = getattr(style, "style_id", None)
        if style_id and style_id not in visited:
            visited.add(style_id)
            # Built-in heading IDs are stable across localized Word installations.
            match = re.fullmatch(r"Heading\s*([1-9])", style_id, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))

        name = getattr(style, "name", None)
        if name:
            match = re.fullmatch(r"Heading\s+([1-9])", name.strip(), flags=re.IGNORECASE)
            if match:
                return int(match.group(1))

        element = getattr(style, "_element", None)
        properties = getattr(element, "pPr", None)
        outline = getattr(properties, "outlineLvl", None)
        if outline is not None:
            try:
                level = int(outline.val) + 1
            except (TypeError, ValueError):
                level = 0
            if 1 <= level <= 9:
                return level
        style = getattr(style, "base_style", None)
    return None


def _resource_limit() -> ProcessingFailure:
    return ProcessingFailure("permanent", "resource_limit_exceeded", "The document exceeds the processing size limit.")


def _malformed() -> ProcessingFailure:
    return ProcessingFailure("permanent", "malformed_file", "The document is malformed or incomplete.")


def _cancelled() -> ProcessingFailure:
    return ProcessingFailure("retryable", "worker_shutdown", "Processing will resume when a worker is available.")
