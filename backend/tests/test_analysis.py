
import json

from app.services import analysis


def test_repair_replaces_existing_incorrect_citation(monkeypatch):
    """An existing but incorrect citation must be replaced."""

    def fake_generate_response(*args, **kwargs):
        return json.dumps({
            "citations": {
                "1": ["S7"]
            }
        })

    monkeypatch.setattr(
        analysis,
        "generate_response",
        fake_generate_response,
    )

    report = (
        "- Payment is due within 30 days after acceptance. [S5]"
    )

    result = analysis._repair_uncited_bullets(
        report,
        context="[S5] Performance security evidence\n[S7] Payment evidence",
        valid_ids={"S5", "S7"},
    )

    assert "[S7]" in result
    assert "[S5]" not in result


def test_repair_adds_citation_to_uncited_bullet(monkeypatch):
    """A supported bullet without a citation should receive one."""

    def fake_generate_response(*args, **kwargs):
        return json.dumps({
            "citations": {
                "1": ["S5"]
            }
        })

    monkeypatch.setattr(
        analysis,
        "generate_response",
        fake_generate_response,
    )

    report = "- Performance security is 3% of the contract value."

    result = analysis._repair_uncited_bullets(
        report,
        context="[S5] Performance security is 3%.",
        valid_ids={"S5"},
    )

    assert result.endswith("[S5]")


def test_repair_rejects_unknown_source_ids(monkeypatch):
    """Unknown source IDs must never be added."""

    def fake_generate_response(*args, **kwargs):
        return json.dumps({
            "citations": {
                "1": ["S99", "S7"]
            }
        })

    monkeypatch.setattr(
        analysis,
        "generate_response",
        fake_generate_response,
    )

    report = "- Payment is due within 30 days."

    result = analysis._repair_uncited_bullets(
        report,
        context="[S7] Payment evidence.",
        valid_ids={"S5", "S7"},
    )

    assert "[S7]" in result
    assert "[S99]" not in result


def test_repair_fails_closed_on_invalid_json(monkeypatch):
    """Invalid verifier output must not preserve old citations."""

    def fake_generate_response(*args, **kwargs):
        return "This is not valid JSON"

    monkeypatch.setattr(
        analysis,
        "generate_response",
        fake_generate_response,
    )

    report = "- Payment is due within 30 days. [S5]"

    result = analysis._repair_uncited_bullets(
        report,
        context="[S7] Payment evidence.",
        valid_ids={"S5", "S7"},
    )

    assert "[S5]" not in result

    # The fail-closed cleanup should remove the unsupported bullet.
    cleaned = analysis._remove_uncited_factual_bullets(result)

    assert "Payment is due" not in cleaned



def test_extract_payment_facts_uses_payment_source():
    evidence = [
        {
            "unit": type(
                "Unit",
                (),
                {"source_ref": {"page": 2, "block_index": 4}},
            )(),
            "filename": "fictional_tender.pdf",
            "source_id": "S7",
            "content": (
                "Payment shall be made within 30 days after "
                "delivery, installation and successful acceptance "
                "testing against a valid invoice. No advance payment "
                "will be made."
            ),
            "full_content": (
                "Payment shall be made within 30 days after "
                "delivery, installation and successful acceptance "
                "testing against a valid invoice. No advance payment "
                "will be made."
            ),
        }
    ]

    facts = analysis._extract_payment_facts(evidence)

    assert len(facts) == 2
    assert all(fact["source_id"] == "S7" for fact in facts)
    assert any("30 days" in fact["value"] for fact in facts)
    assert any("No advance payment" in fact["value"] for fact in facts)


def test_extract_payment_facts_ignores_unrelated_sentences():
    evidence = [
        {
            "unit": type(
                "Unit",
                (),
                {"source_ref": {"page": 2}},
            )(),
            "filename": "fictional_tender.pdf",
            "source_id": "S5",
            "content": (
                "Performance security of 3% must be submitted "
                "within 15 calendar days after award."
            ),
            "full_content": (
                "Performance security of 3% must be submitted "
                "within 15 calendar days after award."
            ),
        }
    ]

    facts = analysis._extract_payment_facts(evidence)

    assert facts == []


