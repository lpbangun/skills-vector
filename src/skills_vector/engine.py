"""Deterministic, credential-free LangGraph investigation implementation."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any
import uuid

from .domain import EvidenceCategory, EvidenceSource, Role, RoleBriefRequest, validate_ingested_evidence
from .seed import ROLE_METADATA
from .workflow import ANALYSIS_NODES, WorkflowHandlers, build_graph


CATEGORY_STAGE = {
    EvidenceCategory.PUBLIC_LABOR_DATA: ("research_public_labor_data", 20),
    EvidenceCategory.RESEARCH_PAPER: ("research_papers", 21),
    EvidenceCategory.CREDIBLE_REPORT: ("research_credible_reports", 22),
    EvidenceCategory.JOB_POSTING_SIGNAL: ("research_job_posting_signals", 23),
    EvidenceCategory.OFFICIAL_POLICY: ("research_official_policy", 24),
}


ROLE_FINDINGS: dict[Role, dict[str, Any]] = {
    Role.HR_COORDINATOR: {
        "change": "Routine coordination is becoming more AI-assisted, while record quality, exceptions, employee trust, and policy interpretation remain human-accountable.",
        "tasks": [
            "Use assisted drafting and summarization for onboarding and employee communications, with human review before action.",
            "Shift time from repetitive document handling toward data-quality checks, exception resolution, and cross-functional follow-through.",
        ],
        "skills": [
            "Move from basic system entry toward data literacy, workflow design, quality assurance, and clear escalation decisions.",
            "Develop ethical and regulatory reasoning for employee-data and AI-supported People Operations workflows.",
        ],
        "scenario": "By {horizon}, a typical U.S. HR Coordinator uses approved assistants inside core HR workflows for drafts, summaries, and checks, then owns verification, exceptions, and employee-facing resolution.",
    },
    Role.RECRUITER: {
        "change": "Recruiting tools increasingly assist sourcing, drafting, and screening workflows, raising the value of evidence-based selection, candidate trust, and bias/accessibility governance.",
        "tasks": [
            "Use assistants to draft and adapt outreach and job content while verifying accuracy, accessibility, and role relevance.",
            "Spend more effort on structured intake, candidate relationship judgment, selection quality, and audit-ready oversight of screening tools.",
        ],
        "skills": [
            "Build data literacy for funnel quality and adverse-impact signals rather than optimizing activity volume alone.",
            "Develop human-AI workflow design, stakeholder influence, and ethical/regulatory reasoning for selection decisions.",
        ],
        "scenario": "By {horizon}, a typical U.S. Recruiter operates an assisted hiring workflow but remains accountable for job requirements, candidate communication, selection evidence, accessibility, and escalation.",
    },
    Role.LEARNING_AND_DEVELOPMENT_SPECIALIST: {
        "change": "AI can accelerate content adaptation and needs analysis, while learning diagnosis, facilitation, transfer to work, evaluation, and responsible adoption become more important.",
        "tasks": [
            "Use assistants to generate first-pass learning assets and variations, then validate accuracy, accessibility, and instructional fit.",
            "Shift effort toward capability diagnosis, practice design, facilitation, evaluation, and change support for new human-AI workflows.",
        ],
        "skills": [
            "Add AI literacy, data-informed evaluation, and human-AI learning design to established instructional and facilitation skills.",
            "Strengthen systems thinking and change facilitation so learning programs connect tool adoption to real work outcomes.",
        ],
        "scenario": "By {horizon}, a typical U.S. L&D Specialist uses assistants for content prototyping and personalization while owning needs diagnosis, learning quality, facilitation, transfer, and evaluation.",
    },
}


DURABLE_CAPABILITIES = (
    "Judgment under uncertainty",
    "Systems thinking",
    "Data literacy",
    "Stakeholder influence",
    "Ethical and regulatory reasoning",
    "Change facilitation",
    "Human-AI workflow design",
)


def _artifact(stage: str, order: int, payload: dict[str, Any], kind: str = "stage") -> dict[str, Any]:
    return {"stage": stage, "order": order, "kind": kind, "payload": payload}


def _as_source(row: dict[str, Any], role: Role) -> EvidenceSource:
    return EvidenceSource(
        source_id=row["source_id"], category=EvidenceCategory(row["category"]), title=row["title"],
        publisher=row["publisher"], url=row["url"], published_on=date.fromisoformat(row["published_on"]),
        retrieved_at=datetime.now(UTC), geography=row["geography"], relevant_excerpt=row["relevant_excerpt"],
        provenance=row["provenance"], role_connections=(role,),
    )


def _claim(
    claim_key: str,
    section: str,
    statement: str,
    evidence_ids: list[str],
    uncertainty: str,
    *,
    cluster_key: str,
    topic_tags: tuple[str, ...],
    finding_keys: tuple[str, ...],
    disagreement: str = "",
) -> dict[str, Any]:
    return {
        "claim_key": claim_key,
        "section": section,
        "cluster_key": cluster_key,
        "topic_tags": list(topic_tags),
        "finding_keys": list(finding_keys),
        "statement": statement,
        "evidence_ids": evidence_ids,
        "uncertainty": uncertainty,
        "disagreement": disagreement,
        "validation_status": "validated" if evidence_ids else "blocked",
    }


def _evidence_ids(evidence: list[EvidenceSource], *categories: EvidenceCategory) -> list[str]:
    selected = [source.source_id for source in evidence if source.category in categories]
    return selected


def validate_brief_payload(
    brief: dict[str, Any], evidence: list[EvidenceSource], *, allow_partial: bool = False
) -> None:
    required = {"role_context", "what_is_changing", "task_shifts", "skill_shifts", "durable_capabilities",
                "scenario", "sources", "counter_evidence", "uncertainty_summary", "forecast"}
    missing = sorted(required - brief.keys())
    if missing:
        raise ValueError("brief missing sections: " + ", ".join(missing))
    evidence_by_id = {source.source_id: source for source in evidence}
    if len(evidence_by_id) != len(evidence):
        raise ValueError("evidence source ids must be unique")
    claims: list[dict[str, Any]] = [brief["what_is_changing"], *brief["task_shifts"], *brief["skill_shifts"],
                                    *brief["durable_capabilities"], *brief["counter_evidence"], brief["scenario"]]
    for claim in claims:
        if not str(claim.get("statement", "")).strip():
            raise ValueError("material claims require a statement")
        ids = claim.get("evidence_ids", [])
        if not ids and allow_partial and claim.get("validation_status") == "blocked":
            continue
        if not ids:
            raise ValueError(f"unsupported material claim: {claim.get('statement', '')}")
        unknown = sorted(set(ids) - evidence_by_id.keys())
        if unknown:
            raise ValueError("material claim references missing evidence: " + ", ".join(unknown))
        if not str(claim.get("uncertainty", "")).strip():
            raise ValueError("material claims must expose uncertainty")


def build_deterministic_handlers(role: Role, rows: list[dict[str, Any]], *, as_of: date) -> WorkflowHandlers:
    sources = [_as_source(row, role) for row in rows]
    profile = ROLE_FINDINGS[role]
    metadata = ROLE_METADATA[role]
    horizon = as_of.replace(year=as_of.year + 2).isoformat()

    def scope_request(state: dict[str, Any]) -> dict[str, Any]:
        request: RoleBriefRequest = state["request"]
        return {"artifacts": [_artifact("scope_request", 10, {
            "role": request.role.value, "geography": request.geography,
            "domain": "People Operations & Talent", "as_of": request.as_of.isoformat(),
            "mode": "deterministic_offline",
        })]}

    def research(category: EvidenceCategory):
        stage, order = CATEGORY_STAGE[category]
        def run(_state: dict[str, Any]) -> dict[str, Any]:
            selected = [source for source in sources if source.category is category]
            status = "succeeded" if selected else "failed"
            message = "" if selected else f"{stage}: no approved source was available"
            return {
                "evidence": selected,
                "artifacts": [_artifact(stage, order, {
                    "category": category.value, "source_count": len(selected),
                    "source_ids": [source.source_id for source in selected],
                    "finding": selected[0].relevant_excerpt if selected else "No approved source was available.",
                    "status": status,
                }, "research")],
                **({"errors": [message]} if message else {}),
            }
        return run

    def validate_evidence(state: dict[str, Any]) -> dict[str, Any]:
        found: list[EvidenceSource] = state.get("evidence", [])
        errors = [error for source in found for error in validate_ingested_evidence(source, role=role)]
        present = {source.category for source in found}
        missing = set(EvidenceCategory) - present
        if missing:
            errors.append("missing approved evidence lenses: " + ", ".join(sorted(item.value for item in missing)))
        ids = [
            source.source_id
            for source in found
            if not validate_ingested_evidence(source, role=role)
        ]
        status = "passed" if not errors else "failed"
        return {
            "validated_evidence_ids": ids,
            "artifacts": [_artifact("validate_evidence", 30, {
                "status": status, "validated_count": len(ids),
                "approved_categories": sorted(item.value for item in present),
                "checks": ["required metadata", "public URL", "U.S. role connection", "approved category"],
                "errors": errors,
            }, "validation")],
            **({"errors": [f"validate_evidence: {error}" for error in errors]} if errors else {}),
        }

    def analysis(name: str, order: int, finding: str, ids: list[str], uncertainty: str):
        def run(state: dict[str, Any]) -> dict[str, Any]:
            validated = set(state.get("validated_evidence_ids", []))
            available_ids = [source_id for source_id in ids if source_id in validated]
            if not available_ids:
                message = f"{name}: no validated evidence remained for this lens"
                return {
                    "errors": [message],
                    "artifacts": [_artifact(name, order, {
                        "lens": name,
                        "finding": "Analysis unavailable because its required evidence did not validate.",
                        "evidence_ids": [],
                        "uncertainty": uncertainty,
                        "status": "failed",
                        "error": message,
                    }, "analysis")],
                }
            record = {
                "lens": name,
                "finding": finding,
                "evidence_ids": available_ids,
                "uncertainty": uncertainty,
                "status": "succeeded",
            }
            return {"analyses": [record], "artifacts": [_artifact(name, order, record, "analysis")]}
        return run

    labor_ids = _evidence_ids(sources, EvidenceCategory.PUBLIC_LABOR_DATA)
    research_ids = _evidence_ids(sources, EvidenceCategory.RESEARCH_PAPER)
    report_ids = _evidence_ids(sources, EvidenceCategory.CREDIBLE_REPORT)
    posting_ids = _evidence_ids(sources, EvidenceCategory.JOB_POSTING_SIGNAL)
    policy_ids = _evidence_ids(sources, EvidenceCategory.OFFICIAL_POLICY)
    all_ids = [source.source_id for source in sources]

    def forecast_panel(state: dict[str, Any]) -> dict[str, Any]:
        panels = state.get("analyses", [])
        panel_lenses = [panel["lens"] for panel in panels]
        missing_lenses = sorted(set(ANALYSIS_NODES) - set(panel_lenses))
        forecast = {
            "horizon": horizon,
            "consensus": profile["scenario"].format(horizon=horizon),
            "panel_lenses": panel_lenses,
            "missing_lenses": missing_lenses,
            "confidence": "bounded" if not missing_lenses else "partial",
            "status": "succeeded" if not missing_lenses else "partial",
            "uncertainty": "Direction is more defensible than adoption speed; outcomes vary by employer, sector, workflow controls, and tool quality.",
        }
        return {"forecast": forecast, "artifacts": [_artifact("forecast_panel", 50, forecast, "forecast")]}

    def draft_brief(state: dict[str, Any]) -> dict[str, Any]:
        found_evidence: list[EvidenceSource] = state.get("evidence", [])
        validated_ids = set(state.get("validated_evidence_ids", []))
        evidence_by_id = {source.source_id: source for source in found_evidence if source.source_id in validated_ids}
        evidence = [evidence_by_id[source_id] for source_id in all_ids if source_id in evidence_by_id]
        keep = lambda ids: [source_id for source_id in ids if source_id in validated_ids]
        shared_uncertainty = "The evidence supports a role-level direction, not a universal employer outcome or individual prediction."
        changing = _claim(
            "what_is_changing", "what_is_changing", profile["change"],
            keep(labor_ids + report_ids + posting_ids), shared_uncertainty,
            cluster_key="role_change", topic_tags=("role_change", "adoption"),
            finding_keys=("analyze_role_evolution_durability", "analyze_demand"),
            disagreement="Positive BLS demand projections do not support a simple role-elimination narrative.",
        )
        task_claims = [_claim(
            f"task_shift.{index}", "task_shifts", item, keep(research_ids + report_ids),
            "Direct causal research is cross-occupation and may not transfer fully to this role.",
            cluster_key="task_shift", topic_tags=("task_shift", "automation"),
            finding_keys=("analyze_tasks_automation",),
        ) for index, item in enumerate(profile["tasks"])]
        skill_claims = [_claim(
            f"skill_shift.{index}", "skill_shifts", item, keep(posting_ids + policy_ids),
            "Job-posting signals are selected vacancies and do not measure every employer.",
            cluster_key="skill_shift", topic_tags=("skill_shift", "adoption"),
            finding_keys=("analyze_skill_shifts",),
        ) for index, item in enumerate(profile["skills"])]
        durable = [_claim(
            f"durable_capability.{index}", "durable_capabilities", capability,
            keep((policy_ids + labor_ids) if capability in {"Ethical and regulatory reasoning", "Stakeholder influence"} else (research_ids + report_ids)),
            "Capability importance is an evidence-led interpretation, not a measured rank ordering.",
            cluster_key="durable_capability", topic_tags=("durable_capability", "governance"),
            finding_keys=("analyze_skill_shifts", "analyze_role_evolution_durability"),
        ) for index, capability in enumerate(DURABLE_CAPABILITIES)]
        counter = [
            _claim(
                "counter_evidence.0", "counter_evidence",
                "BLS projects continued employment growth for the underlying occupation, countering claims of imminent wholesale displacement.",
                keep(labor_ids), "Occupational projections do not isolate the effect of AI.",
                cluster_key="counter_evidence", topic_tags=("role_demand", "counter_evidence"),
                finding_keys=("skeptic", "analyze_demand"),
                disagreement="Growth can coexist with substantial task redesign.",
            ),
            _claim(
                "counter_evidence.1", "counter_evidence",
                "The field evidence reports heterogeneous gains and studies customer support rather than People Operations roles.",
                keep(research_ids), "Transfer to these roles and tools is uncertain.",
                cluster_key="counter_evidence", topic_tags=("transfer_limit", "counter_evidence"),
                finding_keys=("skeptic",),
                disagreement="The result supports augmentation potential, not a role-specific effect size.",
            ),
        ]
        scenario = _claim(
            "scenario", "scenario", state["forecast"]["consensus"], keep(all_ids),
            state["forecast"]["uncertainty"], cluster_key="scenario",
            topic_tags=("scenario", "adoption"), finding_keys=("forecast_panel", "skeptic"),
            disagreement="This is a bounded scenario, not a prediction.",
        ) | {
                              "horizon_start": as_of.isoformat(), "horizon_end": horizon,
                          }
        source_payload = [{
            "source_id": source.source_id, "category": source.category.value, "title": source.title,
            "publisher": source.publisher, "url": source.url,
            "published_on": source.published_on.isoformat() if source.published_on else None,
            "relevant_excerpt": source.relevant_excerpt, "provenance": source.provenance,
            "role_connection": role.value,
        } for source in evidence]
        claims = [changing, *task_claims, *skill_claims, *durable, *counter, scenario]
        blocked_claims = [claim["claim_key"] for claim in claims if claim["validation_status"] != "validated"]
        failed_stages = sorted({error.split(":", 1)[0] for error in state.get("errors", [])})
        completeness = "partial" if failed_stages or state["forecast"].get("status") == "partial" else "complete"
        approval_eligible = not blocked_claims
        brief = {
            "role": role.value, "role_name": metadata["name"], "domain": "People Operations & Talent",
            "geography": "United States", "as_of": as_of.isoformat(), "private": True,
            "role_context": metadata["context"], "what_is_changing": changing,
            "task_shifts": task_claims, "skill_shifts": skill_claims,
            "durable_capabilities": durable, "scenario": scenario,
            "counter_evidence": counter, "forecast": state["forecast"],
            "uncertainty_summary": shared_uncertainty + " Evidence is refreshed only when a new local investigation is launched.",
            "sources": source_payload,
            "completeness": completeness,
            "approval_eligible": approval_eligible,
            "validation": {
                "required_claim_count": len(claims),
                "validated_claim_count": len(claims) - len(blocked_claims),
                "blocked_claim_keys": blocked_claims,
                "failed_stages": failed_stages,
            },
        }
        validate_brief_payload(brief, evidence, allow_partial=not approval_eligible)
        return {"brief_payload": brief, "artifacts": [_artifact("draft_brief", 60, {
            "status": "drafted" if completeness == "complete" else "partial",
            "section_count": 10, "material_claims_validated": approval_eligible,
            "approval_eligible": approval_eligible,
            "blocked_claim_keys": blocked_claims,
            "failed_stages": failed_stages,
            "evidence_ids": [source.source_id for source in evidence],
        }, "draft") ]}

    def human_review(_state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("human review must be resumed only by an explicit application approval")

    handlers = WorkflowHandlers(
        scope_request=scope_request,
        research_public_labor_data=research(EvidenceCategory.PUBLIC_LABOR_DATA),
        research_papers=research(EvidenceCategory.RESEARCH_PAPER),
        research_credible_reports=research(EvidenceCategory.CREDIBLE_REPORT),
        research_job_posting_signals=research(EvidenceCategory.JOB_POSTING_SIGNAL),
        research_official_policy=research(EvidenceCategory.OFFICIAL_POLICY),
        validate_evidence=validate_evidence,
        analyze_demand=analysis("analyze_demand", 40, "Published U.S. projections indicate continuing demand for the underlying occupation.", labor_ids,
                                "BLS projections are occupational, not employer-specific forecasts."),
        analyze_tasks_automation=analysis("analyze_tasks_automation", 41, profile["tasks"][0], research_ids + report_ids,
                                          "Evidence on task effects is early and not role-specific."),
        analyze_skill_shifts=analysis("analyze_skill_shifts", 42, profile["skills"][0], posting_ids + report_ids,
                                      "Online postings are a selected demand signal."),
        analyze_role_evolution_durability=analysis("analyze_role_evolution_durability", 43, profile["change"], labor_ids + policy_ids,
                                                    "Role evolution differs across organizations."),
        skeptic=analysis("skeptic", 44, "Evidence does not justify deterministic automation or displacement claims; transfer and selection limits remain visible.", all_ids,
                         "No bundled study directly estimates causal effects for this exact role."),
        forecast_panel=forecast_panel,
        draft_brief=draft_brief,
        human_review=human_review,
    )
    instrumented: dict[str, Any] = {}
    for definition in fields(WorkflowHandlers):
        handler = getattr(handlers, definition.name)
        instrumented[definition.name] = (
            handler if definition.name == "human_review"
            else _instrument_handler(definition.name, handler)
        )
    return WorkflowHandlers(**instrumented)


def _instrument_handler(stage: str, handler: Any) -> Any:
    def run(state: dict[str, Any]) -> dict[str, Any]:
        started = datetime.now(UTC)
        timer = perf_counter()
        invocation_id = str(uuid.uuid4())
        try:
            result = handler(state)
        except Exception as exc:
            message = f"{stage}: {exc}"
            result = {
                "errors": [message],
                "artifacts": [_artifact(stage, 998, {
                    "status": "failed", "error": str(exc), "finding": "Worker invocation failed.",
                }, "error")],
            }
        completed = datetime.now(UTC)
        duration_ms = max(0, round((perf_counter() - timer) * 1000, 3))
        artifacts = []
        for artifact in result.get("artifacts", []):
            payload_status = artifact.get("payload", {}).get("status")
            status = "failed" if payload_status == "failed" else "succeeded"
            if payload_status == "partial":
                status = "partial"
            artifacts.append(artifact | {"trace": {
                "status": status,
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
                "duration_ms": duration_ms,
                "invocation_id": invocation_id,
                "worker_key": stage,
                "worker_kind": "deterministic",
                "attempt": 1,
                "error": artifact.get("payload", {}).get("error", ""),
            }})
        return dict(result) | {"artifacts": artifacts}
    return run


def execute_investigation(role: Role, rows: list[dict[str, Any]], *, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    request = RoleBriefRequest(role=role, as_of=as_of)
    graph = build_graph(build_deterministic_handlers(role, rows, as_of=as_of))
    result = graph.invoke({"request": request})
    if "brief_payload" not in result:
        raise RuntimeError("LangGraph did not produce a draft before the human gate")
    artifacts = list(result.get("artifacts", []))
    artifacts.append(_artifact("human_approval_gate", 70, {
        "status": "paused", "required_action": "explicit owner approval", "private": True,
    }, "human_gate") | {"trace": {
        "status": "awaiting_approval", "started_at": None, "completed_at": None,
        "duration_ms": None, "invocation_id": None, "worker_key": None,
        "worker_kind": None, "attempt": None, "error": "",
    }})
    return {"brief": result["brief_payload"], "artifacts": sorted(artifacts, key=lambda item: item["order"])}
