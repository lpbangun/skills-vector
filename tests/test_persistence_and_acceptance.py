from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skills_vector.application import SkillsVectorService
from skills_vector.domain import EvidenceCategory, Role
from skills_vector.repository import SQLiteRepository


REQUIRED_STAGES = {
    "scope_request", "research_public_labor_data", "research_papers", "research_credible_reports",
    "research_job_posting_signals", "research_official_policy", "validate_evidence", "analyze_demand",
    "analyze_tasks_automation", "analyze_skill_shifts", "analyze_role_evolution_durability", "skeptic",
    "forecast_panel", "draft_brief", "human_approval_gate",
}


class PersistenceAndAcceptanceTests(unittest.TestCase):
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
