"""Persistence and manual state transitions for bid verification checklists."""

import hashlib
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.verification import BidVerificationChecklist, BidVerificationItem
from app.models.evidence_matching import TenderEvidenceMatchingRun
from app.models.tender import utc_now
from app.schemas.compliance import ComplianceAnalysisResponse
from app.schemas.eligibility import EligibilityAnalysisResponse
from app.schemas.evidence_matching import EvidenceMatchingRunResponse
from app.schemas.verification import VerificationChecklistResponse
from app.services.requirement_normalization import (
    CANONICAL_REQUIREMENT_LABELS,
    canonical_requirement_key,
    evidence_requirement_family,
    merge_unique_lines,
)

TENDER_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _requirement_key(category: str, requirement: str) -> str:
    normalized = canonical_requirement_key(category, requirement)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"requirement:{digest}"


def _parse_milestone_deadline(details: str) -> datetime | None:
    """Parse a tender date/time without guessing when the source has no date."""
    text = details or ""
    date_match = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", text)
    parsed_date = None
    if date_match:
        raw = " ".join(date_match.groups())
        for pattern in ("%d %B %Y", "%d %b %Y"):
            try:
                parsed_date = datetime.strptime(raw, pattern).date()
                break
            except ValueError:
                continue
    if parsed_date is None:
        iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if iso_match:
            try:
                parsed_date = date(*map(int, iso_match.groups()))
            except ValueError:
                parsed_date = None
    if parsed_date is None:
        numeric_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
        if numeric_match:
            day, month, year = map(int, numeric_match.groups())
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                parsed_date = None
    if parsed_date is None:
        return None

    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\b", text)
    if time_match:
        hour, minute, second = time_match.groups()
        parsed_time = time(int(hour), int(minute), int(second or 0))
    else:
        # Date-only deadlines remain open for the whole stated local day.
        parsed_time = time(23, 59, 59)
    return datetime.combine(parsed_date, parsed_time, tzinfo=TENDER_TIMEZONE)


def _milestone_state(details: str, now: datetime) -> tuple[str, str | None]:
    deadline = _parse_milestone_deadline(details)
    if deadline is not None and deadline < now.astimezone(TENDER_TIMEZONE):
        return (
            "blocked",
            "The recorded milestone has passed. Check the latest tender amendments, "
            "corrigenda or official extension before relying on this date.",
        )
    return "pending", None


def _candidate_snapshot(result) -> list[dict]:
    candidates = []
    for candidate in result.candidates:
        candidates.append({
            "document_title": candidate.document_title,
            "original_filename": candidate.original_filename,
            "version_id": str(candidate.version_id),
            "version_number": candidate.version_number,
            "category": candidate.category,
            "issue_date": candidate.issue_date.isoformat() if candidate.issue_date else None,
            "expiry_date": candidate.expiry_date.isoformat() if candidate.expiry_date else None,
            "reference_number": candidate.reference_number,
            "synthetic_test_data": candidate.synthetic_test_data,
            "evidence_state": candidate.evidence_state,
            "relevance_score": candidate.relevance_score,
        })
    for pending in result.pending_versions:
        candidates.append({
            "document_title": pending.document_title,
            "original_filename": pending.original_filename,
            "version_id": str(pending.version_id),
            "version_number": pending.version_number,
            "category": pending.category,
            "processing_state": pending.processing_state,
            "pending_extraction": True,
        })
    return candidates


