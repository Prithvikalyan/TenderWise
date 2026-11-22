"""Consolidate existing TenderWise analyses into a bid-readiness report.

This service is deterministic: it does not ask another LLM to reinterpret
existing agent findings or create new tender facts.
"""

import logging
import re
import unicodedata
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas.report import (
    BidReadinessReportResponse,
    ReportAction,
    ReportSource,
)
from app.services.analysis import analyze_tender
from app.services.compliance import analyze_compliance
from app.services.eligibility import analyze_eligibility
from app.models.company import Company
from app.models.evidence_matching import TenderEvidenceMatchingRun
from app.schemas.evidence_matching import EvidenceMatchingRunResponse, EvidenceRequirementMatch
from app.services.evidence_matching import get_matching_run, read_matching_run
from app.services.verification import create_verification_checklist
from app.services.agent_workflow import run_readiness_analysis_workflow
from app.services.requirement_normalization import (
    CANONICAL_REQUIREMENT_LABELS,
    canonical_requirement_key,
    evidence_requirement_family,
    merge_unique_lines,
)

logger = logging.getLogger(__name__)


def _overall_status(eligibility, compliance) -> str:
    eligibility_items = eligibility.requirements
    compliance_items = compliance.requirements

    if not eligibility_items or not compliance_items:
        return "insufficient_evidence"

    eligibility_statuses = {item.status for item in eligibility_items}
    compliance_statuses = {item.status for item in compliance_items}

    if "not_met" in eligibility_statuses or "non_compliant" in compliance_statuses:
        return "not_ready"

    if "evidence_missing" in eligibility_statuses or "evidence_missing" in compliance_statuses:
        return "incomplete"

    if "needs_verification" in eligibility_statuses or "needs_verification" in compliance_statuses:
        return "needs_review"

    if eligibility_statuses == {"met"} and compliance_statuses == {"compliant"}:
        return "ready"

    return "needs_review"


def _build_sources(analysis, eligibility, compliance) -> list[ReportSource]:
    result: list[ReportSource] = []

    for source in analysis.sources:
        result.append(
            ReportSource(
                source_id=f"TA:{source.source_id}",
                agent="tender_analysis",
                original_source_id=source.source_id,
                document_name=source.document_name,
                document_role="tender",
                unit_type=source.unit_type,
                location=source.location,
                citation=source.citation,
            )
        )

    for source in eligibility.sources:
        result.append(
            ReportSource(
                source_id=f"EL:{source.source_id}",
                agent="eligibility",
                original_source_id=source.source_id,
                document_name=source.document_name,
                document_role=source.document_role,
                unit_type=source.unit_type,
                location=source.location,
                citation=source.citation,
            )
        )

    for source in compliance.sources:
        result.append(
            ReportSource(
                source_id=f"CO:{source.source_id}",
                agent="compliance",
                original_source_id=source.source_id,
                document_name=source.document_name,
                document_role=source.document_role,
                unit_type=source.unit_type,
                location=source.location,
                citation=source.citation,
            )
        )

    unique = {}
    for source in result:
        unique[(source.agent, source.source_id)] = source
    return list(unique.values())


def _action_group(action: str) -> str | None:
    """Group only clearly equivalent reusable-document actions."""
    text = action.casefold()
    if "blacklist" in text:
        return "non_blacklisting_declaration"
    if "turnover" in text or "audited financial" in text:
        return "turnover_evidence"
    if (
        "work order" in text
        or "purchase order" in text
        or "completion certificate" in text
        or "similar contract" in text
    ):
        return "contract_experience_evidence"
    if "registration certificate" in text and "tax" not in text:
        return "business_registration"
    return None


