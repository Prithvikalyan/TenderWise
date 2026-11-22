"""SQLAlchemy models used by the application and Alembic."""

from app.models.company import Company, CompanyDocument, CompanyDocumentCategory, CompanyDocumentVersion
from app.models.tender import Tender, TenderStatus
from app.models.tender_document import DocumentCategory, TenderDocument
from app.models.document_processing import DocumentExtractionUnit, DocumentProcessingRun, DocumentProcessingRunItem

__all__ = [
    "Company",
    "CompanyDocument",
    "CompanyDocumentCategory",
    "CompanyDocumentVersion",
    "DocumentCategory",
    "DocumentExtractionUnit",
    "DocumentProcessingRun",
    "DocumentProcessingRunItem",
    "Tender",
    "TenderDocument",
    "TenderStatus",
]
from app.models.evidence_matching import TenderEvidenceMatchingRun

__all__.append("TenderEvidenceMatchingRun")

from app.models.verification import BidVerificationChecklist, BidVerificationItem

__all__.extend(["BidVerificationChecklist", "BidVerificationItem"])

from app.models.document_extraction_embedding import DocumentExtractionEmbedding

__all__.append("DocumentExtractionEmbedding")

from app.models.milestone import MilestoneCategory, TenderMilestone

__all__.extend(["MilestoneCategory", "TenderMilestone"])

from app.models.retrieval import (  # noqa: F401
    DocumentRetrievalChunk, DocumentRetrievalChunkEmbedding,
    TenderQAConversation, TenderQAMessage, QARetrievalLog,
)
__all__.extend([
    "DocumentRetrievalChunk", "DocumentRetrievalChunkEmbedding",
    "TenderQAConversation", "TenderQAMessage", "QARetrievalLog",
])