def _canonical_checklist_action(family: str, payload: dict) -> str | None:
    """Return a concise action for known requirement families.

    The detailed agent explanations remain available in the report. The
    persistent checklist should contain a task, not concatenated copies of
    several agents' differently worded instructions.
    """
    templates = {
        "business_registration": "Provide an authentic business registration certificate and verify the bidder's legal entity details.",
        "similar_contract_experience": "Provide purchase/work orders and completion certificates for at least two distinct similar supply-and-installation contracts within the tender's stated look-back period.",
        "turnover_evidence": "Provide audited financial statements or a chartered accountant certificate demonstrating the required average annual turnover for the financial years specified in the tender.",
        "non_blacklisting_declaration": "Submit a signed non-blacklisting self-declaration valid as of the bid submission date.",
        "tax_registration_details": "Provide the bidder's applicable tax registration details and verify the identifiers against the original records.",
        "emd_submission": "Arrange the EMD using the tender-specified amount, instrument and validity period; retain proof of submission.",
        "priced_boq": "Complete the supplied BOQ spreadsheet, populate all applicable unit rates, and verify line totals and the grand total.",
        "product_technical:general": "Prepare one technical compliance sheet with product datasheets and a clause-by-clause response to the tender specifications.",
        "product_technical:interactive_display": "Verify the offered interactive display against every required size, resolution, touch-point, speaker and warranty specification using its datasheet.",
        "product_technical:mini_pc": "Verify the offered mini PC's processor, RAM and SSD against the tender minimums using its datasheet.",
        "product_technical:wall_mount": "Provide the wall-mount datasheet and confirm compatibility, load rating and required fittings against the tender.",
        "product_technical:keyboard_mouse": "Provide the wireless keyboard-and-mouse set details and confirm compatibility with the offered equipment.",
        "product_technical:cables_accessories": "Confirm and document that all cables and accessories required for complete installation are included.",
        "product_technical:warranty": "Provide a documented commitment to the required minimum three-year on-site warranty.",
    }
    action = templates.get(family)
    if not action:
        return None
    synthetic = [
        item.get("original_filename")
        for item in payload.get("candidate_documents", [])
        if item.get("synthetic_test_data") and item.get("original_filename")
    ]
    if synthetic:
        action += (
            " Synthetic test files are not bidder evidence and must be replaced "
            "with authentic documents: " + ", ".join(dict.fromkeys(synthetic)) + "."
        )
    return action