def _normalise_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _company_matching_note(result) -> str:
    """Describe discovery results without promoting candidates to verified evidence."""
    filenames = list(dict.fromkeys(
        candidate.original_filename for candidate in result.candidates
    ))
    synthetic_names = list(dict.fromkeys(
        candidate.original_filename
        for candidate in result.candidates
        if candidate.synthetic_test_data
    ))
    non_synthetic_names = list(dict.fromkeys(
        candidate.original_filename
        for candidate in result.candidates
        if not candidate.synthetic_test_data
    ))
    expired_names = list(dict.fromkeys(
        candidate.original_filename
        for candidate in result.candidates
        if candidate.evidence_state == "expired_by_metadata"
    ))
    pending_names = list(dict.fromkeys(
        pending.original_filename for pending in result.pending_versions
    ))

    if result.status == "not_company_evidence":
        return (
            "Company matching classified this as a tender-side submission action, "
            "not a reusable company credential. Track and retain tender-specific proof."
        )
    if synthetic_names and not non_synthetic_names:
        note = (
            "Company document discovery found only synthetic/test documents. They do "
            "not count as bidder evidence and must be replaced with authentic company "
            "documents. Candidate files: " + ", ".join(synthetic_names) + "."
        )
        if pending_names:
            note += " Additional latest files awaiting extraction: " + ", ".join(pending_names) + "."
        return note
    if expired_names and len(expired_names) == len(filenames):
        return (
            "Company document discovery found candidate files, but all have expired "
            "recorded metadata. Obtain or identify current evidence and verify the "
            "originals. Candidate files: " + ", ".join(expired_names) + "."
        )
    if filenames:
        note = (
            "Company document discovery found potentially relevant candidate file(s): "
            + ", ".join(filenames)
            + ". This is not a finding of authenticity, sufficiency or compliance; "
            "a reviewer must inspect the originals, dates, signatures, scope and values."
        )
        if synthetic_names:
            note += " Synthetic/test files: " + ", ".join(synthetic_names) + "."
        if expired_names:
            note += " Expired-by-metadata files: " + ", ".join(expired_names) + "."
        return note
    if pending_names:
        return (
            "Company matching found relevant latest document version(s) that do not "
            "yet have usable extracted text. Complete or review extraction before "
            "treating the requirement as missing evidence. Files: "
            + ", ".join(pending_names) + "."
        )
    return (
        "Company Evidence Matching did not locate a compatible active company "
        "document for this requirement. This does not establish non-compliance."
    )


def _merge_matching_results(results: list[EvidenceRequirementMatch]) -> EvidenceRequirementMatch:
    """Merge matching-run rows for one canonical requirement family."""
    if len(results) == 1:
        return results[0]
    status_priority = {
        "not_company_evidence": 0,
        "missing_evidence": 1,
        "processing_required": 2,
        "potential_match": 3,
        "candidate_found": 4,
        "expired_evidence": 5,
        "manual_verification_required": 6,
    }
    chosen_status = max(
        (item.status for item in results),
        key=lambda status: status_priority.get(status, 0),
    )
    candidates_by_version = {}
    pending_by_version = {}
    sources_by_key = {}
    for item in results:
        for candidate in item.candidates:
            candidates_by_version[str(candidate.version_id)] = candidate
        for pending in item.pending_versions:
            pending_by_version[str(pending.version_id)] = pending
        for source in item.tender_sources:
            sources_by_key[(source.agent, source.source_id)] = source
    experience_summaries = [item.experience_evidence for item in results if item.experience_evidence]
    experience_summary = max(
        experience_summaries,
        key=lambda summary: summary.distinct_referenced_contract_count,
        default=None,
    )
    first = results[0]
    return first.model_copy(update={
        "status": chosen_status,
        "explanation": merge_unique_lines(*(item.explanation for item in results)),
        "required_evidence": list(dict.fromkeys(
            evidence for item in results for evidence in item.required_evidence
        )),
        "tender_sources": list(sources_by_key.values()),
        "candidates": list(candidates_by_version.values())[:5],
        "pending_versions": list(pending_by_version.values()),
        "experience_evidence": experience_summary,
    })


