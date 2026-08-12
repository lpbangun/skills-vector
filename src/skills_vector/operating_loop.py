"""Application boundary for the private adaptive investigation operating loop."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable


from .domain import Claim, EvidenceSource, Role, RoleBrief, RoleBriefRequest, Scenario, ScenarioHorizon, validate_brief
from .persistence import ArtifactRecord, BacklogState, InvestigationStore, NodeEvent
from .planning import AdaptivePlanner, InvestigationPlan
from .runtime import AgentRequest, AgentRuntime, select_runtime
from .workflow import ANALYSIS_NODES, WorkflowHandlers, build_graph


@dataclass(frozen=True, slots=True)
class ScoutCandidate:
    source: EvidenceSource


@dataclass(frozen=True, slots=True)
class InvestigationRunResult:
    run_id: str
    draft: RoleBrief
    plan: InvestigationPlan
    paused_before: str | None
    events: tuple[NodeEvent, ...]
    artifacts: tuple[ArtifactRecord, ...]


class WeeklyDeltaScout:
    """Bounded candidate ingestion. It cannot access claims or brief state."""

    def __init__(self, store: InvestigationStore, *, max_candidates: int = 25) -> None:
        self.store = store
        self.max_candidates = max_candidates

    def enqueue(self, request: RoleBriefRequest, candidates: Iterable[ScoutCandidate]) -> tuple[str, ...]:
        candidate_list = tuple(candidates)
        if len(candidate_list) > self.max_candidates:
            raise ValueError(f"weekly scout is bounded to {self.max_candidates} candidates")
        inserted: list[str] = []
        for candidate in candidate_list:
            source = candidate.source
            if source.geography != request.geography:
                raise ValueError("scout candidate is outside the request geography")
            item, created = self.store.enqueue_candidate(
                request.role, source.category, source, _content_hash(source)
            )
            if created:
                inserted.append(item.candidate_id)
        return tuple(inserted)


class InvestigationHandlers:
    """Injected handlers that trace logical agent nodes and persist artifacts."""

    def __init__(
        self,
        store: InvestigationStore,
        runtime: AgentRuntime,
        planner: AdaptivePlanner,
        *,
        open_disagreements: bool = False,
        full_refresh: bool = False,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.planner = planner
        self.open_disagreements = open_disagreements
        self.full_refresh = full_refresh

    def bind(self) -> WorkflowHandlers:
        return WorkflowHandlers(
            scope_request=self.scope_request,
            plan_investigation=self.plan_investigation,
            research=self.research,
            validate_evidence=self.validate_evidence,
            analyze=self.analyze,
            forecast_panel=self.forecast_panel,
            draft_brief=self.draft_brief,
            human_review=self.human_review,
        )

    def scope_request(self, state: dict[str, Any]) -> dict[str, Any]:
        request = state["request"]
        if not isinstance(request, RoleBriefRequest):
            raise TypeError("workflow requires RoleBriefRequest")
        self._event(state, "scope_request", "completed", details={"role": request.role.value})
        return {}

    def plan_investigation(self, state: dict[str, Any]) -> dict[str, Any]:
        request = state["request"]
        plan = self.planner.plan(
            request,
            self.store.list_backlog(request.role),
            has_prior_brief=self.store.has_prior_draft(request.role),
            open_disagreements=self.open_disagreements,
            full_refresh=self.full_refresh,
        )
        artifact_id = self.store.write_artifact(state["run_id"], "plan_investigation", "plan", plan)
        self._event(
            state,
            "plan_investigation",
            "completed",
            artifact_ids=(artifact_id,),
            details={"reason": plan.reason, "research_nodes": [step.node for step in plan.research]},
        )
        return {"plan": plan}

    def research(self, state: dict[str, Any]) -> dict[str, Any]:
        request: RoleBriefRequest = state["request"]
        plan: InvestigationPlan = state["plan"]
        incorporated = list(
            item.source
            for item in self.store.list_backlog(request.role, (BacklogState.INCORPORATED,))
        )
        gathered: list[EvidenceSource] = []
        for step in plan.research:
            candidate_items = tuple(self.store.backlog_item(candidate_id) for candidate_id in step.candidate_ids)
            for item in candidate_items:
                self.store.transition_backlog(item.candidate_id, BacklogState.RESEARCHING)
            self._event(state, step.node, "started", details={"depth": step.depth.value, "candidate_ids": step.candidate_ids})
            try:
                result = self.runtime.run(
                    AgentRequest(
                        state["run_id"], step.node, request, step.depth.value, step.candidate_ids,
                        candidate_sources=tuple(item.source for item in candidate_items),
                    )
                )
                accepted: list[EvidenceSource] = []
                for source in result.evidence:
                    if source.category is not step.lens:
                        raise ValueError(f"{step.node} returned evidence for another lens")
                    item, _created = self.store.enqueue_candidate(
                        request.role, source.category, source, _content_hash(source)
                    )
                    if item.state is BacklogState.REJECTED:
                        continue
                    if item.state is BacklogState.QUEUED:
                        item = self.store.transition_backlog(item.candidate_id, BacklogState.RESEARCHING)
                    if item.state is BacklogState.RESEARCHING:
                        self.store.transition_backlog(item.candidate_id, BacklogState.INCORPORATED)
                    accepted.append(source)
            finally:
                for item in candidate_items:
                    current = self.store.backlog_item(item.candidate_id)
                    if current.state is BacklogState.RESEARCHING:
                        self.store.transition_backlog(item.candidate_id, BacklogState.QUEUED)
            gathered.extend(accepted)
            artifact_ids: list[str] = []
            if accepted:
                artifact_ids.append(self.store.write_artifact(state["run_id"], step.node, "evidence", accepted))
            self._event(
                state,
                step.node,
                "completed",
                artifact_ids=artifact_ids,
                details={
                    "depth": step.depth.value,
                    "source_ids": [source.source_id for source in accepted],
                    "rejected_sources": len(result.evidence) - len(accepted),
                },
            )
        evidence_by_id = {source.source_id: source for source in (*incorporated, *gathered)}
        self._event(
            state,
            "research",
            "completed",
            details={"selected_lenses": len(plan.research), "reused_sources": len(incorporated)},
        )
        return {"evidence": list(evidence_by_id.values())}

    def validate_evidence(self, state: dict[str, Any]) -> dict[str, Any]:
        evidence: list[EvidenceSource] = state.get("evidence", [])
        if not evidence:
            raise ValueError("investigation requires evidence before analysis")
        source_ids = [source.source_id for source in evidence]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("evidence source ids must be unique")
        artifact_id = self.store.write_artifact(state["run_id"], "validate_evidence", "evidence_corpus", evidence)
        self._event(state, "validate_evidence", "completed", artifact_ids=(artifact_id,), details={"sources": len(evidence)})
        return {}

    def analyze(self, state: dict[str, Any]) -> dict[str, Any]:
        plan: InvestigationPlan = state["plan"]
        request: RoleBriefRequest = state["request"]
        evidence = tuple(state.get("evidence", ()))
        unsupported = set(plan.analysis_nodes) - set(ANALYSIS_NODES)
        if unsupported:
            raise ValueError(f"plan contains unsupported analysis capabilities: {', '.join(sorted(unsupported))}")
        claims: list[Claim] = []
        for node in plan.analysis_nodes:
            self._event(state, node, "started")
            result = self.runtime.run(AgentRequest(state["run_id"], node, request, evidence=evidence))
            claims.extend(result.claims)
            artifact_ids: tuple[str, ...] = ()
            if result.claims:
                artifact_ids = (self.store.write_artifact(state["run_id"], node, "claims", result.claims),)
            self._event(state, node, "completed", artifact_ids=artifact_ids, details={"claims": len(result.claims)})
        return {"claims": claims}

    def forecast_panel(self, state: dict[str, Any]) -> dict[str, Any]:
        request: RoleBriefRequest = state["request"]
        evidence = tuple(state.get("evidence", ()))
        self._event(state, "forecast_panel", "started")
        result = self.runtime.run(AgentRequest(state["run_id"], "forecast_panel", request, evidence=evidence))
        artifact_id = self.store.write_artifact(
            state["run_id"], "forecast_panel", "forecast",
            {"text": result.text, "evidence_ids": [source.source_id for source in evidence]},
        )
        self._event(state, "forecast_panel", "completed", artifact_ids=(artifact_id,))
        return {}

    def draft_brief(self, state: dict[str, Any]) -> dict[str, Any]:
        request: RoleBriefRequest = state["request"]
        evidence = tuple(state.get("evidence", ()))
        claims = tuple(state.get("claims", ()))
        self._event(state, "draft_brief", "started")
        result = self.runtime.run(AgentRequest(state["run_id"], "draft_brief", request, evidence=evidence))
        scenario = Scenario(
            ScenarioHorizon.NEAR_TERM,
            "Evidence suggests bounded near-term change; direction and magnitude remain uncertain.",
            tuple(source.source_id for source in evidence),
            "This scenario is role-level, time-bound, and not an individual career prediction.",
        )
        draft = RoleBrief(request, result.text, claims, (scenario,), evidence, private=True)
        errors = validate_brief(draft)
        if errors:
            raise ValueError("; ".join(errors))
        artifact_id = self.store.write_artifact(state["run_id"], "draft_brief", "role_brief", draft)
        self._event(state, "draft_brief", "completed", artifact_ids=(artifact_id,), details={"private": draft.private})
        self.store.set_run_status(state["run_id"], "awaiting_human_review")
        return {"draft": draft}

    @staticmethod
    def human_review(_state: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("graph must interrupt before human_review")

    def _event(
        self,
        state: dict[str, Any],
        node: str,
        status: str,
        *,
        artifact_ids: tuple[str, ...] | list[str] = (),
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.append_event(state["run_id"], node, status, artifact_ids=artifact_ids, details=details)


def run_recruiter_investigation(
    store: InvestigationStore,
    *,
    as_of: date,
    runtime: AgentRuntime | None = None,
    planner: AdaptivePlanner | None = None,
    open_disagreements: bool = False,
    full_refresh: bool = False,
) -> InvestigationRunResult:
    """Run the private Recruiter path until the mandatory human-review interrupt."""

    request = RoleBriefRequest(Role.RECRUITER, as_of)
    record = store.start_run(request.role)
    selected_runtime = runtime or select_runtime()
    handlers = InvestigationHandlers(
        store,
        selected_runtime,
        planner or AdaptivePlanner(),
        open_disagreements=open_disagreements,
        full_refresh=full_refresh,
    )
    graph = build_graph(handlers.bind())
    try:
        final_state = graph.invoke({"run_id": record.run_id, "request": request})
    except Exception:
        store.set_run_status(record.run_id, "failed")
        store.append_event(record.run_id, "workflow", "failed")
        raise
    draft = final_state.get("draft")
    plan = final_state.get("plan")
    if not isinstance(draft, RoleBrief) or not isinstance(plan, InvestigationPlan):
        raise RuntimeError("investigation did not reach a persisted draft")
    paused_before = "human_review"
    return InvestigationRunResult(
        record.run_id,
        draft,
        plan,
        paused_before,
        store.events(record.run_id),
        store.artifacts(record.run_id),
    )


def _content_hash(source: EvidenceSource) -> str:
    value = "\x1f".join(
        (
            source.url,
            source.publisher.casefold(),
            source.published_on.isoformat() if source.published_on else "",
            source.title,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
