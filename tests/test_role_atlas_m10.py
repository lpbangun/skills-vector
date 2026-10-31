"""M10 publication gates; frozen captures and old releases need not be available."""
import json
from pathlib import Path
import unittest

from skills_vector.market.api import handle_request
from skills_vector.market.role_artifacts import load_roles, read_json
from skills_vector.market.role_atlas import RoleRecord
from skills_vector.market.role_ledger import read_ledger, track_record
from skills_vector.market.role_reads import RoleAtlasReads
from skills_vector.market.skill_rules import SkillRules, TABLE

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "research/role-atlas/2026-Q4-union-r3"


class M10PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_roles(RELEASE / "roles")
        cls.manifest = read_json(RELEASE / "release.json")
        cls.reads = RoleAtlasReads.configured(RELEASE / "roles", ledger_path=RELEASE / "ledger.json")

    def test_all_top_twenty_rank_by_employers_then_weighted_share_not_table_noise(self):
        rules = SkillRules()
        employers = [r["employer"] for r in read_json(TABLE.parent / "employers.json")] + ["Unum", "Illumina", "Brex"]
        for role in self.records:
            skills = role["skill_map"]["skills"]
            expected = sorted(skills, key=lambda s: (-s["windows"][-1]["employers"],
                                                   -s["windows"][-1]["employer_weighted_share"], s["id"]))
            self.assertEqual(skills, expected)
            self.assertGreaterEqual(len(skills), 20)
            for skill in skills[:20]:
                for employer in employers:
                    self.assertIsNone(rules.rejection(skill["label"], employer), (role["id"], skill["id"]))
            light = self.reads.role(role["id"], view="overview")
            self.assertEqual(light["overview"]["ranking"], "employers_then_employer_weighted_share")
            self.assertEqual([s["id"] for s in light["role"]["skill_map"]["skills"]],
                             [s["id"] for s in skills if s["windows"][-1]["employers"] >= 3])
            for group in light["overview"]["tails"]:
                self.assertEqual([s["id"] for s in group["skills"]],
                                 [s["id"] for s in skills if s["status"] == group["status"] and s["windows"][-1]["employers"] < 3])

    def test_counts_drop_receipts_and_unchanged_budgets(self):
        expected = {"role.account-executive": (495, 47), "role.forward-deployed-engineer": (37, 19),
                    "role.growth-manager": (20, 12), "role.hr-generalist": (12, 10)}
        for role in self.records:
            self.assertEqual((role["demand_window"]["postings"], role["demand_window"]["employers"]), expected[role["id"]])
            quality = self.manifest["roles"][role["id"]]["skill_filter"]
            self.assertGreater(sum(quality["drops_by_reason"].values()), 0)
            self.assertIn("postings_emptied", quality)
            self.assertIn("postings_empty_before", quality)
            self.assertNotIn("drops", quality)  # Text remains external, never the publication manifest.
            self.assertTrue(any(n.startswith("Extraction: rules/3") for n in role["limits"]))
            self.assertTrue(any(n.startswith("Sample sectors: ") for n in role["limits"]))
            self.assertTrue(all(s["status"] == "early" for s in role["skill_map"]["skills"]))
            response = handle_request("GET", "/v1/roles/" + role["id"] + "?view=overview", role_catalog=self.reads)
            self.assertEqual(response.status, 200)
            self.assertLessEqual(len(response.body), 400_000)
        self.assertLessEqual(sum(p.stat().st_size for p in RELEASE.rglob("*") if p.is_file()), 6_000_000)
        self.assertLessEqual((RELEASE / "ledger.json").stat().st_size, 300_000)
        self.assertFalse((RELEASE / "audit.json").exists())
        stats = track_record(self.records, read_ledger(RELEASE / "ledger.json"))
        self.assertEqual(stats["basis_counts"]["none"], sum(len(r["skill_map"]["skills"]) for r in self.records))
        self.assertEqual((stats["scored_count"], stats["brier_score"]), (0, None))

    def test_optional_weight_does_not_change_historical_role_roundtrips(self):
        old = ROOT / "research/role-atlas/2026-Q4-union/roles"
        for path in old.glob("*.json"):
            raw = json.loads(path.read_text())
            self.assertEqual(RoleRecord.from_dict(raw).to_dict(), raw)
            self.assertTrue(all("employer_weighted_share" not in w for s in raw["skill_map"]["skills"] for w in s["windows"]))
        self.assertEqual(read_json(old.parent / "release.json")["release_id"], "role_research_7333966260cf6538")


if __name__ == "__main__":
    unittest.main()
