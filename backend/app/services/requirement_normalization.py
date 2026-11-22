"""Conservative cross-agent requirement grouping helpers.

These helpers consolidate duplicated presentation/checklist rows while leaving
agent-produced requirement records and source citations intact. A broad fallback
key is used only when no known procurement requirement family is detected.
"""

from __future__ import annotations

import re
import unicodedata


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def canonical_requirement_key(category: str, requirement: str) -> str:
    """Return a stable, cross-agent key for clearly equivalent requirements."""
    text = _normalise(f"{category} {requirement}")

    if any(term in text for term in ("non blacklisting", "blacklisting", "not been blacklisted", "not blacklisted", "debarment")):
        return "non_blacklisting_declaration"

    # Experience and turnover are separate qualification tests. Check the
    # explicit subject of the requirement before interpreting its evidence list.
    if any(term in text for term in ("similar contract", "similar supply", "work order", "purchase order", "completion certificate", "contract experience", "supply and installation contract")):
        return "similar_contract_experience"

    if any(term in text for term in ("turnover", "annual revenue", "audited financial", "chartered accountant", "financial statements", "financial year")):
        return "turnover_evidence"

    if "tax registration" in text or "gst registration details" in text or "gstin" in text:
        return "tax_registration_details"

    if (
        "registration" in _normalise(category)
        or "legally registered" in text
        or any(term in text for term in ("registration certificate", "registered business", "business registration", "incorporation"))
    ):
        return "business_registration"

    if any(term in text for term in ("emd", "earnest money", "banker s cheque", "bankers cheque", "demand draft", "bid security")):
        return "emd_submission"

    if "signed bid form" in text or "bid form" in text:
        return "signed_bid_form"

    if "priced boq" in text or "bill of quantities" in text:
        return "priced_boq"

    # Keep distinct product families separate, while grouping attribute-level
    # requirements for the same supplied product into a single checklist item.
    if any(term in text for term in ("interactive flat panel", "flat panel display", "interactive display", "65 inch", "touch points", "4k uhd")):
        return "product_technical:interactive_display"
    if "mini pc" in text or "core i5" in text or "256 gb ssd" in text:
        return "product_technical:mini_pc"
    if "wall mount" in text or "wall-mount" in text:
        return "product_technical:wall_mount"
    if "keyboard" in text or "wireless mouse" in text:
        return "product_technical:keyboard_mouse"
    if "cables and accessories" in text or "cable accessories" in text:
        return "product_technical:cables_accessories"
    if "warranty" in text:
        return "product_technical:warranty"
    if any(term in text for term in ("product datasheet", "technical compliance sheet", "technical specification")):
        return "product_technical:general"

    return f"{_normalise(category) or 'requirement'}:{_normalise(requirement)}"


def evidence_requirement_family(evidence_text: str) -> str:
    """Classify one requested evidence phrase without mixing qualification families."""
    text = _normalise(evidence_text)
    if any(term in text for term in ("non blacklisting", "blacklisting", "debarment")):
        return "non_blacklisting_declaration"
    if any(term in text for term in ("turnover", "audited financial", "chartered accountant", "financial statements", "annual revenue")):
        return "turnover_evidence"
    if any(term in text for term in ("similar contract", "work order", "purchase order", "completion certificate", "contract experience")):
        return "similar_contract_experience"
    if "registration certificate" in text or "business registration" in text or "incorporation" in text:
        return "business_registration"
    if any(term in text for term in ("emd", "earnest money", "banker s cheque", "demand draft", "bid security")):
        return "emd_submission"
    if "priced boq" in text or "bill of quantities" in text:
        return "priced_boq"
    if any(term in text for term in ("datasheet", "technical compliance", "product specification")):
        return "product_technical:general"
    return ""


CANONICAL_REQUIREMENT_LABELS = {
    "non_blacklisting_declaration": "Signed non-blacklisting declaration",
    "turnover_evidence": "Average annual turnover evidence for the required financial years",
    "tax_registration_details": "Tax registration details",
    "business_registration": "Legal business registration in India",
    "similar_contract_experience": "At least two similar supply-and-installation contracts, supported by orders and completion certificates",
    "emd_submission": "EMD submission and validity",
    "signed_bid_form": "Signed bid form",
    "priced_boq": "Completed priced Bill of Quantities",
    "product_technical:interactive_display": "Interactive display technical compliance",
    "product_technical:mini_pc": "Mini PC technical compliance",
    "product_technical:wall_mount": "Wall-mount kit technical compliance",
    "product_technical:keyboard_mouse": "Wireless keyboard and mouse compatibility",
    "product_technical:cables_accessories": "Cables and accessories completeness",
    "product_technical:warranty": "Minimum three-year on-site warranty",
    "product_technical:general": "Product technical compliance and datasheets",
}


def merge_unique_lines(*values: str | None) -> str:
    """Join non-empty text lines once, preserving first-seen order."""
    lines: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for line in re.split(r"\n+", value):
            cleaned = line.strip()
            key = _normalise(cleaned)
            if cleaned and key not in seen:
                seen.add(key)
                lines.append(cleaned)
    return "\n".join(lines)
