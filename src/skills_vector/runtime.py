"""Swappable agent-runtime boundary for investigation research and analysis."""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory
from typing import Any, Callable, Protocol

from .domain import Claim, ClaimImpact, ClaimKind, EvidenceCategory, EvidenceSource, RoleBriefRequest

COMPOSER_MODEL = "composer-2.5"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    run_id: str
    node: str
    request: RoleBriefRequest
    depth: str = "deep"
    candidate_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceSource, ...] = ()
    candidate_sources: tuple[EvidenceSource, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentResult:
    evidence: tuple[EvidenceSource, ...] = ()
    claims: tuple[Claim, ...] = ()
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, request: AgentRequest) -> AgentResult: ...


_LENS_BY_NODE = {
    "research_public_labor_data": EvidenceCategory.PUBLIC_LABOR_DATA,
    "research_papers": EvidenceCategory.RESEARCH_PAPER,
    "research_credible_reports": EvidenceCategory.CREDIBLE_REPORT,
    "research_job_posting_signals": EvidenceCategory.JOB_POSTING_SIGNAL,
    "research_official_policy": EvidenceCategory.OFFICIAL_POLICY,
}

_KIND_BY_NODE = {
    "analyze_change": ClaimKind.CHANGE,
    "analyze_durable_capabilities": ClaimKind.DURABLE_CAPABILITY,
    "analyze_uncertainty": ClaimKind.UNCERTAINTY,
}


class DeterministicStubRuntime:
    """Offline runtime with observable calls and domain-valid deterministic output."""

    name = "deterministic-stub"

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        if request.node in _LENS_BY_NODE:
            if request.depth == "skip":
                return AgentResult(metadata={"depth": "skip"})
            if request.candidate_sources:
                return AgentResult(evidence=request.candidate_sources, metadata={"depth": request.depth})
            lens = _LENS_BY_NODE[request.node]
            slug = lens.value.replace("_", "-")
            source = EvidenceSource(
                source_id=f"stub-{request.request.role.value}-{slug}",
                category=lens,
                title=f"Offline {lens.value.replace('_', ' ')} fixture",
                publisher=f"Fixture publisher {lens.value}",
                url=f"https://fixtures.skills-vector.invalid/{request.request.role.value}/{slug}",
                published_on=request.request.as_of,
                retrieved_at=datetime.combine(request.request.as_of, datetime.min.time(), tzinfo=UTC),
            )
            return AgentResult(evidence=(source,), metadata={"depth": request.depth})
        if request.node in _KIND_BY_NODE:
            if not request.evidence:
                raise ValueError("analysis requires evidence")
            kind = _KIND_BY_NODE[request.node]
            source_ids = tuple(source.source_id for source in request.evidence[:2])
            claim = Claim(
                claim_id=f"{request.run_id[:8]}-{kind.value}",
                kind=kind,
                statement=f"Offline {kind.value.replace('_', ' ')} finding for {request.request.role.value}.",
                evidence_ids=source_ids,
                impact=ClaimImpact.ROUTINE,
                uncertainty_note="Fixture evidence is deterministic and does not establish a live market estimate.",
            )
            return AgentResult(claims=(claim,))
        if request.node == "skeptic":
            return AgentResult(text="No unresolved fixture disagreement.")
        if request.node == "forecast_panel":
            return AgentResult(text="Near-term conditions remain evidence-dependent.")
        if request.node == "draft_brief":
            return AgentResult(text=f"Private evidence-led brief for {request.request.role.value}.")
        raise ValueError(f"unsupported agent node: {request.node}")


class CallableRuntime:
    """Adapter for an OMP or local callable with the same typed contract."""

    def __init__(self, callback: Callable[[AgentRequest], AgentResult], *, name: str = "omp-local") -> None:
        self._callback = callback
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, request: AgentRequest) -> AgentResult:
        return self._callback(request)