def _enrich_requirements_with_company_matching(
    eligibility,
    compliance,
    evidence_matching: EvidenceMatchingRunResponse | None,
):
    """Attach saved company-discovery results to the matching agent requirements.

    Matching is keyed by the normalized requirement text returned by the same
    analysis run. It never marks a requirement met/compliant. Only a non-synthetic,
    non-expired candidate moves a missing assessment to manual verification.
    """
    if evidence_matching is None:
        return eligibility, compliance, False

    indexed: dict[tuple[str, str], list[EvidenceRequirementMatch]] = defaultdict(list)
    for match in evidence_matching.results:
        key = canonical_requirement_key(match.category, match.requirement)
        indexed[(match.requirement_type, key)].append(match)

    has_usable_candidate = False

    def enrich(items, requirement_type: str):
        nonlocal has_usable_candidate
        enriched = []
        for item in items:
            key = (requirement_type, canonical_requirement_key(item.category, item.requirement))
            matching_results = indexed.get(key, [])
            if not matching_results:
                enriched.append(item)
                continue
            match = _merge_matching_results(matching_results)

            note = _company_matching_note(match)
            candidate_names = list(dict.fromkeys(
                candidate.original_filename for candidate in match.candidates
            ))[:10]
            pending_names = list(dict.fromkeys(
                pending.original_filename for pending in match.pending_versions
            ))[:10]
            has_usable_candidate = has_usable_candidate or any(
                (not candidate.synthetic_test_data)
                and candidate.evidence_state != "expired_by_metadata"
                for candidate in match.candidates
            )

            status = item.status
            non_synthetic_current_candidate = any(
                (not candidate.synthetic_test_data)
                and candidate.evidence_state != "expired_by_metadata"
                for candidate in match.candidates
            )
            if status in {"evidence_missing", "needs_verification"} and non_synthetic_current_candidate:
                status = "needs_verification"

            # The original agent text often says no bidder documents were
            # supplied. When matching has run, replace that stale explanation
            # with the more specific company-repository discovery result.
            # Keep the two evidence locations explicit. A lack of documents in
            # the tender workspace does not mean company-repository matching was
            # skipped, and a repository candidate does not mean it was uploaded
            # to this tender or is acceptable evidence.
            workspace_note = (
                "Tender workspace assessment: " + item.explanation.strip()
                if item.explanation and item.explanation.strip()
                else "Tender workspace assessment: no additional explanation was returned by the agent."
            )
            explanation = merge_unique_lines(note, workspace_note)[:2000]

            enriched.append(item.model_copy(update={
                "status": status,
                "explanation": explanation,
                "company_evidence_status": match.status,
                "company_evidence_note": note,
                "company_evidence_candidate_names": candidate_names,
                "company_evidence_pending_filenames": pending_names,
            }))
        return enriched

    eligibility_items = enrich(eligibility.requirements, "eligibility")
    compliance_items = enrich(compliance.requirements, "compliance")

    def eligibility_status(items):
        statuses = {item.status for item in items}
        if not items:
            return "no_requirements_found"
        if "not_met" in statuses:
            return "not_ready"
        if "evidence_missing" in statuses:
            return "incomplete"
        if "needs_verification" in statuses:
            return "needs_review"
        return "ready"

    def compliance_status(items):
        statuses = {item.status for item in items}
        if not items:
            return "no_requirements_found"
        if "non_compliant" in statuses:
            return "non_compliant"
        if "evidence_missing" in statuses:
            return "incomplete"
        if "needs_verification" in statuses:
            return "needs_review"
        return "compliant"

    eligibility_copy = eligibility.model_copy(update={
        "requirements": eligibility_items,
        "overall_status": eligibility_status(eligibility_items),
        "bidder_evidence_found": bool(eligibility.bidder_evidence_found or has_usable_candidate),
    })
    compliance_copy = compliance.model_copy(update={
        "requirements": compliance_items,
        "overall_status": compliance_status(compliance_items),
        "bidder_evidence_found": bool(compliance.bidder_evidence_found or has_usable_candidate),
    })
    return eligibility_copy, compliance_copy, has_usable_candidate


