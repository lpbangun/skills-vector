from __future__ import annotations

import unittest
from dataclasses import fields
from datetime import date

from skills_vector.domain import Role, RoleBriefRequest
from skills_vector.planning import InvestigationPlan
from skills_vector.workflow import (
    ANALYSIS_NODES,
    HUMAN_GATED_CHANGES,
    RESEARCH_NODES,
    WORKFLOW_EDGES,
    WorkflowHandlers,
    build_graph,
)


class WorkflowContractTests(unittest.TestCase):
    def test_research_uses_only_approved_evidence_lenses(self) -> None:
        self.assertEqual(
            set(RESEARCH_NODES),
            {
                "research_public_labor_data",
                "research_papers",
                "research_credible_reports",
                "research_job_posting_signals",
                "research_official_policy",
            },
        )

    def test_analysis_keeps_skeptic_as_optional_capability(self) -> None:
        self.assertIn("skeptic", ANALYSIS_NODES)
        self.assertIn(("analyze", "forecast_panel"), WORKFLOW_EDGES)

    def test_human_review_is_the_final_gate(self) -> None:
        self.assertIn(("draft_brief", "human_review"), WORKFLOW_EDGES)
        self.assertIn(("human_review", "END"), WORKFLOW_EDGES)

    def test_every_adaptive_stage_requires_an_explicit_handler(self) -> None:
        self.assertEqual(
            {field.name for field in fields(WorkflowHandlers)},
            {
                "scope_request",
                "plan_investigation",
                "research",
                "validate_evidence",
                "analyze",
                "forecast_panel",
                "draft_brief",
                "human_review",
            },
        )

    def test_governed_configuration_cannot_change_outside_human_gate(self) -> None:
        self.assertEqual(HUMAN_GATED_CHANGES, {"prompt", "policy", "memory", "model_router", "code"})

    def test_compiled_graph_runs_adaptive_stages_and_pauses_for_review(self) -> None:
        calls: list[str] = []

        def handler(name: str):
            def run(_state: dict[str, object]) -> dict[str, object]:
                calls.append(name)
                if name == "plan_investigation":
                    return {"plan": InvestigationPlan((), (), "test")}
                if name == "research":
                    return {"evidence": []}
                if name == "analyze":
                    return {"claims": []}
                return {}

            return run

        def human_review(_state: dict[str, object]) -> dict[str, object]:
            raise AssertionError("the graph must pause before human review")

        handlers = WorkflowHandlers(
            **{
                name: handler(name)
                for name in {
                    "scope_request",
                    "plan_investigation",
                    "research",
                    "validate_evidence",
                    "analyze",
                    "forecast_panel",
                    "draft_brief",
                }
            },
            human_review=human_review,
        )
        result = build_graph(handlers).invoke(
            {"run_id": "contract", "request": RoleBriefRequest(Role.RECRUITER, date(2026, 8, 3))}
        )

        self.assertEqual(
            calls,
            [
                "scope_request",
                "plan_investigation",
                "research",
                "validate_evidence",
                "analyze",
                "forecast_panel",
                "draft_brief",
            ],
        )
        self.assertNotIn("human_review", calls)
        self.assertEqual(result["plan"].reason, "test")


if __name__ == "__main__":
    unittest.main()
