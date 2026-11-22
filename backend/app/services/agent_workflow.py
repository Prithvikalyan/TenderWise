"""LangGraph orchestration for the existing tender analysis agents.

The agents continue to own their prompts, retrieval and validation. This
workflow coordinates their execution in a deterministic order using one
SQLAlchemy session; the session is deliberately not shared across parallel
nodes.
"""

import logging
from functools import lru_cache
from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.analysis import analyze_tender
from app.services.compliance import analyze_compliance
from app.services.eligibility import analyze_eligibility

logger = logging.getLogger(__name__)


class ReadinessWorkflowState(TypedDict, total=False):
    session: Session
    tender_id: UUID
    tender_analysis: Any
    eligibility: Any
    compliance: Any
    execution_trace: list[str]
    report_inputs_ready: bool


def _trace(state: ReadinessWorkflowState, node: str) -> list[str]:
    return [*state.get("execution_trace", []), node]


def _make_agent_node(agent, output_key: str, node_name: str):
    def node(state: ReadinessWorkflowState) -> dict:
        result = agent(state["session"], state["tender_id"])
        return {
            output_key: result,
            "execution_trace": _trace(state, node_name),
        }
    return node


def _report_inputs_node(state: ReadinessWorkflowState) -> dict:
    required = ("tender_analysis", "eligibility", "compliance")
    missing = [key for key in required if state.get(key) is None]
    if missing:
        raise RuntimeError(
            "Tender analysis workflow did not produce all required outputs: "
            + ", ".join(missing)
        )
    return {
        "report_inputs_ready": True,
        "execution_trace": _trace(state, "report_inputs_ready"),
    }


@lru_cache(maxsize=16)
def _compiled_readiness_graph(tender_agent, eligibility_agent, compliance_agent):
    """Compile the graph once per combination of existing agent callables."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return None

    builder = StateGraph(ReadinessWorkflowState)
    builder.add_node(
        "tender_analysis",
        _make_agent_node(tender_agent, "tender_analysis", "tender_analysis"),
    )
    builder.add_node(
        "eligibility",
        _make_agent_node(eligibility_agent, "eligibility", "eligibility"),
    )
    builder.add_node(
        "compliance",
        _make_agent_node(compliance_agent, "compliance", "compliance"),
    )
    builder.add_node("report_inputs_ready", _report_inputs_node)
    builder.add_edge(START, "tender_analysis")
    builder.add_edge("tender_analysis", "eligibility")
    builder.add_edge("eligibility", "compliance")
    builder.add_edge("compliance", "report_inputs_ready")
    builder.add_edge("report_inputs_ready", END)
    return builder.compile()


def run_readiness_analysis_workflow(
    session: Session,
    tender_id: UUID,
    *,
    agent_functions: tuple | None = None,
) -> dict[str, Any]:
    """Run tender, eligibility and compliance nodes and return their outputs.

    LangGraph is the normal execution path. A sequential compatibility path is
    retained for developer/test environments that have not installed the new
    dependency yet; install requirements.txt to activate the compiled graph.
    """
    agents = agent_functions or (analyze_tender, analyze_eligibility, analyze_compliance)
    tender_agent, eligibility_agent, compliance_agent = agents
    graph = _compiled_readiness_graph(*agents)
    initial_state: ReadinessWorkflowState = {
        "session": session,
        "tender_id": tender_id,
        "execution_trace": [],
    }

    if graph is None:
        logger.warning(
            "LangGraph is not installed; using sequential readiness workflow fallback."
        )
        state = _make_agent_node(tender_agent, "tender_analysis", "tender_analysis")(initial_state)
        state = {**initial_state, **state}
        next_state = _make_agent_node(eligibility_agent, "eligibility", "eligibility")(state)
        state = {**state, **next_state}
        next_state = _make_agent_node(compliance_agent, "compliance", "compliance")(state)
        state = {**state, **next_state}
        next_state = _report_inputs_node(state)
        state = {**state, **next_state}
        engine = "sequential_fallback"
    else:
        state = graph.invoke(initial_state)
        engine = "langgraph"

    return {
        "tender_analysis": state["tender_analysis"],
        "eligibility": state["eligibility"],
        "compliance": state["compliance"],
        "execution_trace": state.get("execution_trace", []),
        "orchestration_engine": engine,
    }