def _combined_assessment_groups(eligibility, compliance) -> list[dict]:
    """Consolidate equivalent eligibility/compliance findings for report display."""
    grouped: dict[str, dict] = {}
    for agent, prefix, items in (
        ("Eligibility", "EL", eligibility.requirements),
        ("Compliance", "CO", compliance.requirements),
    ):
        for item in items:
            key = canonical_requirement_key(item.category, item.requirement)
            group = grouped.setdefault(key, {
                "key": key,
                "agents": [],
                "requirements": [],
                "source_ids": [],
                "required_evidence": [],
                "explanations": [],
                "company_evidence_note": None,
                "company_evidence_status": None,
                "company_evidence_candidate_names": [],
                "company_evidence_pending_filenames": [],
                "statuses": [],
            })
            if agent not in group["agents"]:
                group["agents"].append(agent)
            group["requirements"].append(item.requirement)
            group["source_ids"].extend(f"{prefix}:{sid}" for sid in item.tender_source_ids)
            group["source_ids"].extend(f"{prefix}:{sid}" for sid in item.bidder_source_ids)
            group["required_evidence"].extend(getattr(item, "required_evidence", []))
            agent_explanation = item.explanation or ""
            if item.company_evidence_note:
                agent_explanation = "\n".join(
                    line for line in agent_explanation.splitlines()
                    if _normalise_text(line) != _normalise_text(item.company_evidence_note)
                ).strip()
            if agent_explanation:
                group["explanations"].append(f"{agent}: {agent_explanation}")
            group["statuses"].append(item.status)
            if item.company_evidence_note and not group["company_evidence_note"]:
                group["company_evidence_note"] = item.company_evidence_note
            if item.company_evidence_status:
                group["company_evidence_status"] = item.company_evidence_status
            group["company_evidence_candidate_names"].extend(item.company_evidence_candidate_names)
            group["company_evidence_pending_filenames"].extend(item.company_evidence_pending_filenames)

    priority = {
        "not_met": 5,
        "non_compliant": 5,
        "evidence_missing": 4,
        "needs_verification": 3,
        "met": 1,
        "compliant": 1,
    }
    result = []
    for key, group in grouped.items():
        group["requirement"] = CANONICAL_REQUIREMENT_LABELS.get(
            key, max(group["requirements"], key=len)
        )
        group["source_ids"] = list(dict.fromkeys(group["source_ids"]))
        group["required_evidence"] = list(dict.fromkeys(group["required_evidence"]))
        group["explanations"] = list(dict.fromkeys(group["explanations"]))
        group["company_evidence_candidate_names"] = list(dict.fromkeys(group["company_evidence_candidate_names"]))
        group["company_evidence_pending_filenames"] = list(dict.fromkeys(group["company_evidence_pending_filenames"]))
        group["status"] = max(group["statuses"], key=lambda status: priority.get(status, 0))
        result.append(group)
    return result


def _action_requirement_group(action: ReportAction) -> str:
    """Group actions by the requirement they resolve, not incidental wording."""
    families = {
        canonical_requirement_key("", requirement)
        for requirement in action.covered_requirements
    }
    families.discard("")
    technical = {family for family in families if family.startswith("product_technical:")}
    action_text = _normalise_text(action.action)
    if (technical and len(technical) == len(families)) or any(
        phrase in action_text
        for phrase in ("product datasheet", "technical compliance sheet", "technical compliance statement")
    ):
        return "technical_submission"
    if len(families) == 1:
        return next(iter(families))
    # Legacy/sparse agent output may not have requirement labels. In that case
    # merge only byte-for-byte equivalent action text, never by loose keywords.
    return f"action:{_normalise_text(action.action)}"



def _canonical_action_text(
    group: str,
    requirements: list[str],
    first_action: str,
    second_action: str,
) -> str:
    """Produce one readable action for a known cross-agent requirement family.

    Agent action strings are often near-duplicates with different wording. Joining
    those strings creates noisy output; use a conservative, family-specific task
    while keeping all source IDs and original requirement text on the action.
    """
    templates = {
        "business_registration": (
            "Provide an authentic business registration certificate and verify that "
            "the legal entity details satisfy the tender requirement."
        ),
        "similar_contract_experience": (
            "Provide purchase/work orders and completion certificates for at least "
            "two distinct similar supply-and-installation contracts within the "
            "tender's stated look-back period."
        ),
        "turnover_evidence": (
            "Provide audited financial statements or a chartered accountant "
            "certificate demonstrating the required average annual turnover for the financial years specified in the tender."
        ),
        "non_blacklisting_declaration": (
            "Submit a signed non-blacklisting self-declaration that is valid as of "
            "the bid submission date."
        ),
        "tax_registration_details": (
            "Provide the bidder's applicable tax registration details and verify "
            "the registration identifiers against the original records."
        ),
        "emd_submission": (
            "Arrange the EMD using the tender-specified amount, instrument and validity period, and retain proof of submission."
        ),
        "priced_boq": (
            "Complete the supplied BOQ spreadsheet: populate every applicable unit "
            "rate and confirm the calculated line totals and grand total."
        ),
        "technical_submission": (
            "Submit one technical compliance sheet with product datasheets and a "
            "clause-by-clause response covering every applicable product and "
            "warranty specification."
        ),
        "product_technical:interactive_display": (
            "Provide the offered interactive display datasheet and a clause-by-clause "
            "response for size, resolution, touch points, speakers and warranty."
        ),
        "product_technical:mini_pc": (
            "Provide the offered mini-PC datasheet confirming processor, RAM and SSD "
            "specifications."
        ),
        "product_technical:warranty": (
            "Provide the manufacturer's or bidder's documented commitment to the "
            "required minimum three-year on-site warranty."
        ),
    }
    if group in templates:
        action = templates[group]
        source_actions = f"{first_action} {second_action}".casefold()
        if "synthetic" in source_actions:
            action = (
                "Replace synthetic test-only candidate documents with authentic "
                "bidder evidence. " + action
            )
        return action
    # Do not concatenate semantically similar sentences. Keep the first authored
    # task, which is deterministic and still accompanied by all merged citations.
    return (first_action or second_action).strip()[:1200]

