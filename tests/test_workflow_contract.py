from __future__ import annotations

import unittest
from dataclasses import fields
from datetime import date

from skills_vector.domain import Role, RoleBriefRequest
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

    def test_analysis_includes_skeptic_before_forecasting(self) -> None:
        self.assertIn("skeptic", ANALYSIS_NODES)
        self.assertIn((ANALYSIS_NODES, "forecast_panel"), WORKFLOW_EDGES)

    def test_human_review_is_the_final_gate(self) -> None:
        self.assertIn(("draft_brief", "human_review"), WORKFLOW_EDGES)
        self.assertIn(("human_review", "END"), WORKFLOW_EDGES)

    def test_every_module_requires_an_explicit_handler(self) -> None:
        names = {field.name for field in fields(WorkflowHandlers)}
        expected = {
            "scope_request",
            *RESEARCH_NODES,
            "validate_evidence",
            *ANALYSIS_NODES,
            "forecast_panel",
            "draft_brief",
            "human_review",
        }
        self.assertEqual(names, expected)

    def test_governed_configuration_cannot_change_outside_human_gate(self) -> None:
        self.assertEqual(
            HUMAN_GATED_CHANGES,
            {"prompt", "policy", "memory", "model_router"},
        )

    def test_compiled_graph_executes_fanouts_and_pauses_for_human_review(self) -> None:
        calls: list[str] = []

        def handler(name: str):
            def run(_state: dict[str, object]) -> dict[str, object]:
                calls.append(name)
                if name.startswith("research_"):
                    return {"evidence": []}
                if name.startswith("analyze_") or name == "skeptic":
                    return {"analyses": []}
                return {}

            return run

        def human_review(_state: dict[str, object]) -> dict[str, object]:
            raise AssertionError("the graph must pause before human review")

        handlers = WorkflowHandlers(
            **{
                name: handler(name)
                for name in {
                    "scope_request",
                    *RESEARCH_NODES,
                    "validate_evidence",
                    *ANALYSIS_NODES,
                    "forecast_panel",
                    "draft_brief",
                }
            },
            human_review=human_review,
        )
        graph = build_graph(handlers)
        graph.invoke(
            {"request": RoleBriefRequest(Role.RECRUITER, date(2026, 8, 3))}
        )

        self.assertTrue(set(RESEARCH_NODES).issubset(calls))
        self.assertTrue(set(ANALYSIS_NODES).issubset(calls))
        self.assertIn("forecast_panel", calls)
        self.assertIn("draft_brief", calls)


if __name__ == "__main__":
    unittest.main()
