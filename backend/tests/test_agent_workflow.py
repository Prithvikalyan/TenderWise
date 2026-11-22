from uuid import uuid4

from app.services import agent_workflow


def test_sequential_fallback_runs_existing_agents_in_order(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(agent_workflow, "_compiled_readiness_graph", lambda *_args: None)
    monkeypatch.setattr(
        agent_workflow,
        "analyze_tender",
        lambda _session, _tender_id: calls.append("tender_analysis") or {"ok": "analysis"},
    )
    monkeypatch.setattr(
        agent_workflow,
        "analyze_eligibility",
        lambda _session, _tender_id: calls.append("eligibility") or {"ok": "eligibility"},
    )
    monkeypatch.setattr(
        agent_workflow,
        "analyze_compliance",
        lambda _session, _tender_id: calls.append("compliance") or {"ok": "compliance"},
    )

    result = agent_workflow.run_readiness_analysis_workflow(object(), uuid4())

    assert calls == ["tender_analysis", "eligibility", "compliance"]
    assert result["execution_trace"] == [
        "tender_analysis", "eligibility", "compliance", "report_inputs_ready"
    ]
    assert result["orchestration_engine"] == "sequential_fallback"
    assert result["tender_analysis"] == {"ok": "analysis"}
    assert result["eligibility"] == {"ok": "eligibility"}
    assert result["compliance"] == {"ok": "compliance"}


def test_compiled_langgraph_path_when_dependency_is_installed(monkeypatch) -> None:
    import pytest
    pytest.importorskip("langgraph")

    calls: list[str] = []
    monkeypatch.setattr(
        agent_workflow,
        "analyze_tender",
        lambda _session, _tender_id: calls.append("tender_analysis") or {"ok": "analysis"},
    )
    monkeypatch.setattr(
        agent_workflow,
        "analyze_eligibility",
        lambda _session, _tender_id: calls.append("eligibility") or {"ok": "eligibility"},
    )
    monkeypatch.setattr(
        agent_workflow,
        "analyze_compliance",
        lambda _session, _tender_id: calls.append("compliance") or {"ok": "compliance"},
    )
    agent_workflow._compiled_readiness_graph.cache_clear()
    try:
        result = agent_workflow.run_readiness_analysis_workflow(object(), uuid4())
    finally:
        agent_workflow._compiled_readiness_graph.cache_clear()

    assert result["orchestration_engine"] == "langgraph"
    assert calls == ["tender_analysis", "eligibility", "compliance"]
    assert result["execution_trace"][-1] == "report_inputs_ready"