def _merge_duplicate_actions(actions: list[ReportAction]) -> list[ReportAction]:
    """Merge equivalent requirement actions while retaining all source links."""
    result: list[ReportAction] = []
    group_to_index: dict[str, int] = {}
    priority = {
        "needs_verification": 1,
        "evidence_missing": 2,
        "not_met": 3,
        "non_compliant": 3,
    }

    for action in actions:
        group = _action_requirement_group(action)
        if group not in group_to_index:
            group_to_index[group] = len(result)
            result.append(action.model_copy(deep=True))
            continue

        current = result[group_to_index[group]]
        current.source_ids = list(dict.fromkeys(current.source_ids + action.source_ids))
        current.covered_requirements = list(dict.fromkeys(
            current.covered_requirements + action.covered_requirements
        ))
        if current.category != action.category:
            current.category = "combined"
        if priority.get(action.status, 0) > priority.get(current.status, 0):
            current.status = action.status

        current.action = _canonical_action_text(
            group,
            current.covered_requirements,
            current.action,
            action.action,
        )

    return result


def _deduplicate_post_award(obligations):
    """Merge repeated obligations while retaining the strongest detail and citations."""
    result = []
    keys: dict[str, int] = {}

    for obligation in obligations:
        category = obligation.category.casefold().strip()
        normalized_name = re.sub(
            r"\W+", " ", obligation.obligation.casefold()
        ).strip()

        # Warranty statements often differ in wording while describing the
        # same contract obligation. Group them by category, not exact wording.
        if category == "warranty":
            key = "warranty"
        else:
            key = f"{category}:{normalized_name}"

        if key not in keys:
            keys[key] = len(result)
            result.append(obligation.model_copy(deep=True))
            continue

        existing = result[keys[key]]
        existing.tender_source_ids = list(
            dict.fromkeys(existing.tender_source_ids + obligation.tender_source_ids)
        )

        if len(obligation.obligation) > len(existing.obligation):
            existing.obligation = obligation.obligation
        if len(obligation.details) > len(existing.details):
            existing.details = obligation.details

    return result


def _referenced_sources(markdown: str, sources: list[ReportSource]) -> list[ReportSource]:
    used = set(
        re.findall(r"\[(TA:S\d+|EL:[TC]\d+|CO:[TC]\d+)\]", markdown)
    )
    return [source for source in sources if source.source_id in used]


