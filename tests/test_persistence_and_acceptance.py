from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skills_vector.application import SkillsVectorService
from skills_vector.domain import EvidenceCategory, Role
from skills_vector.engine import execute_investigation
from skills_vector.repository import SQLiteRepository


REQUIRED_STAGES = {
    "scope_request", "research_public_labor_data", "research_papers", "research_credible_reports",
    "research_job_posting_signals", "research_official_policy", "validate_evidence", "analyze_demand",
    "analyze_tasks_automation", "analyze_skill_shifts", "analyze_role_evolution_durability", "skeptic",
    "forecast_panel", "draft_brief", "human_approval_gate",
}


class PersistenceAndAcceptanceTests(unittest.TestCase):
    def test_normalized_trace_distinguishes_stages_invocations_findings_and_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = SkillsVectorService(SQLiteRepository(Path(directory) / "trace.db"))
            run = service.investigate(Role.RECRUITER.value)
            summary = run["trace"]["summary"]
            self.assertEqual(summary["stage_count"], 15)
            self.assertEqual(summary["invocation_count"], 14)
            self.assertEqual(summary["finding_count"], 14)
            self.assertEqual(summary["claim_count"], 15)
            gate = next(stage for stage in run["trace"]["stages"] if stage["stage_key"] == "human_approval_gate")
            self.assertEqual(gate["invocation_count"], 0)
            self.assertEqual(gate["status"], "awaiting_approval")

    def test_missing_required_lens_persists_an_inspectable_non_approvable_partial_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "partial.db")
            repository.initialize()
            rows = [
                row for row in repository.evidence_for_role(Role.RECRUITER)
                if row["category"] != EvidenceCategory.RESEARCH_PAPER.value
            ]
            result = execute_investigation(Role.RECRUITER, rows)
            run_id = repository.create_run(Role.RECRUITER)
            repository.save_draft(run_id, result["artifacts"], result["brief"])
            run = repository.get_run(run_id)
            self.assertEqual(run["status"], "partial_blocked")
            self.assertFalse(run["brief"]["approval_eligible"])
            self.assertIn("research_papers", run["brief"]["validation"]["failed_stages"])
            self.assertGreater(run["trace"]["summary"]["failure_count"], 0)
            with self.assertRaisesRegex(ValueError, "approval-eligible"):
                repository.approve(run_id, "Owner")

    def test_evidence_explorer_paginates_a_five_hundred_source_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "scale.db")
            run = SkillsVectorService(repository).investigate(Role.RECRUITER.value)
            claim_id = next(
                claim["id"] for claim in run["trace"]["claims"]
                if claim["cluster_key"] == "role_change"
            )
            with repository._connection() as db:  # exercise the persisted read model at its design target
                for index in range(500):
                    source_id = f"scale-source-{index:03d}"
                    db.execute(
                        "INSERT INTO run_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (run["id"], source_id, EvidenceCategory.CREDIBLE_REPORT.value,
                         f"Scale source {index}", f"Publisher {index}", f"https://example.org/{index}",
                         "2025-01-01", "A bounded fixture excerpt.", "Synthetic scale fixture.",
                         Role.RECRUITER.value, "validated"),
                    )
                    db.execute(
                        "INSERT INTO claim_sources VALUES (?,?,?,?,?)",
                        (claim_id, run["id"], source_id, "supports", index + 100),
                    )
            page = repository.evidence_for_run(run["id"], cluster_key="role_change", limit=25)
            self.assertGreaterEqual(page["total"], 500)
            self.assertEqual(len(page["sources"]), 25)
            next_page = repository.evidence_for_run(run["id"], cluster_key="role_change", limit=25, offset=25)
            self.assertEqual(len(next_page["sources"]), 25)
            self.assertNotEqual(page["sources"][0]["source_id"], next_page["sources"][0]["source_id"])

    def test_run_draft_and_approval_survive_repository_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills-vector.db"
            draft = SkillsVectorService(SQLiteRepository(path)).investigate(Role.RECRUITER.value)
            self.assertEqual(draft["status"], "awaiting_approval")
            restarted = SkillsVectorService(SQLiteRepository(path))
            persisted = restarted.run(draft["id"])
            self.assertEqual(persisted["brief"], draft["brief"])
            approved = restarted.approve(draft["id"], "Owner", "Evidence reviewed")
            self.assertEqual(approved["status"], "approved")
            final = SkillsVectorService(SQLiteRepository(path)).run(draft["id"])
            self.assertEqual(final["approval"]["reviewer"], "Owner")
            self.assertEqual(final["brief"]["state"], "approved")
            self.assertIn("human_review", {item["stage"] for item in final["artifacts"]})

    def test_all_three_role_cases_pass_consecutively_through_real_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = SkillsVectorService(SQLiteRepository(Path(directory) / "acceptance.db"))
            streak = []
            for role in Role:
                draft = service.investigate(role.value)
                self.assertEqual(draft["role_slug"], role.value)
                self.assertEqual(draft["status"], "awaiting_approval")
                self.assertTrue(REQUIRED_STAGES.issubset({item["stage"] for item in draft["artifacts"]}))
                self.assertEqual({source["category"] for source in draft["brief"]["sources"]},
                                 {item.value for item in EvidenceCategory})
                self.assertEqual(len(draft["brief"]["durable_capabilities"]), 7)
                approved = service.approve(draft["id"], "Local Owner")
                self.assertEqual(approved["status"], "approved")
                streak.append(role.value)
            self.assertEqual(streak, [role.value for role in Role])

    def test_new_investigation_creates_an_updated_version_without_erasing_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "versions.db")
            service = SkillsVectorService(repository)
            first = service.investigate(Role.HR_COORDINATOR.value)
            second = service.investigate(Role.HR_COORDINATOR.value)
            self.assertEqual(first["brief"]["version"], 1)
            self.assertEqual(second["brief"]["version"], 2)
            self.assertIsNotNone(service.run(first["id"]))
            self.assertEqual(repository.counts()["runs"], 2)


if __name__ == "__main__":
    unittest.main()
