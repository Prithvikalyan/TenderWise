"""Evidence discovery between tender requirements and reusable company documents.

V1 deliberately performs conservative lexical/category matching. It identifies
candidate documents but never concludes that a bidder is eligible or compliant.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.company import Company, CompanyDocument, CompanyDocumentVersion
from app.models.document_processing import (
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
)
from app.models.evidence_matching import TenderEvidenceMatchingRun
from app.models.tender import Tender
from app.schemas.compliance import ComplianceAnalysisResponse
from app.schemas.eligibility import EligibilityAnalysisResponse
from app.schemas.evidence_matching import (
    CompanyEvidenceCandidate,
    CompanyEvidencePendingVersion,
    CompanyEvidenceUnitReference,
    ExperienceContractGroup,
    ExperienceEvidenceDocument,
    ExperienceEvidenceSummary,
    EvidenceRequirementMatch,
    EvidenceMatchingRunResponse,
    TenderEvidenceCitation,
)


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "can", "for", "from", "has", "have", "in", "into", "is", "it", "its",
    "of", "on", "or", "shall", "should", "such", "that", "the", "their",
    "there", "these", "this", "those", "to", "under", "upon", "was", "were",
    "will", "with", "within", "must", "may", "bidder", "bid", "tender",
    "submit", "submission", "provide", "provided", "proof", "document", "documents",
    "evidence", "requirement", "requirements", "valid", "validity", "copy", "copies",
    "show", "showing", "shall", "minimum", "least", "during", "preceding", "last",
    "completed", "completion", "required", "applicable", "details", "date", "dated",
}

TOKEN_CANONICAL = {
    "turnover": "turnover", "revenue": "turnover", "receipts": "turnover", "receipt": "turnover",
    "income": "turnover", "gross": "turnover",
    "experience": "experience", "experienced": "experience", "executed": "experience",
    "contract": "contract", "contracts": "contract", "project": "project", "projects": "project",
    "works": "work", "work": "work", "installation": "installation", "install": "installation",
    "installed": "installation", "supply": "supply", "supplied": "supply",
    "blacklisting": "blacklisting", "blacklisted": "blacklisting", "debarred": "blacklisting",
    "debarment": "blacklisting", "blacklist": "blacklisting",
    "registration": "registration", "registered": "registration", "incorporation": "registration",
    "gst": "gst", "gstin": "gst", "pan": "pan",
    "licence": "license", "license": "license", "permit": "license", "permitted": "license",
    "audited": "audit", "auditing": "audit", "audit": "audit", "accounts": "account",
    "accounting": "account", "financial": "financial", "finance": "financial",
    "statement": "statement", "statements": "statement", "certified": "certificate",
    "certification": "certificate", "certificates": "certificate", "certificate": "certificate",
    "declaration": "declaration", "declared": "declaration", "declarations": "declaration",
    "purchase": "purchase", "orders": "order", "order": "order", "datasheets": "datasheet",
    "specifications": "specification", "specs": "specification", "guarantees": "guarantee",
    "cheques": "cheque", "drafts": "draft", "deposits": "deposit",
}

# Requirement kind is inferred from the actual requirement and requested evidence,
# not merely the broad agent category (for example, "documents" is too broad).
EVIDENCE_KIND_LABELS = {
    "registration": "Business registration",
    "experience": "Similar work experience",
    "financial": "Financial capacity",
    "declaration": "Non-blacklisting declaration",
    "technical_certification": "Technical certification or licence",
    "product_technical": "Product technical specification",
    "emd": "Earnest money deposit / bid security",
    "administrative_deadline": "Tender timeline / administrative action",
    "generic": "Company evidence",
}

CATEGORY_BY_KIND = {
    "registration": {"business_registration", "gst_certificate"},
    "experience": {"work_order", "purchase_order", "completion_certificate"},
    "financial": {"audited_financial_statements", "turnover_certificate"},
    "declaration": {"non_blacklisting_declaration"},
    "technical_certification": {"technical_certification", "license"},
    # There is no dedicated product-datasheet category yet. Only "other" records
    # whose extracted content explicitly looks like product specification evidence can match.
    "product_technical": {"other"},
    "emd": {"other"},
    "generic": set(),
}

KIND_ANCHORS = {
    "registration": {"registration", "gst", "pan", "incorporation", "business"},
    "experience": {"work", "order", "purchase", "contract", "project", "supply", "installation", "experience"},
    "financial": {"turnover", "audit", "financial", "statement", "account", "revenue", "accountant"},
    "declaration": {"blacklisting", "declaration", "debarment", "affidavit"},
    "technical_certification": {"technical", "certificate", "license", "quality", "certification", "iso"},
    "product_technical": {"datasheet", "specification", "display", "computer", "hardware", "warranty", "product", "touch", "speaker", "processor", "memory", "storage"},
    "emd": {"emd", "earnest", "deposit", "banker", "cheque", "draft", "guarantee", "security"},
}

SYNTHETIC_MARKERS = (
    "sample - not valid", "for software testing only", "fictional entity",
    "fictional test data", "synthetic test data", "not audited",
    "no legal or procurement effect", "not a government-issued certificate",
    "intentionally blank", "not an actual", "not valid procurement evidence",
)

PLACEHOLDER_METADATA = {"string", "null", "none", "n/a", "na", "not provided", "not applicable", "-"}

CONTRACT_REFERENCE_PATTERNS = (
    re.compile(
        r"\b(?:work\s*order|purchase\s*order|contract|order|po|wo)\s*"
        r"(?:reference|ref(?:erence)?|no\.?|number|#)\s*[:#-]?\s*"
        r"([A-Z0-9][A-Z0-9./_-]{2,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b((?:po|wo)[-/][A-Z0-9][A-Z0-9./_-]{2,})",
        re.IGNORECASE,
    ),
)


class TenderOrCompanyNotFoundError(LookupError):
    pass


class ArchivedCompanyError(ValueError):
    pass


def _unit_text(unit: DocumentExtractionUnit) -> str:
    """Return searchable text from text and structured table extraction units.

    XLSX extraction stores ordinary cells under ``value`` and formula cells
    under ``formula``/``cached_value``. Older PDF/DOCX table extractors use
    ``text``. Reading all of these representations is necessary for company
    evidence matching and avoids reporting populated spreadsheets as empty.
    """
    content_text = getattr(unit, "content_text", None)
    if isinstance(content_text, str) and content_text.strip():
        return content_text.strip()

    data = getattr(unit, "table_data", None)
    if not data:
        return ""

    parts: list[str] = []
    rows = data.get("rows", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return ""

    for row in rows:
        cells = row.get("cells", []) if isinstance(row, dict) else row
        if not isinstance(cells, list):
            continue
        values: list[str] = []
        for cell in cells:
            if isinstance(cell, dict):
                value = cell.get("text")
                if value is None or not str(value).strip():
                    if cell.get("data_type") == "formula":
                        # A cached result is the only value suitable for
                        # numeric comparison. Preserve an uncached formula as
                        # explanatory text, never as a calculated amount.
                        value = cell.get("cached_value")
                        if value is None:
                            formula = cell.get("formula")
                            value = f"[formula not calculated: {formula}]" if formula else None
                    else:
                        value = cell.get("value")
                coordinate = cell.get("coordinate")
                if value is not None and str(value).strip():
                    rendered = str(value).strip()
                    values.append(f"{coordinate}: {rendered}" if coordinate else rendered)
            else:
                value = cell
                if value is not None and str(value).strip():
                    values.append(str(value).strip())
        if values:
            parts.append(" | ".join(values))
    return "\n".join(parts)


def _tokens(value: str) -> set[str]:
    raw = re.findall(r"[a-z][a-z0-9]{1,}", value.casefold())
    return {
        TOKEN_CANONICAL.get(token, token)
        for token in raw
        if token not in STOP_WORDS and len(token) > 1
    }


def _clean_metadata_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in PLACEHOLDER_METADATA:
        return None
    return cleaned


def _is_synthetic(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in SYNTHETIC_MARKERS)


def _normalise_contract_reference(value: str | None) -> tuple[str, str] | None:
    """Return display and comparison forms for an explicit reference only."""
    cleaned = _clean_metadata_value(value)
    if not cleaned:
        return None
    display = cleaned.strip().strip(" .,:;#")
    key = re.sub(r"[^A-Z0-9]", "", display.upper())
    if len(key) < 3:
        return None
    return display, key


def _extract_contract_reference(source: dict[str, Any]) -> tuple[str, str] | None:
    version = source["version"]
    metadata_reference = _normalise_contract_reference(version.reference_number)
    if metadata_reference:
        return metadata_reference
    text = source.get("text", "")
    for pattern in CONTRACT_REFERENCE_PATTERNS:
        match = pattern.search(text[:30000])
        if match:
            reference = _normalise_contract_reference(match.group(1))
            if reference:
                return reference
    return None


def _experience_document_payload(source: dict[str, Any]) -> ExperienceEvidenceDocument:
    document = source["document"]
    version = source["version"]
    category = document.category.value if hasattr(document.category, "value") else str(document.category)
    role = "completion_certificate" if category == "completion_certificate" else "order_document"
    return ExperienceEvidenceDocument(
        version_id=version.id,
        document_title=document.title,
        original_filename=version.original_filename,
        category=category,
        version_number=version.version_number,
        evidence_role=role,
        synthetic_test_data=bool(source.get("synthetic")),
    )


def _build_experience_evidence_summary(
    candidates: list[dict[str, Any]],
) -> ExperienceEvidenceSummary:
    """Group matched experience documents by exact explicit contract reference.

    The grouping is deliberately conservative. It never assumes two unreferenced
    documents are different contracts and never decides that a contract is similar
    or properly completed.
    """
    groups: dict[str, dict[str, Any]] = {}
    unreferenced: list[ExperienceEvidenceDocument] = []
    for source in candidates:
        document = source["document"]
        category = document.category.value if hasattr(document.category, "value") else str(document.category)
        if category not in {"work_order", "purchase_order", "completion_certificate"}:
            continue
        document_payload = _experience_document_payload(source)
        reference = _extract_contract_reference(source)
        if reference is None:
            unreferenced.append(document_payload)
            continue
        display_reference, reference_key = reference
        group = groups.setdefault(
            reference_key,
            {"reference_number": display_reference, "documents": [], "has_order_document": False, "has_completion_certificate": False},
        )
        group["documents"].append(document_payload)
        if document_payload.evidence_role == "completion_certificate":
            group["has_completion_certificate"] = True
        else:
            group["has_order_document"] = True

    contract_groups = [
        ExperienceContractGroup(**group)
        for _key, group in sorted(groups.items(), key=lambda entry: entry[0])
    ]
    if contract_groups:
        note = (
            "Documents are grouped only when the same explicit contract/order reference is available. "
            "This grouping does not establish that the work is similar, falls within the tender's look-back period, "
            "or is satisfactorily completed. Review original documents and verify each distinct contract manually."
        )
    elif candidates:
        note = (
            "No explicit contract/order reference could be identified in the matched documents. "
            "They are not counted as distinct contracts. Check the originals and enter reliable reference numbers "
            "in document metadata where appropriate."
        )
    else:
        note = "No experience documents were identified for grouping. This is not a finding that the bidder lacks experience."
    return ExperienceEvidenceSummary(
        distinct_referenced_contract_count=len(contract_groups),
        unreferenced_document_count=len(unreferenced),
        contract_groups=contract_groups,
        unreferenced_documents=unreferenced,
        note=note,
    )


def _infer_evidence_kind(requirement: dict[str, Any]) -> str:
    text = " ".join([
        str(requirement.get("requirement", "")),
        str(requirement.get("category", "")),
        *[str(item) for item in requirement.get("required_evidence", [])],
    ]).casefold()

    # Qualification look-back periods often mention the bid deadline. Classify
    # experience first so "five years preceding the bid deadline" is not mistaken
    # for a tender submission action.
    if any(term in text for term in (
        "similar contract", "similar work", "work order", "purchase order",
        "completion certificate", "supply-and-install", "supply and install",
        "completed at least", "contracts during the five", "preceding the bid deadline",
    )):
        return "experience"
    if any(term in text for term in (
        "bid submission by", "submission deadline", "submit bid before",
        "bid deadline for submission", "last date for submission",
        "clarification deadline", "technical bid opening", "financial bid opening",
    )):
        return "administrative_deadline"
    if any(term in text for term in ("emd", "earnest money", "bid security", "banker's cheque", "bankers cheque", "demand draft", "bid guarantee")):
        return "emd"
    if any(term in text for term in ("blacklist", "blacklisting", "debar", "non-blacklisting", "non blacklisting")):
        return "declaration"
    if any(term in text for term in ("turnover", "annual revenue", "audited financial", "chartered accountant", "annual accounts", "financial statements")):
        return "financial"
    if any(term in text for term in ("registration", "registered business", "incorporation", "gst registration", "pan card", "gst certificate")):
        return "registration"

    # Product attributes can be extracted as individual requirements without
    # repeating the phrase "datasheet" or "technical specification".
    product_attribute_terms = (
        "datasheet", "data sheet", "technical specification", "technical specifications",
        "product specification", "product compliance", "interactive flat-panel",
        "flat panel display", "display", "65-inch", "65 inch", "4k", "uhd",
        "touch point", "simultaneous touch", "built-in speaker", "speakers",
        "mini pc", "processor", "core i5", "ram", "ssd", "solid state drive",
        "wall-mount", "keyboard", "mouse", "three-year warranty", "3-year warranty",
        "on-site warranty", "product warranty",
    )
    if any(term in text for term in product_attribute_terms):
        return "product_technical"
    if any(term in text for term in ("technical certificate", "technical certification", "iso certificate", "quality certification", "operating licence", "operating license")):
        return "technical_certification"

    category = str(requirement.get("category", "")).casefold()
    if category in {"registration"}:
        return "registration"
    if category in {"experience"}:
        return "experience"
    if category in {"financial"}:
        return "financial"
    if category in {"declaration"}:
        return "declaration"
    return "generic"


def _infer_document_kind(document: CompanyDocument, version: CompanyDocumentVersion, text: str) -> str:
    category = document.category.value if hasattr(document.category, "value") else str(document.category)
    fixed = {
        "business_registration": "registration",
        "gst_certificate": "registration",
        "pan_card": "registration",
        "work_order": "experience",
        "purchase_order": "experience",
        "completion_certificate": "experience",
        "audited_financial_statements": "financial",
        "turnover_certificate": "financial",
        "non_blacklisting_declaration": "declaration",
        "technical_certification": "technical_certification",
        "license": "technical_certification",
    }
    if category in fixed:
        return fixed[category]

    # Infer narrowly for documents deliberately categorized as OTHER.
    probe = " ".join([document.title or "", version.original_filename or "", text[:12000]]).casefold()
    if any(term in probe for term in (
        "product datasheet", "product data sheet", "technical specification",
        "technical compliance sheet", "interactive flat-panel", "mini pc",
    )):
        return "product_technical"
    if any(term in probe for term in (
        "earnest money deposit", "emd instrument", "banker's cheque",
        "bankers cheque", "demand draft", "bank guarantee", "fixed deposit receipt",
    )):
        return "emd"
    if any(term in probe for term in ("turnover schedule", "annual turnover", "audited financial statements", "chartered accountant certificate")):
        return "financial"
    if any(term in probe for term in ("non-blacklisting", "non blacklisting", "not been blacklisted", "not been debarred")):
        return "declaration"
    if any(term in probe for term in ("work order", "purchase order", "completion certificate", "similar contract")):
        return "experience"
    if any(term in probe for term in ("gst registration", "registration certificate", "certificate of incorporation", "gstin")):
        return "registration"
    return "generic"


def _score_candidate(
    requirement_text: str,
    document_text: str,
    evidence_kind: str,
    document_kind: str,
    document_category: str,
) -> tuple[float, list[str], bool]:
    allowed_categories = CATEGORY_BY_KIND.get(evidence_kind, set())
    category_match = document_category in allowed_categories
    # "other" is not a blanket allowlist. EMD and product specifications require
    # explicit instrument/datasheet content; generic text overlap is never enough.
    inferred_kind_match = document_category == "other" and document_kind == evidence_kind
    if evidence_kind in {"administrative_deadline", "generic"}:
        return 0.0, [], False
    if evidence_kind in {"emd", "product_technical"}:
        if not inferred_kind_match:
            return 0.0, [], False
        category_match = False
    elif not category_match:
        return 0.0, [], False

    requirement_tokens = _tokens(requirement_text)
    document_tokens = _tokens(document_text)
    anchors = KIND_ANCHORS.get(evidence_kind, set())
    specific_requirement_terms = requirement_tokens & anchors
    matched = sorted((requirement_tokens & document_tokens) & anchors)

    # A category label by itself is insufficient. At least one evidence-kind
    # anchor must appear in both the requirement and the extracted content.
    if not matched or not specific_requirement_terms:
        return 0.0, matched, category_match

    coverage = len(matched) / max(1, min(len(specific_requirement_terms), 5))
    score = min(1.0, 0.30 + coverage * 0.70)
    return round(score, 3), matched, category_match

def _source_citations(agent: str, source_ids: list[str], sources: list[Any]) -> list[TenderEvidenceCitation]:
    by_id = {source.source_id: source for source in sources if source.document_role == "tender"}
    citations: list[TenderEvidenceCitation] = []
    for source_id in source_ids:
        source = by_id.get(source_id)
        if source is None:
            continue
        citations.append(
            TenderEvidenceCitation(
                agent=agent,
                source_id=source.source_id,
                document_name=source.document_name,
                citation=source.citation,
                location=source.location,
            )
        )
    return citations


def _requirement_payloads(
    eligibility: EligibilityAnalysisResponse,
    compliance: ComplianceAnalysisResponse,
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for index, item in enumerate(eligibility.requirements, start=1):
        if not item.tender_source_ids:
            continue
        requirements.append({
            "requirement_id": f"EL-{index}",
            "requirement_type": "eligibility",
            "requirement": item.requirement,
            "category": item.category,
            "required_evidence": item.required_evidence,
            "tender_source_ids": item.tender_source_ids,
            "sources": _source_citations("eligibility", item.tender_source_ids, eligibility.sources),
        })
    for index, item in enumerate(compliance.requirements, start=1):
        if not item.tender_source_ids:
            continue
        evidence_terms = [item.required_action] if item.required_action else []
        requirements.append({
            "requirement_id": f"CO-{index}",
            "requirement_type": "compliance",
            "requirement": item.requirement,
            "category": item.category,
            "required_evidence": evidence_terms,
            "tender_source_ids": item.tender_source_ids,
            "sources": _source_citations("compliance", item.tender_source_ids, compliance.sources),
        })
    return requirements


def _load_latest_company_evidence(
    session: Session,
    company_id: UUID,
) -> tuple[list[dict[str, Any]], int, int, int, int, int, list[dict[str, Any]]]:
    """Load only latest versions owned by this company and their own successful extraction units.

    Returns candidates, active document count, latest version count, usable extracted version
    count, latest versions without usable extraction, and active documents with no version.
    """
    documents = list(
        session.scalars(
            select(CompanyDocument)
            .options(selectinload(CompanyDocument.versions))
            .where(CompanyDocument.company_id == company_id, CompanyDocument.is_archived.is_(False))
            .order_by(CompanyDocument.created_at.desc())
        ).all()
    )
    latest_versions: list[tuple[CompanyDocument, CompanyDocumentVersion]] = []
    documents_without_versions = 0
    for document in documents:
        version = document.current_version
        if version is not None:
            latest_versions.append((document, version))
        else:
            documents_without_versions += 1
    if not latest_versions:
        return [], len(documents), 0, 0, 0, documents_without_versions, []

    version_by_id = {version.id: (document, version) for document, version in latest_versions}
    version_ids = list(version_by_id)
    completed_rows = session.execute(
        select(DocumentProcessingRunItem, DocumentProcessingRun.created_at)
        .join(DocumentProcessingRun, DocumentProcessingRun.id == DocumentProcessingRunItem.run_id)
        .where(
            DocumentProcessingRun.company_id == company_id,
            DocumentProcessingRunItem.company_document_version_id.in_(version_ids),
            DocumentProcessingRunItem.status == "completed",
        )
        .order_by(DocumentProcessingRun.created_at.desc(), DocumentProcessingRun.id.desc())
    ).all()

    # Track the most recent processing state for each current version so the UI
    # can tell users exactly which relevant files are waiting, running or failed.
    attempt_rows = session.execute(
        select(DocumentProcessingRunItem, DocumentProcessingRun.created_at)
        .join(DocumentProcessingRun, DocumentProcessingRun.id == DocumentProcessingRunItem.run_id)
        .where(
            DocumentProcessingRun.company_id == company_id,
            DocumentProcessingRunItem.company_document_version_id.in_(version_ids),
        )
        .order_by(DocumentProcessingRun.created_at.desc(), DocumentProcessingRun.id.desc())
    ).all()
    latest_attempt_by_version: dict[UUID, DocumentProcessingRunItem] = {}
    for item, _created_at in attempt_rows:
        if item.company_document_version_id not in latest_attempt_by_version:
            latest_attempt_by_version[item.company_document_version_id] = item

    # Select the most recent successful extraction for each version, never one
    # from another company's processing queue.
    latest_item_by_version: dict[UUID, DocumentProcessingRunItem] = {}
    for item, _created_at in completed_rows:
        if item.company_document_version_id not in latest_item_by_version:
            latest_item_by_version[item.company_document_version_id] = item

    item_ids = [item.id for item in latest_item_by_version.values()]
    unit_rows = list(
        session.scalars(
            select(DocumentExtractionUnit)
            .where(DocumentExtractionUnit.run_item_id.in_(item_ids))
            .order_by(DocumentExtractionUnit.unit_index.asc())
        ).all()
    ) if item_ids else []
    units_by_item: dict[UUID, list[DocumentExtractionUnit]] = defaultdict(list)
    for unit in unit_rows:
        units_by_item[unit.run_item_id].append(unit)

    candidates: list[dict[str, Any]] = []
    processed_versions = 0
    processed_version_ids: set[UUID] = set()
    for version_id, item in latest_item_by_version.items():
        document, version = version_by_id[version_id]
        units = units_by_item.get(item.id, [])
        unit_payloads = []
        document_parts = []
        for unit in units:
            content = _unit_text(unit)
            if not content:
                continue
            document_parts.append(content)
            unit_payloads.append({"unit": unit, "text": content})
        full_text = "\n".join(document_parts)
        if not full_text.strip():
            continue
        processed_versions += 1
        processed_version_ids.add(version_id)
        candidates.append({
            "document": document,
            "version": version,
            "units": unit_payloads,
            "text": full_text,
            "synthetic": _is_synthetic(full_text),
            "document_kind": _infer_document_kind(document, version, full_text),
        })

    unprocessed_versions = [
        (document, version)
        for document, version in latest_versions
        if version.id not in processed_version_ids
    ]
    unprocessed_count = len(unprocessed_versions)
    pending_versions: list[dict[str, Any]] = []
    for document, version in unprocessed_versions:
        last_attempt = latest_attempt_by_version.get(version.id)
        if last_attempt is None:
            processing_state = "not_queued"
        elif last_attempt.status == "pending":
            processing_state = "pending"
        elif last_attempt.status == "processing":
            processing_state = "processing"
        elif last_attempt.status == "failed":
            processing_state = "failed"
        else:
            processing_state = "completed_without_text"
        pending_versions.append({
            "company_document_id": document.id,
            "document_title": document.title,
            "category": document.category.value if hasattr(document.category, "value") else str(document.category),
            "version_id": version.id,
            "version_number": version.version_number,
            "original_filename": version.original_filename,
            "inferred_evidence_kind": _infer_document_kind(document, version, ""),
            "processing_state": processing_state,
        })
    return (
        candidates, len(documents), len(latest_versions), processed_versions,
        unprocessed_count, documents_without_versions, pending_versions,
    )


def _pending_versions_for_requirement(
    evidence_kind: str,
    pending_versions: list[dict[str, Any]],
) -> list[CompanyEvidencePendingVersion]:
    """Return only unprocessed versions plausibly compatible with this evidence kind."""
    if evidence_kind in {"generic", "administrative_deadline"}:
        return []
    allowed_categories = CATEGORY_BY_KIND.get(evidence_kind, set())
    selected: list[CompanyEvidencePendingVersion] = []
    for pending in pending_versions:
        category = pending["category"]
        inferred_kind = pending["inferred_evidence_kind"]
        if evidence_kind in {"emd", "product_technical"}:
            compatible = category == "other" and inferred_kind == evidence_kind
        else:
            compatible = category in allowed_categories or (category == "other" and inferred_kind == evidence_kind)
        if compatible:
            selected.append(CompanyEvidencePendingVersion(**pending))
    return selected

def _candidate_for_requirement(
    requirement: dict[str, Any],
    source: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any] | None:
    document = source["document"]
    version = source["version"]
    requirement_text = " ".join([
        requirement["requirement"],
        requirement["category"],
        *requirement.get("required_evidence", []),
    ])
    evidence_kind = requirement.get("evidence_kind") or _infer_evidence_kind(requirement)
    document_category = document.category.value if hasattr(document.category, "value") else str(document.category)
    score, matched_terms, category_match = _score_candidate(
        requirement_text,
        source["text"],
        evidence_kind,
        source.get("document_kind", "generic"),
        document_category,
    )
    if score <= 0 or not matched_terms:
        return None

    expired = version.expiry_date is not None and version.expiry_date < today
    validity_not_recorded = version.expiry_date is None
    evidence_state = "expired_by_metadata" if expired else (
        "validity_not_recorded" if validity_not_recorded else "not_expired_by_metadata"
    )
    synthetic = bool(source["synthetic"])

    ranked_units = []
    req_tokens = _tokens(requirement_text) & KIND_ANCHORS.get(evidence_kind, set())
    for unit_data in source["units"]:
        unit = unit_data["unit"]
        content = unit_data["text"]
        unit_tokens = _tokens(content)
        overlap = len(req_tokens & unit_tokens)
        if overlap:
            ranked_units.append((overlap, unit, content))
    ranked_units.sort(key=lambda row: (-row[0], row[1].unit_index))
    unit_refs = [
        CompanyEvidenceUnitReference(
            extraction_unit_id=unit.id,
            unit_index=unit.unit_index,
            unit_type=unit.unit_type,
            source_ref=unit.source_ref or {},
            excerpt=content[:700],
        )
        for _overlap, unit, content in ranked_units[:5]
    ]

    if synthetic:
        review_note = "Synthetic or test-only content was detected. This document is not valid procurement evidence and requires replacement with genuine company evidence."
    elif expired:
        review_note = f"The stored expiry date ({version.expiry_date.isoformat()}) is earlier than the matching run date ({today.isoformat()})."
    elif validity_not_recorded:
        review_note = "No expiry date is recorded in the document metadata. Check the original file and the tender's validity requirement."
    else:
        review_note = "Document category and extracted evidence terms suggest relevance. A reviewer must verify authenticity, scope, thresholds, signatures and tender-specific sufficiency."
    if evidence_kind == "registration" and document_category == "gst_certificate":
        review_note += (
            " A GST certificate is evidence of tax registration; confirm that the tender accepts it "
            "for the stated business-registration requirement. It does not by itself establish "
            "incorporation or every form of legal-entity registration."
        )

    return {
        "source": source,
        "score": score,
        "matched_terms": matched_terms,
        "evidence_state": evidence_state,
        "synthetic": synthetic,
        "expired": expired,
        "category_match": category_match,
        "unit_refs": unit_refs,
        "candidate": CompanyEvidenceCandidate(
            company_document_id=document.id,
            document_title=document.title,
            category=document_category,
            version_id=version.id,
            version_number=version.version_number,
            original_filename=version.original_filename,
            issue_date=version.issue_date,
            expiry_date=version.expiry_date,
            financial_year=_clean_metadata_value(version.financial_year),
            issuer=_clean_metadata_value(version.issuer),
            reference_number=_clean_metadata_value(version.reference_number),
            relevance_score=score,
            matched_terms=matched_terms,
            evidence_kind=evidence_kind,
            category_match=category_match,
            synthetic_test_data=synthetic,
            evidence_state=evidence_state,
            review_note=review_note,
            units=unit_refs,
        ),
    }

def _match_requirement(
    requirement: dict[str, Any],
    evidence_sources: list[dict[str, Any]],
    pending_company_versions: list[dict[str, Any]],
    *,
    today: date,
) -> tuple[EvidenceRequirementMatch, list[dict[str, Any]]]:
    evidence_kind = requirement.get("evidence_kind") or _infer_evidence_kind(requirement)
    if evidence_kind == "administrative_deadline":
        return EvidenceRequirementMatch(
            requirement_id=requirement["requirement_id"],
            requirement_type=requirement["requirement_type"],
            requirement=requirement["requirement"],
            category=requirement["category"],
            evidence_kind=evidence_kind,
            status="not_company_evidence",
            explanation="This is a tender timeline or submission action, not a reusable company credential. Track it in the tender schedule rather than searching company documents.",
            required_evidence=requirement.get("required_evidence", []),
            tender_sources=requirement["sources"],
            candidates=[],
        ), []

    candidates = [
        item for source in evidence_sources
        if (item := _candidate_for_requirement(requirement, source, today=today)) is not None
    ]
    candidates.sort(key=lambda item: (-item["score"], item["candidate"].original_filename.casefold()))
    selected = candidates[:5]
    pending_versions = _pending_versions_for_requirement(evidence_kind, pending_company_versions)

    if not selected:
        if pending_versions:
            status = "processing_required"
            explanation = (
                "No compatible document was identified among usable extractions. "
                f"{len(pending_versions)} latest company document version(s) appear relevant and are awaiting usable extraction. "
                "Review or process the listed versions before treating the requirement as missing evidence."
            )
        else:
            status = "missing_evidence"
            explanation = (
                "No document in the compatible evidence category contained requirement-specific supporting content. "
                "No unprocessed version in a compatible category is waiting for extraction. "
                "Evidence was not located in this company's repository; this does not establish non-compliance."
            )
    elif all(item["synthetic"] for item in selected):
        status = "manual_verification_required"
        explanation = (
            "Potentially relevant test documents were found, but all selected candidates are explicitly synthetic or not valid. "
            "They cannot be treated as genuine bidder evidence and must be replaced with authentic documents."
        )
    elif all(item["expired"] for item in selected):
        status = "expired_evidence"
        explanation = (
            "Compatible document candidates were found, but all selected candidates have expired metadata. "
            "Obtain and review a current version if the tender requires evidence to remain valid."
        )
    elif selected[0]["score"] >= 0.65:
        status = "candidate_found"
        explanation = (
            "A document in the expected evidence category contains requirement-specific supporting terms. "
            "This is discovery only; authenticity, scope, values, dates, signatures and sufficiency still require review."
        )
    else:
        status = "potential_match"
        explanation = "A limited category-compatible content match was found. Review the original document before relying on it."

    experience_summary = (
        _build_experience_evidence_summary([item["source"] for item in candidates])
        if evidence_kind == "experience" else None
    )
    match = EvidenceRequirementMatch(
        requirement_id=requirement["requirement_id"],
        requirement_type=requirement["requirement_type"],
        requirement=requirement["requirement"],
        category=requirement["category"],
        evidence_kind=evidence_kind,
        status=status,
        explanation=explanation,
        required_evidence=requirement.get("required_evidence", []),
        tender_sources=requirement["sources"],
        candidates=[item["candidate"] for item in selected],
        pending_versions=pending_versions if status == "processing_required" else [],
        experience_evidence=experience_summary,
    )
    candidate_facts = [
        {
            "version_id": str(item["candidate"].version_id),
            "synthetic": bool(item["synthetic"]),
        }
        for item in candidates
    ]
    return match, candidate_facts

def create_evidence_matching_run(
    session: Session,
    tender_id: UUID,
    company_id: UUID,
    eligibility: EligibilityAnalysisResponse,
    compliance: ComplianceAnalysisResponse,
    *,
    today: date | None = None,
) -> EvidenceMatchingRunResponse:
    tender = session.get(Tender, tender_id)
    company = session.get(Company, company_id)
    if tender is None or company is None:
        raise TenderOrCompanyNotFoundError
    if company.is_archived:
        raise ArchivedCompanyError("Archived companies cannot be used for evidence matching")

    requirement_payloads = _requirement_payloads(eligibility, compliance)
    (
        evidence_sources,
        document_count,
        latest_version_count,
        processed_count,
        unprocessed_count,
        no_version_count,
        pending_company_versions,
    ) = _load_latest_company_evidence(session, company_id)
    for requirement in requirement_payloads:
        requirement["evidence_kind"] = _infer_evidence_kind(requirement)
    run_date = today or date.today()
    matching_outputs = [
        _match_requirement(req, evidence_sources, pending_company_versions, today=run_date)
        for req in requirement_payloads
    ]
    matches = [output[0] for output in matching_outputs]
    all_candidate_facts = [fact for _match, facts in matching_outputs for fact in facts]
    unique_candidates: dict[str, bool] = {}
    for fact in all_candidate_facts:
        # A version is synthetic or not; de-duplicate it across requirements.
        unique_candidates[fact["version_id"]] = unique_candidates.get(fact["version_id"], False) or fact["synthetic"]
    candidate_metrics = {
        "requirements_with_candidates": sum(bool(item.candidates) for item in matches),
        "candidate_occurrence_count": len(all_candidate_facts),
        "unique_candidate_document_count": len(unique_candidates),
        "non_synthetic_candidate_document_count": sum(not is_synthetic for is_synthetic in unique_candidates.values()),
        "synthetic_candidate_document_count": sum(unique_candidates.values()),
    }

    expired_candidate_requirements = sum(
        any(candidate.evidence_state == "expired_by_metadata" for candidate in item.candidates)
        for item in matches
    )
    unique_expired_documents = {
        candidate.version_id
        for item in matches
        for candidate in item.candidates
        if candidate.evidence_state == "expired_by_metadata"
    }
    counts = {
        "candidate_found": sum(item.status == "candidate_found" for item in matches),
        "potential_match": sum(item.status == "potential_match" for item in matches),
        "missing_evidence": sum(item.status == "missing_evidence" for item in matches),
        "expired_evidence": expired_candidate_requirements,
        "unique_expired_documents": len(unique_expired_documents),
        "manual_review": sum(item.status == "manual_verification_required" for item in matches),
        "processing_required": sum(item.status == "processing_required" for item in matches),
        "non_company_evidence": sum(item.status == "not_company_evidence" for item in matches),
    }
    eligibility_count = sum(item.requirement_type == "eligibility" for item in matches)
    compliance_count = sum(item.requirement_type == "compliance" for item in matches)
    summary = (
        f"Compared {eligibility_count} eligibility and {compliance_count} bid-stage compliance requirements "
        f"for {company.legal_name}. The company has {document_count} active document record(s), "
        f"including {latest_version_count} latest uploaded version(s): {processed_count} with usable extraction "
        f"and {unprocessed_count} awaiting usable extraction; {no_version_count} active record(s) have no uploaded version. "
        f"Found {counts['candidate_found']} requirement(s) with strong candidate matches and "
        f"{counts['potential_match']} requirement(s) with potential matches, "
        f"{counts['missing_evidence']} requirement(s) with no located evidence, "
        f"{counts['expired_evidence']} requirement(s) with an expired candidate across {counts['unique_expired_documents']} unique expired document version(s), "
        f"{counts['manual_review']} requiring manual verification, {counts['processing_required']} requiring processing, "
        f"and {counts['non_company_evidence']} administrative requirement(s) excluded from company-document matching. "
        f"Candidate discovery surfaced {candidate_metrics['candidate_occurrence_count']} requirement-to-document occurrence(s) "
        f"across {candidate_metrics['unique_candidate_document_count']} unique version(s): "
        f"{candidate_metrics['non_synthetic_candidate_document_count']} non-synthetic and "
        f"{candidate_metrics['synthetic_candidate_document_count']} synthetic. "
        "These are discovery results, not eligibility or compliance decisions."
    )

    run = TenderEvidenceMatchingRun(
        tender_id=tender_id,
        company_id=company_id,
        status="completed",
        eligibility_requirement_count=eligibility_count,
        compliance_requirement_count=compliance_count,
        candidate_found_count=counts["candidate_found"],
        potential_match_count=counts["potential_match"],
        company_document_count=document_count,
        latest_version_count=latest_version_count,
        documents_without_versions_count=no_version_count,
        processed_company_document_count=processed_count,
        unprocessed_company_document_count=unprocessed_count,
        missing_evidence_count=counts["missing_evidence"],
        expired_evidence_count=counts["expired_evidence"],
        unique_expired_document_count=counts["unique_expired_documents"],
        manual_review_count=counts["manual_review"],
        processing_required_count=counts["processing_required"],
        non_company_evidence_count=counts["non_company_evidence"],
        summary=summary,
        result_json={
            "results": [item.model_dump(mode="json") for item in matches],
            "candidate_metrics": candidate_metrics,
        },
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return EvidenceMatchingRunResponse(
        id=run.id,
        tender_id=run.tender_id,
        company_id=run.company_id,
        status="completed",
        eligibility_requirement_count=run.eligibility_requirement_count,
        compliance_requirement_count=run.compliance_requirement_count,
        candidate_found_count=run.candidate_found_count,
        potential_match_count=run.potential_match_count,
        requirements_with_candidates=candidate_metrics["requirements_with_candidates"],
        candidate_occurrence_count=candidate_metrics["candidate_occurrence_count"],
        unique_candidate_document_count=candidate_metrics["unique_candidate_document_count"],
        non_synthetic_candidate_document_count=candidate_metrics["non_synthetic_candidate_document_count"],
        synthetic_candidate_document_count=candidate_metrics["synthetic_candidate_document_count"],
        company_document_count=run.company_document_count,
        latest_version_count=run.latest_version_count,
        documents_without_versions_count=run.documents_without_versions_count,
        processed_company_document_count=run.processed_company_document_count,
        unprocessed_company_document_count=run.unprocessed_company_document_count,
        missing_evidence_count=run.missing_evidence_count,
        expired_evidence_count=run.expired_evidence_count,
        unique_expired_document_count=getattr(run, "unique_expired_document_count", 0),
        manual_review_count=run.manual_review_count,
        processing_required_count=run.processing_required_count,
        non_company_evidence_count=run.non_company_evidence_count,
        summary=run.summary,
        created_at=run.created_at,
        results=matches,
    )

def read_matching_run(run: TenderEvidenceMatchingRun) -> EvidenceMatchingRunResponse:
    stored_metrics = run.result_json.get("candidate_metrics", {})
    snapshot_results = [EvidenceRequirementMatch.model_validate(item) for item in run.result_json.get("results", [])]
    # Older saved runs predate the explicit candidate metrics. Derive conservative
    # counts from the candidate cards actually retained in those historical snapshots.
    legacy_unique_candidates: dict[str, bool] = {}
    legacy_occurrences = 0
    legacy_requirements_with_candidates = 0
    for result in snapshot_results:
        if result.candidates:
            legacy_requirements_with_candidates += 1
        for candidate in result.candidates:
            legacy_occurrences += 1
            legacy_unique_candidates[str(candidate.version_id)] = (
                legacy_unique_candidates.get(str(candidate.version_id), False)
                or candidate.synthetic_test_data
            )
    metrics = {
        "requirements_with_candidates": stored_metrics.get("requirements_with_candidates", legacy_requirements_with_candidates),
        "candidate_occurrence_count": stored_metrics.get("candidate_occurrence_count", legacy_occurrences),
        "unique_candidate_document_count": stored_metrics.get("unique_candidate_document_count", len(legacy_unique_candidates)),
        "non_synthetic_candidate_document_count": stored_metrics.get(
            "non_synthetic_candidate_document_count",
            sum(not value for value in legacy_unique_candidates.values()),
        ),
        "synthetic_candidate_document_count": stored_metrics.get(
            "synthetic_candidate_document_count", sum(legacy_unique_candidates.values())
        ),
    }
    return EvidenceMatchingRunResponse(
        id=run.id,
        tender_id=run.tender_id,
        company_id=run.company_id,
        status="completed",
        eligibility_requirement_count=run.eligibility_requirement_count,
        compliance_requirement_count=run.compliance_requirement_count,
        candidate_found_count=run.candidate_found_count,
        potential_match_count=run.potential_match_count,
        requirements_with_candidates=metrics["requirements_with_candidates"],
        candidate_occurrence_count=metrics["candidate_occurrence_count"],
        unique_candidate_document_count=metrics["unique_candidate_document_count"],
        non_synthetic_candidate_document_count=metrics["non_synthetic_candidate_document_count"],
        synthetic_candidate_document_count=metrics["synthetic_candidate_document_count"],
        company_document_count=run.company_document_count,
        latest_version_count=run.latest_version_count,
        documents_without_versions_count=run.documents_without_versions_count,
        processed_company_document_count=run.processed_company_document_count,
        unprocessed_company_document_count=run.unprocessed_company_document_count,
        missing_evidence_count=run.missing_evidence_count,
        expired_evidence_count=run.expired_evidence_count,
        unique_expired_document_count=getattr(run, "unique_expired_document_count", 0),
        manual_review_count=run.manual_review_count,
        processing_required_count=run.processing_required_count,
        non_company_evidence_count=run.non_company_evidence_count,
        summary=run.summary,
        created_at=run.created_at,
        results=snapshot_results,
    )


def get_matching_run(
    session: Session, tender_id: UUID, company_id: UUID, run_id: UUID
) -> TenderEvidenceMatchingRun | None:
    return session.scalar(
        select(TenderEvidenceMatchingRun).where(
            TenderEvidenceMatchingRun.id == run_id,
            TenderEvidenceMatchingRun.tender_id == tender_id,
            TenderEvidenceMatchingRun.company_id == company_id,
        )
    )


def list_matching_runs(
    session: Session, tender_id: UUID, company_id: UUID, *, page: int, limit: int
) -> tuple[list[TenderEvidenceMatchingRun], int]:
    from sqlalchemy import func

    filters = (
        TenderEvidenceMatchingRun.tender_id == tender_id,
        TenderEvidenceMatchingRun.company_id == company_id,
    )
    total = session.scalar(
        select(func.count()).select_from(TenderEvidenceMatchingRun).where(*filters)
    ) or 0
    items = list(
        session.scalars(
            select(TenderEvidenceMatchingRun)
            .where(*filters)
            .order_by(TenderEvidenceMatchingRun.created_at.desc(), TenderEvidenceMatchingRun.id.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        ).all()
    )
    return items, total
