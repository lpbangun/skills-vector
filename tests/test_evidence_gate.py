from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from skills_vector.domain import EvidenceCategory, EvidenceSource, Role, validate_ingested_evidence
from skills_vector.engine import validate_brief_payload
from skills_vector.repository import SQLiteRepository


class EvidenceGateTests(unittest.TestCase):
    def test_fresh_seed_has_every_approved_lens_and_required_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "evidence.db")
            repository.initialize()
            self.assertEqual(repository.role_count(), 3)
            for role in Role:
                records = repository.evidence_for_role(role)
                self.assertEqual({row["category"] for row in records}, {item.value for item in EvidenceCategory})
                for row in records:
                    self.assertTrue(row["url"].startswith("https://"))
                    self.assertTrue(row["published_on"])
                    self.assertTrue(row["relevant_excerpt"])
                    self.assertTrue(row["provenance"])
                    self.assertNotIn("example.", row["url"])

    def test_incomplete_ingestion_and_unapproved_category_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "not approved"):
            EvidenceSource(
                source_id="bad", category="social_media", title="Unsupported", publisher="Unknown",  # type: ignore[arg-type]
                url="https://example.org", published_on=date(2024, 1, 1), retrieved_at=datetime.now(UTC),
            )
        source = EvidenceSource(
            source_id="incomplete", category=EvidenceCategory.RESEARCH_PAPER, title="Paper", publisher="Publisher",
            url="https://example.org/paper", published_on=date(2024, 1, 1), retrieved_at=datetime.now(UTC),
            role_connections=(Role.RECRUITER,),
        )
        errors = validate_ingested_evidence(source, role=Role.RECRUITER)
        self.assertIn("relevant excerpt/claim is required", "; ".join(errors))
        self.assertIn("source provenance is required", "; ".join(errors))

    def test_unsupported_material_claim_fails_draft_validation(self) -> None:
        unsupported = {"statement": "Unsupported claim", "evidence_ids": [], "uncertainty": "Unknown."}
        brief = {
            "role_context": "Context", "what_is_changing": unsupported,
            "task_shifts": [unsupported], "skill_shifts": [unsupported],
            "durable_capabilities": [unsupported], "scenario": unsupported,
            "sources": [], "counter_evidence": [unsupported], "uncertainty_summary": "Unknown", "forecast": {},
        }
        with self.assertRaisesRegex(ValueError, "unsupported material claim"):
            validate_brief_payload(brief, [])


if __name__ == "__main__":
    unittest.main()