def _build_actions(eligibility, compliance) -> list[ReportAction]:
    actions: list[ReportAction] = []

    for item in eligibility.requirements:
        if item.status in {"evidence_missing", "needs_verification", "not_met"}:
            if item.company_evidence_status == "manual_verification_required" and "synthetic" in (item.company_evidence_note or "").casefold():
                action_text = (
                    "Replace synthetic test-only candidate documents with authentic bidder evidence for: "
                    + item.requirement
                )
            elif item.company_evidence_status == "processing_required":
                action_text = (
                    "Complete extraction and review the company document(s): "
                    + "; ".join(item.company_evidence_pending_filenames)
                )
            elif item.required_evidence:
                family = canonical_requirement_key(item.category, item.requirement)
                relevant_evidence = [
                    evidence for evidence in item.required_evidence
                    if not evidence_requirement_family(evidence)
                    or evidence_requirement_family(evidence) == family
                ]
                action_text = "Provide and verify: " + "; ".join(relevant_evidence or item.required_evidence)
            else:
                action_text = "Review and resolve this eligibility requirement: " + item.requirement
            actions.append(
                ReportAction(
                    action=action_text[:1200],
                    category="eligibility",
                    status=item.status,
                    source_ids=[
                        f"EL:{source_id}" for source_id in item.tender_source_ids
                    ]
                    + [f"EL:{source_id}" for source_id in item.bidder_source_ids],
                    covered_requirements=[item.requirement],
                )
            )

    for item in compliance.requirements:
        if item.status in {"evidence_missing", "needs_verification", "non_compliant"}:
            if item.company_evidence_status == "manual_verification_required" and "synthetic" in (item.company_evidence_note or "").casefold():
                action_text = (
                    "Replace synthetic test-only candidate documents with authentic bidder evidence for: "
                    + item.requirement
                )
            elif item.company_evidence_status == "processing_required":
                action_text = (
                    "Complete extraction and review the company document(s): "
                    + "; ".join(item.company_evidence_pending_filenames)
                )
            else:
                action_text = item.required_action.strip() or (
                    "Review and resolve this compliance requirement: " + item.requirement
                )

            actions.append(
                ReportAction(
                    action=action_text[:1200],
                    category="compliance",
                    status=item.status,
                    source_ids=[
                        f"CO:{source_id}" for source_id in item.tender_source_ids
                    ]
                    + [f"CO:{source_id}" for source_id in item.bidder_source_ids],
                    covered_requirements=[item.requirement],
                )
            )

    return _merge_duplicate_actions(actions)


def _status_label(status: str) -> str:
    return status.replace("_", " ").title()


