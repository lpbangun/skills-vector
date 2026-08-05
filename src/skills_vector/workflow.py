"""Controlled LangGraph topology for the Skills Vector MVP.

Handlers are injected by the application boundary. This module owns sequencing and
the human gate; it does not grant tools, select providers, or modify its own policy.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

from .domain import EvidenceSource, HumanReview, RoleBriefRequest


NodeHandler = Callable[[dict[str, Any]], dict[str, Any]]

RESEARCH_NODES = (
    "research_public_labor_data",
    "research_papers",
    "research_credible_reports",
    "research_job_posting_signals",
    "research_official_policy",
)

ANALYSIS_NODES = (
    "analyze_demand",
    "analyze_tasks_automation",
    "analyze_skill_shifts",
    "analyze_role_evolution_durability",
    "skeptic",
)

HUMAN_GATED_CHANGES = frozenset({"prompt", "policy", "memory", "model_router"})


class WorkflowState(TypedDict, total=False):
    request: RoleBriefRequest
    evidence: Annotated[list[EvidenceSource], operator.add]
    artifacts: Annotated[list[dict[str, Any]], operator.add]
    analyses: Annotated[list[dict[str, Any]], operator.add]
    validated_evidence_ids: list[str]
    forecast: dict[str, Any]
    brief_payload: dict[str, Any]
    review: HumanReview
    errors: Annotated[list[str], operator.add]


@dataclass(frozen=True, slots=True)
class WorkflowHandlers:
    scope_request: NodeHandler
    research_public_labor_data: NodeHandler
    research_papers: NodeHandler
    research_credible_reports: NodeHandler
    research_job_posting_signals: NodeHandler
    research_official_policy: NodeHandler
    validate_evidence: NodeHandler
    analyze_demand: NodeHandler
    analyze_tasks_automation: NodeHandler
    analyze_skill_shifts: NodeHandler
    analyze_role_evolution_durability: NodeHandler
    skeptic: NodeHandler
    forecast_panel: NodeHandler
    draft_brief: NodeHandler
    human_review: NodeHandler


WORKFLOW_EDGES: tuple[tuple[str | tuple[str, ...], str], ...] = (
    ("START", "scope_request"),
    ("scope_request", RESEARCH_NODES),
    (RESEARCH_NODES, "validate_evidence"),
    ("validate_evidence", ANALYSIS_NODES),
    (ANALYSIS_NODES, "forecast_panel"),
    ("forecast_panel", "draft_brief"),
    ("draft_brief", "human_review"),
    ("human_review", "END"),
)


def build_graph(handlers: WorkflowHandlers, *, checkpointer: Any = None) -> Any:
    """Compile the controlled graph, pausing before the human-review node.

    No default handlers exist deliberately: the graph caller must explicitly bind
    every narrow capability and its allowed tools.
    """

    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(WorkflowState)
    for name in ("scope_request", *RESEARCH_NODES, "validate_evidence", *ANALYSIS_NODES,
                 "forecast_panel", "draft_brief", "human_review"):
        graph.add_node(name, getattr(handlers, name))

    graph.add_edge(START, "scope_request")
    for node in RESEARCH_NODES:
        graph.add_edge("scope_request", node)
    graph.add_edge(list(RESEARCH_NODES), "validate_evidence")
    for node in ANALYSIS_NODES:
        graph.add_edge("validate_evidence", node)
    graph.add_edge(list(ANALYSIS_NODES), "forecast_panel")
    graph.add_edge("forecast_panel", "draft_brief")
    graph.add_edge("draft_brief", "human_review")
    graph.add_edge("human_review", END)

    compile_options: dict[str, Any] = {"interrupt_before": ["human_review"]}
    if checkpointer is not None:
        compile_options["checkpointer"] = checkpointer
    return graph.compile(**compile_options)
