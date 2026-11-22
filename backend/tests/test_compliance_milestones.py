from app.services.compliance import _extract_explicit_clarification_deadlines


def test_explicit_clarification_deadline_is_recovered_with_source_reference():
    result = _extract_explicit_clarification_deadlines({
        "T6": "Question / clarification deadline: 25 November 2026, 17:00.\n"
             "Bid opening date: 07 December 2026, 11:00."
    })
    assert len(result) == 1
    assert result[0].category == "clarification_deadline"
    assert result[0].tender_source_ids == ["T6"]
    assert "25 November 2026" in result[0].details
    assert "17:00" in result[0].details


def test_clarification_fallback_does_not_infer_from_unrelated_date_or_unlabelled_question():
    result = _extract_explicit_clarification_deadlines({
        "T1": "Bid submission deadline: 05 December 2026, 15:00.",
        "T2": "Clarification questions may be sent by email. Bid opening: 07 December 2026.",
    })
    assert result == []
