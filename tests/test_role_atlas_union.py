"""M9-r5 selected union publication, source framing and unchanged budget gates."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from skills_vector.market.api import handle_request
from skills_vector.market.role_artifacts import load_roles, read_json
from skills_vector.market.role_ledger import read_ledger, track_record
from skills_vector.market.role_overview import overview
from skills_vector.market.role_pipeline import posting_source_note
from skills_vector.market.role_reads import RoleAtlasReads

ROOT = Path(__file__).resolve().parents[1]
UNION = ROOT / "research/role-atlas/2026-Q4-union"
PIN = "role_research_7333966260cf6538"


class UnionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_roles(UNION / "roles")
        cls.reads = RoleAtlasReads.configured(UNION / "roles", ledger_path=UNION / "ledger.json")

    def test_published_counts_source_coverage_floors_and_budgets(self):
        expected = {"role.account-executive": (495, 47), "role.forward-deployed-engineer": (37, 19),
                    "role.growth-manager": (20, 12), "role.hr-generalist": (12, 10)}
        for r in self.records:
            self.assertEqual(r["release_id"], PIN)
            self.assertEqual(r["data_kind"], "research")
            self.assertEqual((r["demand_window"]["postings"], r["demand_window"]["employers"]), expected[r["id"]])
            self.assertEqual(len({s["windows"][-1]["window"] for s in r["skill_map"]["skills"]}), 1)
            self.assertTrue(all(s["status"] == "early" for s in r["skill_map"]["skills"]))
            source = next(n for n in r["limits"] if n.startswith("Posting sources: "))
            self.assertIn("employer career sites (Workday)", source)
            if r["id"] == "role.hr-generalist":
                self.assertNotIn("Lever", source)
            for sector in r["industries"]:
                if sector["profile"] is not None:
                    self.assertGreaterEqual(sector["evidence"]["postings"], 30)
                    self.assertGreaterEqual(sector["evidence"]["employers"], 8)
            response = handle_request("GET", "/v1/roles/" + r["id"] + "?view=overview", role_catalog=self.reads)
            self.assertEqual(response.status, 200)
            self.assertLessEqual(len(response.body), 400_000)
        self.assertLessEqual(sum(p.stat().st_size for p in UNION.rglob("*") if p.is_file()), 6_000_000)
        self.assertLessEqual((UNION / "ledger.json").stat().st_size, 300_000)
        self.assertFalse((UNION / "audit.json").exists())
        self.assertLess((UNION / "release.json").stat().st_size, 32_000)
        stats = track_record(self.records, read_ledger(UNION / "ledger.json"))
        self.assertEqual(stats["basis_counts"]["none"], sum(len(r["skill_map"]["skills"]) for r in self.records))
        self.assertEqual((stats["scored_count"], stats["brier_score"], stats["calibration_bins"]), (0, None, []))

    def test_tail_labels_are_explicitly_abbreviated_not_lost_details(self):
        saw_long = False
        for record in self.records:
            full = {s["id"]: s for s in record["skill_map"]["skills"]}
            light = self.reads.role(record["id"], view="overview")
            self.assertEqual(light["overview"]["tail_label_limit"], 80)
            self.assertEqual(light["overview"]["employer_floor"], 3)
            common = {s["id"]: s for s in light["role"]["skill_map"]["skills"]}
            tail = {s["id"]: s for g in light["overview"]["tails"] for s in g["skills"]}
            self.assertEqual(set(common) | set(tail), set(full))
            for sid, stub in tail.items():
                self.assertEqual(set(stub), {"id", "label"})
                self.assertLessEqual(len(stub["label"]), 80)
                if len(full[sid]["label"]) > 80 and not saw_long:
                    saw_long = True
                    self.assertTrue(stub["label"].endswith("…"))
                    detail = self.reads.skill(record["id"], sid, PIN)
                    self.assertEqual(detail["skill"], full[sid])
            self.assertEqual(light["role"]["skill_map"]["skills"], [s for s in record["skill_map"]["skills"] if s["windows"][-1]["employers"] >= 3])
        self.assertTrue(saw_long)
        for bad in (0, -1, 17, True, "80"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                overview(self.records[0], tail_label_limit=bad)

    def test_note_comes_from_admitted_kinds_not_employer_name_or_global_policy(self):
        self.assertEqual(posting_source_note([{"ats": "greenhouse", "employer": "Workday"}]),
                         "Posting sources: public job boards (Greenhouse)")
        self.assertEqual(posting_source_note([{"ats": "jsonld", "board_url": "https://tenant.myworkdayjobs.com/"}]),
                         "Posting sources: employer career sites (Workday)")
        self.assertEqual(posting_source_note([{"ats": "jsonld", "board_url": "https://careers.example.org/"}]),
                         "Posting sources: employer career sites (structured JSON-LD)")
        self.assertIn("source coverage not recorded", posting_source_note([{"ats": "unknown"}]))
        self.assertNotIn("Greenhouse", posting_source_note([]))

    def test_superseded_sector_release_remains_readable_without_selection(self):
        root = ROOT / "research/role-atlas/2026-Q4-sectors"
        receipt = read_json(root / "release.json")
        self.assertEqual(receipt["selection"], {"status": "superseded", "superseded_by": PIN, "replacement": "2026-Q4-union"})
        old = RoleAtlasReads.configured(root / "roles", ledger_path=root / "ledger.json")
        self.assertEqual(old.list_roles()["release_id"], "role_research_71d6040270516002")
        self.assertEqual(old.role("account-executive")["role"]["demand_window"]["postings"], 436)
        self.assertEqual(self.reads.role("account-executive", receipt["release_id"])["status"], "unknown_release")
        config = read_json(ROOT / "vercel.json")["functions"]["api/index.py"]["includeFiles"]
        self.assertIn("2026-Q4-union/roles/*.json", config)
        self.assertNotIn("2026-Q4-sectors", config)
        self.assertNotIn("audit.json", config)

    def test_deployment_operator_selector_errors_do_not_fall_back(self):
        for env in ({"SKILLS_VECTOR_ROLE_ATLAS_ROOT": "/missing-role-atlas-root"},
                    {"SKILLS_VECTOR_ROLE_ATLAS_LEDGER": "/missing-role-atlas-ledger"},
                    {"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "0"}):
            with patch.dict(os.environ, env, clear=True), patch.object(sys, "path", list(sys.path)):
                spec = importlib.util.spec_from_file_location("union_entrypoint", ROOT / "api/index.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with TestClient(module.app) as client:
                    self.assertEqual(client.get("/v1/roles").status_code, 503)


if __name__ == "__main__":
    unittest.main()
