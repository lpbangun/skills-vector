"""Human-readable trace vocabulary and deterministic evidence read models."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable


STAGE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "scope_request": {"label": "Scope request", "phase": "scope", "order": 10},
    "research_public_labor_data": {"label": "Public labor data", "phase": "research", "order": 20},
    "research_papers": {"label": "Research papers", "phase": "research", "order": 21},
    "research_credible_reports": {"label": "Credible reports", "phase": "research", "order": 22},
    "research_job_posting_signals": {"label": "Job-posting signals", "phase": "research", "order": 23},
    "research_official_policy": {"label": "Official policy", "phase": "research", "order": 24},
    "validate_evidence": {"label": "Validate evidence", "phase": "validation", "order": 30},
    "analyze_demand": {"label": "Demand outlook", "phase": "analysis", "order": 40},
    "analyze_tasks_automation": {"label": "Task automation", "phase": "analysis", "order": 41},
    "analyze_skill_shifts": {"label": "Skill shifts", "phase": "analysis", "order": 42},
    "analyze_role_evolution_durability": {"label": "Role durability", "phase": "analysis", "order": 43},
    "skeptic": {"label": "Skeptical review", "phase": "analysis", "order": 44},
    "forecast_panel": {"label": "Forecast panel", "phase": "forecast", "order": 50},
    "draft_brief": {"label": "Draft brief", "phase": "draft", "order": 60},
    "human_approval_gate": {"label": "Human approval", "phase": "approval", "order": 70},
}


TRACE_EDGES: tuple[tuple[str, str], ...] = (
    *(("scope_request", stage) for stage in (
        "research_public_labor_data", "research_papers", "research_credible_reports",
        "research_job_posting_signals", "research_official_policy",
    )),
    *((stage, "validate_evidence") for stage in (
        "research_public_labor_data", "research_papers", "research_credible_reports",
        "research_job_posting_signals", "research_official_policy",
    )),
    *(("validate_evidence", stage) for stage in (
        "analyze_demand", "analyze_tasks_automation", "analyze_skill_shifts",
        "analyze_role_evolution_durability", "skeptic",
    )),
    *((stage, "forecast_panel") for stage in (
        "analyze_demand", "analyze_tasks_automation", "analyze_skill_shifts",
        "analyze_role_evolution_durability", "skeptic",
    )),
    ("forecast_panel", "draft_brief"),
    ("draft_brief", "human_approval_gate"),
)


CLUSTER_LABELS = {
    "role_change": "What is changing",
    "task_shift": "Task shifts",
    "skill_shift": "Skill shifts",
    "durable_capability": "Durable capabilities",
    "scenario": "Two-year scenario",
    "counter_evidence": "Counter-evidence & disagreement",
}


CLAIM_SECTIONS: tuple[tuple[str, str], ...] = (
    ("what_is_changing", "single"),
    ("task_shifts", "many"),
    ("skill_shifts", "many"),
    ("durable_capabilities", "many"),
    ("scenario", "single"),
    ("counter_evidence", "many"),
)


def iter_claims(brief: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield every material claim in stable brief order."""

    order = 0
    for section, cardinality in CLAIM_SECTIONS:
        value = brief.get(section)
        items = value if cardinality == "many" else [value]
        for item in items or []:
            if not isinstance(item, dict):
                continue
            fallback = section if cardinality == "single" else f"{section}.{order}"
            yield item | {
                "claim_key": item.get("claim_key", fallback),
                "section": item.get("section", section),
                "cluster_key": item.get("cluster_key", section),
                "claim_order": order,
            }
            order += 1


def artifact_summary(stage: str, payload: dict[str, Any]) -> str:
    if payload.get("finding"):
        return str(payload["finding"])
    if payload.get("consensus"):
        return str(payload["consensus"])
    if payload.get("error"):
        return str(payload["error"])
    if stage == "scope_request":
        return f"Scoped {payload.get('role', 'role')} to {payload.get('geography', 'US')} evidence."
    if stage == "validate_evidence":
        return f"Validated {payload.get('validated_count', 0)} evidence records."
    if stage == "draft_brief":
        return "Drafted the private role brief and evaluated claim support."
    if stage == "human_approval_gate":
        return "Waiting for explicit owner approval."
    return str(payload.get("status") or payload.get("required_action") or "Stage output persisted.")


def artifact_source_ids(payload: dict[str, Any]) -> list[str]:
    values = payload.get("source_ids", payload.get("evidence_ids", []))
    return [str(value) for value in values if value]


def rank_source(
    source: dict[str, Any], *, claim_count: int, publisher_count: int,
    as_of: date | None, counter_evidence: bool,
) -> tuple[int, list[dict[str, str]]]:
    """Return a deterministic contextual rank and human-readable reasons."""

    score = 4
    reasons: list[dict[str, str]] = [
        {"code": "direct_claim", "label": "Directly cited by this claim"},
    ]
    if source.get("role_connection"):
        score += 2
        reasons.append({"code": "role_specific", "label": "Connected to this role"})
    if claim_count > 1:
        score += min(2, claim_count - 1)
        reasons.append({"code": "cluster_coverage", "label": f"Supports {claim_count} claims in this cluster"})
    if publisher_count == 1:
        score += 2
        reasons.append({"code": "independent_publisher", "label": "Adds an independent publisher"})
    published = source.get("published_on")
    if as_of and published:
        try:
            published_date = date.fromisoformat(str(published))
            if 0 <= (as_of - published_date).days <= 3 * 366:
                score += 1
                reasons.append({"code": "current", "label": "Current for this investigation"})
        except ValueError:
            pass
    combined = f"{source.get('provenance', '')} {source.get('relevant_excerpt', '')}".casefold()
    if any(term in combined for term in ("not an hr occupation", "selection bias", "may later become outdated")):
        reasons.append({"code": "limitation", "label": "Carries a visible transfer or freshness limitation"})
    if counter_evidence:
        reasons.append({"code": "counter_evidence", "label": "Counter-evidence or disagreement"})
    return score, reasons
