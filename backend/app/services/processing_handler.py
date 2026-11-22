"""Extraction contract and safe failure mapping for processing handlers.

The manual worker registers the production format dispatcher when no custom
handler is registered. The dispatcher supports PDF, DOCX, XLSX and CSV extraction.
"""

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Literal, Protocol, Sequence
from uuid import UUID


@dataclass(frozen=True)
class ExtractionUnitData:
    unit_type: Literal["text", "table", "ocr_text"]
    source_ref: dict
    content_text: str | None = None
    table_data: dict | list | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessingContext:
    document_id: UUID
    source_path: Path
    cancellation_requested: Event
    original_filename: str = ""
    extension: str = ""


class ProcessingHandler(Protocol):
    """Future handlers must check cancellation between bounded operations.

    Implementations must not start unbounded parser, network, or subprocess
    operations. The worker signals cancellation but cannot safely stop a Python
    thread; deployment supervisors must enforce the process shutdown deadline.
    A handler returning after cancellation is discarded by the worker.
    """

    def process(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]: ...


@dataclass(frozen=True)
class ProcessingFailure(Exception):
    """Classified processing failure; ``safe_message`` is never persisted."""

    failure_class: Literal["retryable", "permanent"]
    outcome_code: str
    safe_message: str


SAFE_FAILURE_MESSAGES = {
    "unsupported_format": "This document format is not supported.",
    "source_unavailable": "The source document is unavailable or unreadable.",
    "unsafe_storage_key": "The source document cannot be accessed safely.",
    "invalid_document_metadata": "The document metadata is invalid.",
    "source_size_mismatch": "The source document failed an integrity check.",
    "resource_limit_exceeded": "The document exceeds the processing size limit.",
    "source_hash_mismatch": "The source document failed an integrity check.",
    "empty_file": "The source document is empty.",
    "extension_signature_mismatch": "The document content does not match its file type.",
    "malformed_file": "The document is malformed or incomplete.",
    "invalid_document": "The document could not be read.",
    "extraction_failed": "Document processing could not be completed.",
    "source_deleted": "The source document is no longer available.",
    "processing_error": "Document processing could not be completed.",
    "processing_timeout": "Document processing exceeded its time limit.",
    "worker_shutdown": "Processing will resume when a worker is available.",
    "database_error": "Processing could not be saved temporarily.",
    "temporary_io": "The source document could not be read temporarily.",
    "parser_error": "The document could not be read.",
    "attempt_limit_exceeded": "Processing could not be completed after repeated attempts.",
}


def safe_failure_details(failure: ProcessingFailure) -> tuple[str, str, str]:
    """Return allowlisted failure fields suitable for persistence/API output."""
    failure_class = failure.failure_class
    if failure_class not in ("retryable", "permanent"):
        failure_class = "permanent"
    outcome_code = failure.outcome_code
    if not isinstance(outcome_code, str) or outcome_code not in SAFE_FAILURE_MESSAGES:
        outcome_code = "extraction_failed"
    return failure_class, outcome_code, SAFE_FAILURE_MESSAGES[outcome_code]


_production_handler: ProcessingHandler | None = None


def register_production_handler(handler: ProcessingHandler) -> None:
    """Register a real extraction handler from a future application module."""
    global _production_handler
    _production_handler = handler


def get_production_handler() -> ProcessingHandler | None:
    return _production_handler