class CursorComposerRuntime:
    """Cursor Python SDK adapter. Construction is lazy so offline imports stay clean."""

    name = "cursor-composer-2.5"


    def run(self, request: AgentRequest) -> AgentResult:
        from cursor_sdk import Agent, LocalAgentOptions  # type: ignore[import-not-found]

        prompt = self._prompt(request)
        with TemporaryDirectory(prefix="skills-vector-agent-") as workspace:
            with Agent.create(model=COMPOSER_MODEL, local=LocalAgentOptions(cwd=workspace)) as agent:
                raw = agent.send(prompt).text()
        return self._decode(raw)

    @staticmethod
    def _prompt(request: AgentRequest) -> str:
        payload = {
            "node": request.node,
            "role": request.request.role.value,
            "as_of": request.request.as_of.isoformat(),
            "geography": request.request.geography,
            "depth": request.depth,
            "candidate_ids": request.candidate_ids,
            "evidence": [CursorComposerRuntime._source_payload(source) for source in request.evidence],
            "candidate_sources": [
                CursorComposerRuntime._source_payload(source) for source in request.candidate_sources
            ],
        }
        return (
            "Operate only as the named Skills Vector research/analysis capability. "
            "Do not modify prompts, policy, memory, routing, code, or publication state. "
            "Return one JSON object with keys evidence, claims, text, metadata. "
            f"Input: {json.dumps(payload, sort_keys=True)}"
        )

    @staticmethod
    def _source_payload(source: EvidenceSource) -> dict[str, Any]:
        return {
            "source_id": source.source_id,
            "category": source.category.value,
            "title": source.title,
            "publisher": source.publisher,
            "url": source.url,
            "published_on": source.published_on.isoformat() if source.published_on else None,
            "retrieved_at": source.retrieved_at.isoformat(),
            "geography": source.geography,
        }

    @staticmethod
    def _decode(raw: str) -> AgentResult:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Composer returned invalid JSON: {exc}") from exc
        evidence = tuple(
            CursorComposerRuntime._decode_evidence(item)
            for item in data.get("evidence", ())
        )
        claims = tuple(
            CursorComposerRuntime._decode_claim(item)
            for item in data.get("claims", ())
        )
        return AgentResult(evidence, claims, data.get("text", ""), data.get("metadata", {}))

    @staticmethod
    def _decode_evidence(item: dict[str, Any]) -> EvidenceSource:
        missing = [k for k in ("source_id", "category", "title", "publisher", "url", "retrieved_at") if k not in item]
        if missing:
            raise ValueError(f"Composer evidence item missing required fields: {', '.join(missing)}")
        return EvidenceSource(
            source_id=item["source_id"],
            category=EvidenceCategory(item["category"]),
            title=item["title"],
            publisher=item["publisher"],
            url=item["url"],
            published_on=date.fromisoformat(item["published_on"]) if item.get("published_on") else None,
            retrieved_at=datetime.fromisoformat(item["retrieved_at"]),
            geography=item.get("geography", "US"),
        )

    @staticmethod
    def _decode_claim(item: dict[str, Any]) -> Claim:
        missing = [k for k in ("claim_id", "kind", "statement", "evidence_ids", "uncertainty_note") if k not in item]
        if missing:
            raise ValueError(f"Composer claim item missing required fields: {', '.join(missing)}")
        return Claim(
            claim_id=item["claim_id"],
            kind=ClaimKind(item["kind"]),
            statement=item["statement"],
            evidence_ids=tuple(item["evidence_ids"]),
            impact=ClaimImpact(item.get("impact", ClaimImpact.ROUTINE.value)),
            uncertainty_note=item["uncertainty_note"],
            disagreement_note=item.get("disagreement_note", ""),
        )


def select_runtime(
    mode: str = "auto",
    *,
    omp_runtime: AgentRuntime | None = None,
) -> AgentRuntime:
    """Select Composer when usable; otherwise remain deterministic and offline."""

    normalized = mode.casefold()
    if normalized == "stub":
        return DeterministicStubRuntime()
    if normalized in {"omp", "local"}:
        if omp_runtime is None:
            raise ValueError("OMP/local mode requires an injected runtime")
        return omp_runtime
    if normalized not in {"auto", "composer"}:
        raise ValueError(f"unknown runtime mode: {mode}")
    sdk_available = importlib.util.find_spec("cursor_sdk") is not None
    credentials_available = bool(os.environ.get("CURSOR_API_KEY"))
    if sdk_available and credentials_available:
        return CursorComposerRuntime()
    if normalized == "composer":
        missing = "Cursor SDK" if not sdk_available else "CURSOR_API_KEY"
        raise RuntimeError(f"Composer runtime requested but {missing} is unavailable")
    return DeterministicStubRuntime()
