"""Structure-aware retrieval chunking with stable source provenance."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable
from uuid import UUID

from app.models.document_processing import DocumentExtractionUnit
from app.models.retrieval import DocumentRetrievalChunk

CHUNKER_VERSION = "structure-v1"
DEFAULT_TARGET_TOKENS = 600
DEFAULT_OVERLAP_TOKENS = 80
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z0-9(\[])" )


def estimate_tokens(text: str) -> int:
    """A deterministic, dependency-free token estimate used for chunk limits."""
    return len(_TOKEN_RE.findall(text))


def _cell_text(cell: Any) -> str:
    if isinstance(cell, dict):
        value = cell.get("text")
        if value is None:
            value = cell.get("value")
        if value is None:
            value = cell.get("cached_value")
        if value is None:
            value = cell.get("formula")
        return "" if value is None else str(value).strip()
    return "" if cell is None else str(cell).strip()


def _table_rows(table_data: Any) -> list[tuple[int | None, list[str]]]:
    if not isinstance(table_data, dict) or not isinstance(table_data.get("rows"), list):
        return []
    result = []
    for row in table_data["rows"]:
        if not isinstance(row, dict):
            continue
        raw_cells = row.get("cells", [])
        if not isinstance(raw_cells, list):
            continue
        cells = [_cell_text(cell) for cell in raw_cells]
        if any(cells):
            index = row.get("row_index")
            result.append((index if isinstance(index, int) else None, cells))
    return result


def _is_heading(text: str, source_ref: dict[str, Any]) -> bool:
    if source_ref.get("heading_level"):
        return True
    value = " ".join(text.strip().split())
    if not value or len(value) > 110 or estimate_tokens(value) > 16:
        return False
    if value.endswith((".", ";", ",")):
        return False
    letters = [char for char in value if char.isalpha()]
    if letters and sum(char.isupper() for char in letters) / len(letters) >= 0.75:
        return True
    return bool(re.match(r"^(section|schedule|chapter|annexure|appendix|eligibility|scope of work|technical specifications|commercial terms)\b", value, re.I))


def _word_windows(text: str, target: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if estimate_tokens(text) <= target:
        return [text]
    windows: list[str] = []
    start = 0
    while start < len(words):
        end = start
        current: list[str] = []
        while end < len(words):
            candidate = " ".join(current + [words[end]])
            if current and estimate_tokens(candidate) > target:
                break
            current.append(words[end])
            end += 1
        windows.append(" ".join(current))
        if end >= len(words):
            break
        # Overlap uses a conservative word approximation to retain context.
        start = max(start + 1, end - max(1, overlap // 1))
    return windows


def _merge_refs(refs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        key = repr(sorted(ref.items(), key=lambda item: item[0]))
        if key not in seen:
            unique.append(ref)
            seen.add(key)
    return unique


def build_retrieval_chunks(
    *,
    tender_id: UUID,
    document_id: UUID,
    run_item_id: UUID,
    units: list[DocumentExtractionUnit],
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[DocumentRetrievalChunk]:
    """Convert extraction blocks into page/section-aware text and table chunks.

    PDF text is grouped within its page; DOCX paragraphs retain explicit heading
    style metadata; spreadsheet/PDF/DOCX tables are chunked by rows while
    preserving their column headings and source metadata.
    """
    if target_tokens < 1:
        raise ValueError("target_tokens must be positive")
    # Prevent overlap from approaching the whole window; that would create
    # excessive duplicate chunks when a caller supplies aggressive settings.
    effective_overlap = min(max(0, overlap_tokens), max(0, target_tokens // 4))
    chunks: list[DocumentRetrievalChunk] = []
    chunk_index = 0
    text_groups: dict[tuple[Any, ...], list[DocumentExtractionUnit]] = defaultdict(list)
    ordered_units = sorted(units, key=lambda unit: unit.unit_index)

    for unit in ordered_units:
        source_ref = unit.source_ref or {}
        if unit.unit_type == "table":
            rows = _table_rows(unit.table_data)
            if not rows:
                continue
            header_index, header_cells = rows[0]
            header_text = " | ".join(header_cells)
            current_rows: list[tuple[int | None, list[str]]] = []
            for row in rows[1:] if len(rows) > 1 else rows:
                candidate_rows = current_rows + [row]
                table_body = "\n".join(" | ".join(cells) for _idx, cells in candidate_rows)
                candidate = f"Table columns: {header_text}\n{table_body}" if header_text else table_body
                if current_rows and estimate_tokens(candidate) > target_tokens:
                    content = f"Table columns: {header_text}\n" + "\n".join(" | ".join(cells) for _idx, cells in current_rows)
                    refs = [dict(source_ref)]
                    if current_rows[0][0] is not None:
                        refs[0]["row_start"] = current_rows[0][0]
                        refs[0]["row_end"] = current_rows[-1][0] if current_rows[-1][0] is not None else current_rows[0][0]
                    chunks.append(_chunk_row(
                        tender_id, document_id, run_item_id, chunk_index,
                        "table", None, content, refs, [unit.id],
                    ))
                    chunk_index += 1
                    # Include one preceding row as overlap for table continuity.
                    current_rows = current_rows[-1:]
                current_rows.append(row)
            if current_rows:
                content = f"Table columns: {header_text}\n" + "\n".join(" | ".join(cells) for _idx, cells in current_rows)
                refs = [dict(source_ref)]
                if current_rows[0][0] is not None:
                    refs[0]["row_start"] = current_rows[0][0]
                    refs[0]["row_end"] = current_rows[-1][0] if current_rows[-1][0] is not None else current_rows[0][0]
                chunks.append(_chunk_row(
                    tender_id, document_id, run_item_id, chunk_index,
                    "table", None, content, refs, [unit.id],
                ))
                chunk_index += 1
            continue

        content = (unit.content_text or "").strip()
        if not content:
            continue
        # Keep PDF pages separate so each source viewer link lands on a page
        # containing the passage. DOCX paragraphs without pages remain in order.
        key = (source_ref.get("page"), source_ref.get("worksheet_name"), source_ref.get("format"))
        text_groups[key].append(unit)

    for _group_key, group_units in text_groups.items():
        current_heading: str | None = None
        segments: list[tuple[str, dict[str, Any], UUID, str | None]] = []
        for unit in group_units:
            source_ref = dict(unit.source_ref or {})
            raw_text = (unit.content_text or "").strip()
            if not raw_text:
                continue
            if _is_heading(raw_text, source_ref):
                current_heading = " ".join(raw_text.split())
                # The heading is carried as metadata and repeated as context
                # in following passage text; avoid creating a heading-only chunk.
                continue
            enriched = f"{current_heading}\n{raw_text}" if current_heading else raw_text
            segments.append((enriched, source_ref, unit.id, current_heading))

        buffer_parts: list[str] = []
        buffer_refs: list[dict[str, Any]] = []
        buffer_unit_ids: list[UUID] = []
        buffer_heading: str | None = None

        def flush_buffer() -> None:
            nonlocal chunk_index, buffer_parts, buffer_refs, buffer_unit_ids, buffer_heading
            if not buffer_parts:
                return
            content = "\n\n".join(buffer_parts).strip()
            refs = _merge_refs(buffer_refs)
            chunks.append(_chunk_row(
                tender_id, document_id, run_item_id, chunk_index,
                "text", buffer_heading, content, refs, list(dict.fromkeys(buffer_unit_ids)),
            ))
            chunk_index += 1
            buffer_parts = []
            buffer_refs = []
            buffer_unit_ids = []
            buffer_heading = None

        for text, source_ref, unit_id, segment_heading in segments:
            if buffer_parts and segment_heading != buffer_heading:
                flush_buffer()
            parts = _word_windows(text, target_tokens, effective_overlap)
            for part in parts:
                candidate = "\n\n".join(buffer_parts + [part])
                if buffer_parts and estimate_tokens(candidate) > target_tokens:
                    # Long individual units already receive overlap in
                    # _word_windows. Do not add a second overlap here, which
                    # could exceed the target size when paragraphs are merged.
                    flush_buffer()
                buffer_parts.append(part)
                buffer_refs.append(source_ref)
                buffer_unit_ids.append(unit_id)
                if buffer_heading is None:
                    buffer_heading = segment_heading
        flush_buffer()

    return chunks


def _chunk_row(
    tender_id: UUID,
    document_id: UUID,
    run_item_id: UUID,
    chunk_index: int,
    chunk_type: str,
    heading: str | None,
    content: str,
    source_refs: list[dict[str, Any]],
    source_unit_ids: list[UUID],
) -> DocumentRetrievalChunk:
    clean = content.strip()
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    return DocumentRetrievalChunk(
        tender_id=tender_id,
        document_id=document_id,
        run_item_id=run_item_id,
        chunk_index=chunk_index,
        chunk_type=chunk_type,
        section_heading=heading,
        content_text=clean,
        source_ref=source_refs[0] if source_refs else {},
        source_refs=source_refs,
        source_unit_ids=[str(value) for value in source_unit_ids],
        token_count=estimate_tokens(clean),
        content_sha256=digest,
        chunker_version=CHUNKER_VERSION,
    )
