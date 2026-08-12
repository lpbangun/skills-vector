"""Deterministic adaptive planning over approved investigation capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .domain import EvidenceCategory, RoleBriefRequest
from .persistence import BacklogItem, BacklogState


class ResearchDepth(StrEnum):
    SKIP = "skip"
    SHALLOW = "shallow"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class ResearchStep:
    lens: EvidenceCategory
    depth: ResearchDepth
    candidate_ids: tuple[str, ...] = ()

    @property
    def node(self) -> str:
        suffix = {
            EvidenceCategory.PUBLIC_LABOR_DATA: "public_labor_data",
            EvidenceCategory.RESEARCH_PAPER: "papers",
            EvidenceCategory.CREDIBLE_REPORT: "credible_reports",
            EvidenceCategory.JOB_POSTING_SIGNAL: "job_posting_signals",
            EvidenceCategory.OFFICIAL_POLICY: "official_policy",
        }[self.lens]
        return f"research_{suffix}"


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    research: tuple[ResearchStep, ...]
    analysis_nodes: tuple[str, ...]
    reason: str


_DEFAULT_LENSES = {
    "recruiter": (EvidenceCategory.PUBLIC_LABOR_DATA, EvidenceCategory.JOB_POSTING_SIGNAL),
    "hr_coordinator": (EvidenceCategory.PUBLIC_LABOR_DATA, EvidenceCategory.OFFICIAL_POLICY),
    "learning_and_development_specialist": (EvidenceCategory.RESEARCH_PAPER, EvidenceCategory.CREDIBLE_REPORT),
}


class AdaptivePlanner:
    """Select the smallest useful bounded plan; never invents evidence categories."""

    def __init__(self, *, freshness_days: int = 14, max_deep_lenses: int = 2) -> None:
        self.freshness_days = freshness_days
        self.max_deep_lenses = max_deep_lenses

    def plan(
        self,
        request: RoleBriefRequest,
        backlog: tuple[BacklogItem, ...],
        *,
        has_prior_brief: bool = False,
        open_disagreements: bool = False,
        full_refresh: bool = False,
    ) -> InvestigationPlan:
        if full_refresh:
            research = tuple(ResearchStep(lens, ResearchDepth.DEEP) for lens in EvidenceCategory)
            return InvestigationPlan(research, self._analysis(open_disagreements), "explicit full refresh")

        queued = tuple(item for item in backlog if item.state is BacklogState.QUEUED)
        if queued:
            by_lens: dict[EvidenceCategory, list[str]] = {}
            for item in queued:
                by_lens.setdefault(item.lens, []).append(item.candidate_id)
            research = tuple(
                ResearchStep(lens, ResearchDepth.SHALLOW, tuple(candidate_ids))
                for lens, candidate_ids in by_lens.items()
            )
            return InvestigationPlan(research, self._analysis(open_disagreements), "queued delta candidates")

        incorporated = tuple(item for item in backlog if item.state is BacklogState.INCORPORATED)
        fresh = tuple(item for item in incorporated if self._is_fresh(item, request.as_of))
        if incorporated and len(fresh) == len(incorporated):
            analysis = self._analysis(open_disagreements) if open_disagreements else (
                "analyze_change", "analyze_durable_capabilities", "analyze_uncertainty"
            )
            return InvestigationPlan((), analysis, "fresh incorporated backlog; reuse corpus")

        if open_disagreements:
            lenses = (EvidenceCategory.CREDIBLE_REPORT, EvidenceCategory.OFFICIAL_POLICY)
        else:
            lenses = _DEFAULT_LENSES[request.role.value]
        research = tuple(
            ResearchStep(lens, ResearchDepth.DEEP)
            for lens in lenses[: self.max_deep_lenses]
        )
        reason = "stale backlog; bounded deep refresh" if incorporated or has_prior_brief else "initial bounded investigation"
        return InvestigationPlan(research, self._analysis(open_disagreements), reason)

    def _is_fresh(self, item: BacklogItem, as_of: date) -> bool:
        anchor = item.source.published_on or item.updated_at.date()
        return 0 <= (as_of - anchor).days <= self.freshness_days

    @staticmethod
    def _analysis(open_disagreements: bool) -> tuple[str, ...]:
        nodes = ("analyze_change", "analyze_durable_capabilities", "analyze_uncertainty")
        return (*nodes, "skeptic") if open_disagreements else nodes
