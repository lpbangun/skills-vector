"""Controlled adaptive LangGraph topology for private role investigations.

Handlers remain injected by the application boundary. This module owns sequencing
and the human gate; it never selects providers or grants tools.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

from .domain import Claim, EvidenceSource, HumanReview, RoleBrief, RoleBriefRequest
from .planning import InvestigationPlan


NodeHandler = Callable[[dict[str, Any]], dict[str, Any]]

RESEARCH_NODES = (
    "research_public_labor_data",
    "research_papers",
    "research_credible_reports",
    "research_job_posting_signals",
    "research_official_policy",
)

ANALYSIS_NODES = (
    "analyze_change",
    "analyze_durable_capabilities",
    "analyze_uncertainty",
    "skeptic",
)

HUMAN_GATED_CHANGES = frozenset({"prompt", "policy", "memory", "model_router", "code"})


class WorkflowState(TypedDict, total=False):
    run_id: str
    request: RoleBriefRequest
    plan: InvestigationPlan
    evidence: Annotated[list[EvidenceSource], operator.add]
    claims: Annotated[list[Claim], operator.add]
    draft: RoleBrief
    review: HumanReview
    errors: Annotated[list[str], operator.add]


@dataclass(frozen=True, slots=True)
class WorkflowHandlers:
    scope_request: NodeHandler
    plan_investigation: NodeHandler
    research: NodeHandler
    validate_evidence: NodeHandler
    analyze: NodeHandler
    forecast_panel: NodeHandler
    draft_brief: NodeHandler
    human_review: NodeHandler


WORKFLOW_EDGES: tuple[tuple[str, str], ...] = (
    ("START", "scope_request"),
    ("scope_request", "plan_investigation"),
    ("plan_investigation", "research"),
    ("research", "validate_evidence"),
    ("validate_evidence", "analyze"),
    ("analyze", "forecast_panel"),
    ("forecast_panel", "draft_brief"),
    ("draft_brief", "human_review"),
    ("human_review", "END"),
)


def build_graph(handlers: WorkflowHandlers, *, checkpointer: Any = None) -> Any:
    """Compile the adaptive graph, pausing before the human-review node."""

    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(WorkflowState)
    for name in (
        "scope_request",
        "plan_investigation",
        "research",
        "validate_evidence",
        "analyze",
        "forecast_panel",
        "draft_brief",
        "human_review",
    ):
        graph.add_node(name, getattr(handlers, name))

    graph.add_edge(START, "scope_request")
    graph.add_edge("scope_request", "plan_investigation")
    graph.add_edge("plan_investigation", "research")
    graph.add_edge("research", "validate_evidence")
    graph.add_edge("validate_evidence", "analyze")
    graph.add_edge("analyze", "forecast_panel")
    graph.add_edge("forecast_panel", "draft_brief")
    graph.add_edge("draft_brief", "human_review")
    graph.add_edge("human_review", END)

    compile_options: dict[str, Any] = {"interrupt_before": ["human_review"]}
    if checkpointer is not None:
        compile_options["checkpointer"] = checkpointer
    return graph.compile(**compile_options)

