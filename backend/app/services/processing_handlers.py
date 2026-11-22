"""Production format dispatcher for currently implemented extractors."""

from typing import Sequence

from app.services.pdf_extraction import PdfTextExtractionHandler
from app.services.docx_extraction import DocxTextExtractionHandler
from app.services.processing_handler import (
    ExtractionUnitData,
    ProcessingContext,
    ProcessingFailure,
)
from app.services.tabular_extraction import TabularExtractionHandler


class FormatAwareProcessingHandler:
    """Route by the original validated extension, never by the staged path."""

    def __init__(
        self,
        pdf_handler: PdfTextExtractionHandler,
        docx_handler: DocxTextExtractionHandler | None = None,
        tabular_handler: TabularExtractionHandler | None = None,
    ) -> None:
        self.pdf_handler = pdf_handler
        settings = None
        if docx_handler is None or tabular_handler is None:
            settings = _default_settings()
        self.docx_handler = docx_handler or DocxTextExtractionHandler(settings)
        self.tabular_handler = tabular_handler or TabularExtractionHandler(settings)

    def process(self, context: ProcessingContext) -> Sequence[ExtractionUnitData]:
        extension = context.extension.lower()
        if extension == ".pdf":
            return self.pdf_handler.process(context)
        if extension == ".docx":
            return self.docx_handler.process(context)
        if extension in {".xlsx", ".csv"}:
            return self.tabular_handler.process(context)
        raise ProcessingFailure(
            "permanent", "unsupported_format", "This document format is not supported."
        )


def create_production_handler(settings) -> FormatAwareProcessingHandler:
    return FormatAwareProcessingHandler(
        PdfTextExtractionHandler(settings),
        DocxTextExtractionHandler(settings),
        TabularExtractionHandler(settings),
    )


def _default_settings():
    # Preserve the historical one-argument dispatcher constructor for callers/tests.
    from app.core.config import get_settings
    return get_settings()