def test_ensure_payment_facts_replaces_ai_payment_citation():
    report = (
        "## 5. EMD and Financial Requirements\n"
        "- Payment within 30 days. [S5]\n"
        "- Performance security: 3%. [S5]\n"
        "\n"
        "## 6. Important Dates\n"
        "- Bid deadline: 5 December 2026. [S9]"
    )

    facts = [
        {
            "value": "Payment within 30 days after acceptance.",
            "source_id": "S7",
        }
    ]

    result = analysis._ensure_payment_facts(report, facts)

    assert "Payment within 30 days after acceptance. [S7]" in result
    assert "- Payment within 30 days. [S5]" not in result
    assert "Performance security: 3%. [S5]" in result
    assert "## 6. Important Dates" in result



def test_classify_scope_boq_technical_evidence():
    evidence = [
        {
            "source_id": "S1",
            "filename": "tender_notice.pdf",
            "content": (
                "Scope of Supply: 20 interactive flat-panel displays "
                "and installation, testing and orientation."
            ),
        },
        {
            "source_id": "S2",
            "filename": "priced_boq.xlsx",
            "content": (
                "BOQ Item Description Quantity Unit Rate Amount"
            ),
        },
        {
            "source_id": "S3",
            "filename": "technical_annexure.pdf",
            "content": (
                "Technical specifications: 65-inch display, "
                "4K resolution and 20 touch points."
            ),
        },
    ]

    result = analysis._classify_scope_boq_technical_evidence(
        evidence
    )

    assert [item["source_id"] for item in result["scope"]] == ["S1"]
    assert [item["source_id"] for item in result["boq"]] == ["S2"]
    assert [item["source_id"] for item in result["technical"]] == ["S3"]
    assert [item["source_id"] for item in result["installation"]] == ["S1"]


def test_classification_uses_dynamic_source_ids():
    evidence = [
        {
            "source_id": "S17",
            "filename": "notice.pdf",
            "content": "Scope of Supply: 20 display units.",
        },
        {
            "source_id": "S15",
            "filename": "priced_boq.xlsx",
            "content": "Item description, quantity and unit rate.",
        },
    ]

    result = analysis._classify_scope_boq_technical_evidence(
        evidence
    )

    assert result["scope"][0]["source_id"] == "S17"
    assert result["boq"][0]["source_id"] == "S15"


def test_classification_does_not_invent_evidence():
    result = analysis._classify_scope_boq_technical_evidence([])

    assert result == {
        "scope": [],
        "boq": [],
        "technical": [],
        "installation": [],
    }



def test_verified_scope_section_preserves_dynamic_source_citations():
    classified = {
        "scope": [
            {
                "source_id": "S17",
                "filename": "tender_notice.pdf",
                "content": "Scope of Supply: 20 interactive displays.",
            }
        ],
        "boq": [
            {
                "source_id": "S15",
                "filename": "priced_boq.xlsx",
                "content": "Interactive display | Quantity: 20 | Unit rate: blank",
            }
        ],
        "installation": [
            {
                "source_id": "S17",
                "filename": "tender_notice.pdf",
                "content": "Installation, testing and orientation are required.",
            }
        ],
        "technical": [],
    }

    result = analysis._build_verified_scope_section(classified)

    assert "## 2. Scope of Work" in result
    assert "Scope of Supply: 20 interactive displays." in result
    assert "Interactive display | Quantity: 20 | Unit rate: blank" in result
    assert "Installation, testing and orientation are required." in result
    assert "[S17]" in result
    assert "[S15]" in result


def test_verified_technical_section_uses_technical_source_only():
    classified = {
        "scope": [],
        "boq": [],
        "installation": [],
        "technical": [
            {
                "source_id": "S8",
                "filename": "technical_specs.pdf",
                "content": "Display: 65-inch, 4K UHD, minimum 20 touch points.",
            },
            {
                "source_id": "S7",
                "filename": "warranty.pdf",
                "content": "Minimum 3-year onsite warranty.",
            },
        ],
    }

    result = analysis._build_verified_technical_section(classified)

    assert "## 4. Technical Requirements" in result
    assert "65-inch, 4K UHD" in result
    assert "3-year onsite warranty" in result
    assert "[S8]" in result
    assert "[S7]" in result
    assert "[S15]" not in result


def test_verified_sections_return_empty_when_evidence_is_missing():
    classified = {
        "scope": [],
        "boq": [],
        "installation": [],
        "technical": [],
    }

    assert analysis._build_verified_scope_section(classified) == ""
    assert analysis._build_verified_technical_section(classified) == ""