def create_verification_checklist(
    session: Session,
    *,
    tender_id: UUID,
    company_id: UUID | None,
    matching_run_id: UUID | None,
    readiness_status: str,
    eligibility: EligibilityAnalysisResponse,
    compliance: ComplianceAnalysisResponse,
    evidence_matching: EvidenceMatchingRunResponse | None,
    now: datetime | None = None,
) -> VerificationChecklistResponse:
    """Create an immutable checklist snapshot; all items start unverified."""
    checklist = BidVerificationChecklist(
        tender_id=tender_id,
        company_id=company_id,
        evidence_matching_run_id=matching_run_id,
        readiness_status=readiness_status,
    )
    session.add(checklist)
    session.flush()

    item_payloads: list[dict] = []
    if evidence_matching is not None:
        for result in evidence_matching.results:
            family = canonical_requirement_key(result.category, result.requirement)
            relevant_evidence = [
                evidence for evidence in result.required_evidence
                if not evidence_requirement_family(evidence)
                or evidence_requirement_family(evidence) == family
                or (
                    family.startswith("product_technical:")
                    and evidence_requirement_family(evidence) == "product_technical:general"
                )
            ]
            actions = [result.explanation.strip()] if result.explanation.strip() else []
            if relevant_evidence:
                actions.append("Evidence requested: " + "; ".join(relevant_evidence))
            if result.pending_versions:
                actions.append(
                    "Review extraction for: " + "; ".join(
                        f"{item.original_filename} ({item.processing_state})"
                        for item in result.pending_versions
                    )
                )
            if result.candidates:
                actions.append(
                    "Manually inspect candidate files, authenticity, validity and requirement coverage."
                )
            if result.status == "not_company_evidence":
                actions.append("Track this tender-side submission action and retain the required submission proof.")
            source_ids = [
                f"{source.agent}:{source.source_id}" for source in result.tender_sources
            ]
            item_payloads.append({
                "category": result.requirement_type,
                "requirement": result.requirement,
                "required_action": "\n".join(dict.fromkeys(actions)) or "Review this requirement against the tender and original evidence.",
                "evidence_status": result.status,
                "source_ids": source_ids,
                "candidate_documents": _candidate_snapshot(result),
            })

    # The matching service intentionally covers only requirements suitable for
    # company-document discovery. It does not cover every tender-side action or
    # every individual product specification. Always union the agent findings
    # into the checklist so technical products, BOQ, EMD and other obligations
    # cannot disappear merely because no company-document match row exists.
    for agent, items in (("eligibility", eligibility.requirements), ("compliance", compliance.requirements)):
        for item in items:
            family = canonical_requirement_key(item.category, item.requirement)
            relevant_evidence = [
                evidence for evidence in getattr(item, "required_evidence", [])
                if not evidence_requirement_family(evidence)
                or evidence_requirement_family(evidence) == family
                or (
                    family.startswith("product_technical:")
                    and evidence_requirement_family(evidence) == "product_technical:general"
                )
            ]
            if agent == "eligibility":
                action = (
                    "Provide and manually verify: " + "; ".join(relevant_evidence)
                    if relevant_evidence else item.explanation
                )
            else:
                action = item.required_action or item.explanation
            item_payloads.append({
                "category": agent,
                "requirement": item.requirement,
                "required_action": action or "Review this requirement against the tender and original evidence.",
                "evidence_status": getattr(item, "company_evidence_status", None) or item.status,
                "source_ids": [f"{agent}:{value}" for value in item.tender_source_ids]
                    + [f"{agent}:{value}" for value in item.bidder_source_ids],
                "candidate_documents": [],
            })

    current_time = now or datetime.now(TENDER_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=TENDER_TIMEZONE)
    else:
        current_time = current_time.astimezone(TENDER_TIMEZONE)

    # Only bidder-actionable deadlines belong in the checklist. Issue dates and
    # bid-opening dates are reference information, not pending bidder tasks.
    for milestone in compliance.tender_information:
        if milestone.category not in {"submission_deadline", "clarification_deadline"}:
            continue
        verification_status, verification_notes = _milestone_state(
            milestone.details, current_time
        )
        item_payloads.append({
            "category": "tender_schedule",
            "requirement": f"{milestone.item}: {milestone.details}",
            "required_action": (
                "Track and meet this tender milestone: " + milestone.details
            ),
            "evidence_status": "tender_information",
            "source_ids": [f"CO:{value}" for value in milestone.tender_source_ids],
            "candidate_documents": [],
            "verification_status": verification_status,
            "verification_notes": verification_notes,
        })

    # Deduplicate exact repeated requirements without discarding source references.
    deduplicated: dict[str, dict] = {}
    evidence_priority = {
        "not_company_evidence": 0,
        "missing_evidence": 1,
        "processing_required": 2,
        "potential_match": 3,
        "candidate_found": 4,
        "expired_evidence": 5,
        "manual_verification_required": 6,
    }
    for payload in item_payloads:
        key = _requirement_key(payload["category"], payload["requirement"])
        if key not in deduplicated:
            deduplicated[key] = {**payload, "requirement_key": key}
        else:
            current = deduplicated[key]
            current["source_ids"] = list(dict.fromkeys(current["source_ids"] + payload["source_ids"]))
            existing_version_ids = {
                item.get("version_id") for item in current["candidate_documents"]
            }
            current["candidate_documents"].extend(
                item for item in payload["candidate_documents"]
                if item.get("version_id") not in existing_version_ids
            )
            current["source_ids"] = list(dict.fromkeys(current["source_ids"]))
            if not current.get("required_action") and payload.get("required_action"):
                current["required_action"] = payload["required_action"]
            if evidence_priority.get(payload["evidence_status"], -1) > evidence_priority.get(current["evidence_status"], -1):
                current["evidence_status"] = payload["evidence_status"]
            categories = {current["category"], payload["category"]}
            if categories.intersection({"eligibility", "compliance"}) == {"eligibility", "compliance"}:
                current["category"] = "eligibility_compliance"
            if payload.get("verification_status") == "blocked":
                current["verification_status"] = "blocked"
                current["verification_notes"] = merge_unique_lines(
                    current.get("verification_notes"), payload.get("verification_notes")
                ) or None

    for key, payload in deduplicated.items():
        canonical_key = next(
            (name for name in CANONICAL_REQUIREMENT_LABELS if hashlib.sha256(name.encode("utf-8")).hexdigest()[:32] == key.split(":", 1)[-1]),
            None,
        )
        if canonical_key:
            payload["requirement"] = CANONICAL_REQUIREMENT_LABELS[canonical_key]
        normalized_family = canonical_requirement_key(payload["category"], payload["requirement"])
        canonical_action = _canonical_checklist_action(normalized_family, payload)
        if canonical_action:
            payload["required_action"] = canonical_action

    for payload in deduplicated.values():
        session.add(BidVerificationItem(checklist_id=checklist.id, **payload))

    session.commit()
    return get_verification_checklist(session, tender_id, checklist.id)