def _build_markdown(
    tender_id: UUID,
    overall_status: str,
    summary: str,
    analysis,
    eligibility,
    compliance,
    actions: list[ReportAction],
    post_award_obligations,
    evidence_matching: EvidenceMatchingRunResponse | None = None,
    company_name: str | None = None,
) -> str:
    analysis_report = re.sub(
        r"\[(S\d+)\]", r"[TA:\1]", analysis.report.strip()
    )
    lines = [
        "# TenderWise — Bid Readiness Report",
        "",
        f"- Tender ID: `{tender_id}`",
        f"- Overall readiness: **{_status_label(overall_status)}**",
        f"- Summary: {summary}",
        "",
        "## 1. Tender Analysis",
        "",
        analysis_report or "No tender analysis report was generated.",
        "",
        "## 2. Consolidated Eligibility and Bid-stage Compliance",
        "",
        f"**Eligibility agent status:** {_status_label(eligibility.overall_status)}  ",
        f"**Compliance agent status:** {_status_label(compliance.overall_status)}",
        "",
        "Equivalent requirements identified by both agents are shown once below. "
        "All original agent assessments and tender citations are retained.",
        "",
    ]

    assessment_groups = _combined_assessment_groups(eligibility, compliance)
    if assessment_groups:
        for group in assessment_groups:
            citations = ", ".join(f"[{source_id}]" for source_id in group["source_ids"])
            agent_names = " and ".join(group["agents"])
            required = "; ".join(group["required_evidence"])
            explanations = "  \n".join(group["explanations"])
            lines.append(
                f"- **{_status_label(group['status'])} — {group['requirement']}**  \n"
                f"  Assessed by: {agent_names}.  \n"
                + (f"  {explanations}  \n" if explanations else "")
                + (f"  Evidence requested: {required}  \n" if required else "")
                + (
                    f"  Company evidence discovery: {group['company_evidence_note']}  \n"
                    if group["company_evidence_note"] else ""
                )
                + f"  Tender/agent evidence references: {citations or 'Not supplied'}"
            )
    else:
        lines.append("No eligibility or bid-stage compliance requirements could be established from processed evidence.")

    lines.extend(["", "## 3. Important Tender Information", ""])
    if compliance.tender_information:
        for item in compliance.tender_information:
            citations = ", ".join(f"[CO:{sid}]" for sid in item.tender_source_ids)
            lines.append(f"- **{item.item}:** {item.details} {citations}")
    else:
        lines.append(
            "No important tender dates or information were returned by the compliance analysis."
        )

    lines.extend(["", "## 4. Post-award Obligations", ""])
    if post_award_obligations:
        for item in post_award_obligations:
            citations = ", ".join(f"[CO:{sid}]" for sid in item.tender_source_ids)
            lines.append(f"- **{item.obligation}:** {item.details} {citations}")
    else:
        lines.append(
            "No post-award obligations were identified in the processed evidence."
        )

    lines.extend(["", "## 5. Actions Required Before Submission", ""])
    if actions:
        for item in actions:
            citations = ", ".join(f"[{sid}]" for sid in item.source_ids)
            covered = "; ".join(item.covered_requirements)
            lines.append(
                f"- **{_status_label(item.status)} ({item.category}):** "
                f"{item.action} {citations}"
                + (f"  \n  Covers: {covered}" if covered else "")
            )
    else:
        lines.append(
            "No outstanding evidence or verification actions were identified by these assessments."
        )

    if evidence_matching is not None:
        lines.extend(["", "## 6. Company Evidence Matching", ""])
        lines.append(
            f"Company: **{company_name or str(evidence_matching.company_id)}** "
            f"(`{evidence_matching.company_id}`) · Matching run: `{evidence_matching.id}`"
        )
        lines.append(evidence_matching.summary)
        lines.append("")
        for result in evidence_matching.results:
            lines.append(
                f"- **{_status_label(result.status)} ({result.requirement_type}) — {result.requirement}**"
            )
            lines.append(f"  {result.explanation}")
            if result.tender_sources:
                citations = "; ".join(
                    f"{source.document_name}: {source.citation or source.source_id}"
                    for source in result.tender_sources
                )
                lines.append(f"  Tender sources: {citations}")
            if result.candidates:
                for candidate in result.candidates:
                    flags = []
                    if candidate.synthetic_test_data:
                        flags.append("SYNTHETIC TEST DATA — NOT VALID BIDDER EVIDENCE")
                    if candidate.evidence_state == "expired_by_metadata":
                        flags.append("EXPIRED BY RECORDED METADATA")
                    lines.append(
                        f"  Candidate: {candidate.original_filename} · Version {candidate.version_number}"
                        + (f" · {'; '.join(flags)}" if flags else "")
                    )
            if result.pending_versions:
                for pending in result.pending_versions:
                    lines.append(
                        f"  Extraction review: {pending.original_filename} · {pending.processing_state}"
                    )
            if result.experience_evidence is not None:
                summary = result.experience_evidence
                lines.append(
                    f"  Contract-reference review: {summary.distinct_referenced_contract_count} "
                    f"distinct explicit reference(s); {summary.unreferenced_document_count} "
                    "matched document(s) without an identifiable reference."
                )
                for group in summary.contract_groups:
                    roles = []
                    if group.has_order_document:
                        roles.append("order document found")
                    if group.has_completion_certificate:
                        roles.append("completion certificate found")
                    lines.append(
                        f"  Reference `{group.reference_number}`: {', '.join(roles) or 'documents found'}"
                    )
                lines.append(f"  Experience grouping limitation: {summary.note}")
        lines.extend([
            "",
            "Company evidence matching is document discovery only. Candidate documents do not establish authenticity, validity, sufficiency or compliance. Synthetic test documents must never be used as bidder evidence.",
        ])
        limitations_number = "7"
    else:
        limitations_number = "6"

    lines.extend(
        [
            "",
            f"## {limitations_number}. Evidence and Limitations",
            "",
            "- Source references are namespaced: `TA:` for Tender Analysis, "
            "`EL:` for Eligibility, and `CO:` for Compliance.",
            "- A missing bidder document is treated as missing evidence, not proof of non-compliance.",
            "- This report is an evidence-based readiness aid, not a guarantee of bid acceptance.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_bid_readiness_report(
    session: Session,
    tender_id: UUID,
    *,
    company_id: UUID | None = None,
    matching_run_id: UUID | None = None,
) -> BidReadinessReportResponse:
    """Run existing analyses and consolidate their validated outputs."""

    workflow = run_readiness_analysis_workflow(
        session,
        tender_id,
        agent_functions=(analyze_tender, analyze_eligibility, analyze_compliance),
    )
    analysis = workflow["tender_analysis"]
    eligibility = workflow["eligibility"]
    compliance = workflow["compliance"]
    orchestration_engine = workflow["orchestration_engine"]

    evidence_matching = None
    selected_run = None
    company_name = None
    if matching_run_id is not None and company_id is None:
        raise ValueError("company_id is required when matching_run_id is supplied")
    if company_id is not None:
        company = session.get(Company, company_id)
        if company is None:
            raise LookupError("Company not found")
        if company.is_archived:
            raise ValueError("Archived companies cannot be used in a bid-readiness report")
        company_name = company.display_name or company.legal_name
        if matching_run_id is not None:
            selected_run = get_matching_run(session, tender_id, company_id, matching_run_id)
        else:
            selected_run = session.scalar(
                select(TenderEvidenceMatchingRun)
                .where(
                    TenderEvidenceMatchingRun.tender_id == tender_id,
                    TenderEvidenceMatchingRun.company_id == company_id,
                )
                .order_by(TenderEvidenceMatchingRun.created_at.desc(), TenderEvidenceMatchingRun.id.desc())
                .limit(1)
            )
        if selected_run is None:
            raise ValueError("No saved Evidence Matching run exists for this tender and company. Run Evidence Matching first.")
        evidence_matching = read_matching_run(selected_run)

    eligibility, compliance, company_candidate_evidence_found = (
        _enrich_requirements_with_company_matching(
            eligibility, compliance, evidence_matching
        )
    )

    overall = _overall_status(eligibility, compliance)

    if overall == "ready":
        summary = (
            "All assessed eligibility and bid-stage compliance requirements are "
            "marked as met/compliant by the existing agents. Verify the original "
            "documents before submission."
        )
    elif overall == "not_ready":
        summary = (
            "At least one assessed requirement is explicitly marked not met or "
            "non-compliant. Resolve it before considering submission."
        )
    elif overall == "incomplete":
        if evidence_matching is not None:
            summary = (
                "One or more requirements lack authentic, usable bidder evidence. "
                "Company document discovery results are included; candidate files "
                "require review, synthetic test files do not count as bidder evidence, "
                "and documents awaiting extraction must be processed before assessment."
            )
        else:
            summary = (
                "One or more requirements lack processed bidder evidence. Upload the "
                "required supporting documents and rerun the assessments."
            )
    elif overall == "needs_review":
        summary = (
            "One or more requirements need manual verification, or the assessments "
            "returned an inconclusive status."
        )
    else:
        summary = (
            "There is insufficient processed evidence to establish bid readiness. "
            "Process the tender documents and run the required assessments."
        )

    actions = _build_actions(eligibility, compliance)
    post_award_obligations = _deduplicate_post_award(
        compliance.post_award_obligations
    )
    sources = _build_sources(analysis, eligibility, compliance)

    markdown = _build_markdown(
        tender_id,
        overall,
        summary,
        analysis,
        eligibility,
        compliance,
        actions,
        post_award_obligations,
        evidence_matching,
        company_name,
    )
    sources = _referenced_sources(markdown, sources)

    checklist = create_verification_checklist(
        session,
        tender_id=tender_id,
        company_id=company_id,
        matching_run_id=selected_run.id if selected_run is not None else None,
        readiness_status=overall,
        eligibility=eligibility,
        compliance=compliance,
        evidence_matching=evidence_matching,
    )

    logger.info(
        "Bid readiness report generated: tender_id=%s status=%s actions=%s checklist=%s",
        tender_id,
        overall,
        len(actions),
        checklist.id,
    )

    return BidReadinessReportResponse(
        tender_id=str(tender_id),
        overall_status=overall,
        summary=summary,
        report_markdown=markdown,
        tender_analysis=analysis,
        eligibility_requirements=eligibility.requirements,
        compliance_requirements=compliance.requirements,
        tender_information=compliance.tender_information,
        post_award_obligations=post_award_obligations,
        action_items=actions,
        sources=sources,
        company_name=company_name,
        evidence_matching=evidence_matching,
        verification_checklist=checklist,
        evidence_found=bool(
            analysis.evidence_found
            or eligibility.evidence_found
            or compliance.evidence_found
        ),
        orchestration_engine=orchestration_engine,
        bidder_evidence_found=bool(
            eligibility.bidder_evidence_found
            or compliance.bidder_evidence_found
            or company_candidate_evidence_found
        ),
    )