def get_verification_checklist(
    session: Session, tender_id: UUID, checklist_id: UUID
) -> VerificationChecklistResponse:
    checklist = session.scalar(
        select(BidVerificationChecklist)
        .options(selectinload(BidVerificationChecklist.items))
        .where(
            BidVerificationChecklist.id == checklist_id,
            BidVerificationChecklist.tender_id == tender_id,
        )
    )
    if checklist is None:
        raise LookupError("Verification checklist not found")
    items = checklist.items
    verified = sum(item.verification_status == "verified" for item in items)
    pending = sum(item.verification_status in {"pending", "in_progress"} for item in items)
    blocked = sum(item.verification_status == "blocked" for item in items)
    return VerificationChecklistResponse(
        id=checklist.id,
        tender_id=checklist.tender_id,
        company_id=checklist.company_id,
        evidence_matching_run_id=checklist.evidence_matching_run_id,
        readiness_status=checklist.readiness_status,
        item_count=len(items),
        verified_count=verified,
        pending_count=pending,
        blocked_count=blocked,
        created_at=checklist.created_at,
        updated_at=checklist.updated_at,
        items=items,
    )


def get_latest_verification_checklist(
    session: Session, tender_id: UUID
) -> VerificationChecklistResponse | None:
    checklist_id = session.scalar(
        select(BidVerificationChecklist.id)
        .where(BidVerificationChecklist.tender_id == tender_id)
        .order_by(BidVerificationChecklist.created_at.desc(), BidVerificationChecklist.id.desc())
        .limit(1)
    )
    if checklist_id is None:
        return None
    return get_verification_checklist(session, tender_id, checklist_id)


def update_verification_item(
    session: Session,
    *,
    tender_id: UUID,
    checklist_id: UUID,
    item_id: UUID,
    verification_status: str,
    verification_notes: str | None,
    reviewer_name: str | None,
) -> VerificationChecklistResponse:
    checklist = session.scalar(
        select(BidVerificationChecklist).where(
            BidVerificationChecklist.id == checklist_id,
            BidVerificationChecklist.tender_id == tender_id,
        )
    )
    if checklist is None:
        raise LookupError("Verification checklist not found")
    item = session.scalar(
        select(BidVerificationItem).where(
            BidVerificationItem.id == item_id,
            BidVerificationItem.checklist_id == checklist_id,
        )
    )
    if item is None:
        raise LookupError("Verification item not found")

    reviewer = (reviewer_name or "").strip() or None
    if verification_status == "verified" and reviewer is None:
        raise ValueError("Enter the reviewer name before marking an item as verified.")

    item.verification_status = verification_status
    item.verification_notes = (verification_notes or "").strip() or None
    item.reviewer_name = reviewer
    item.verified_at = utc_now() if verification_status == "verified" else None
    item.updated_at = utc_now()
    checklist.updated_at = utc_now()
    session.commit()
    return get_verification_checklist(session, tender_id, checklist_id)
