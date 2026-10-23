"""Offline M4 evidence projection, provenance, floor, and determinism gates."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills_vector.market.role_atlas import RoleRecord, RoleValidationError
from skills_vector.market.role_pipeline import (EvidenceInputs, MAPPING_ROOT, canonical, cooccurrences,
                                                digest, sector_profile, triage, window_of)
from skills_vector.market.role_reads import RoleAtlasReads

ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "preview/release"


def synthetic_tree(root: Path) -> tuple[Path, Path]:
    """Tiny synthetic publisher/index for mutation tests, never product findings."""
    published, maps = root / "published", root / "maps"
    release = published / "releases/unit"
    release.mkdir(parents=True)
    maps.mkdir()
    def save(path: Path, value):
        path.write_bytes(canonical(value))
    policy = "Interpret and explain human resources policies, procedures, laws, standards, or regulations."
    task = "Interpret and explain human resources policies."
    scope = "Independently handle employee relations issues."
    company = "Acme primary employer sector is NAICS 51."
    source_rows = []
    for sid, text, url, kind in (("job", task + "\n" + scope + "\n" + company, "https://example.org/job", "job-posting"),
                                  ("onet", policy, "https://www.onetonline.org/link/summary/13-1071.00", "foundation")):
        data = text.encode()
        (release / (sid + ".txt")).write_bytes(data)
        source_rows.append({"id": sid, "source_type": kind, "url": url, "inclusion": True, "extract_path": sid + ".txt",
                            "sha256": digest(data), "extract_sha256": digest(data), "retrieved_at": "2026-10-02T00:00:00Z"})
    source_hash = source_rows[0]["sha256"]
    save(release / "sources.json", source_rows)
    save(release / "postings.json", [{"occupation_slug": "example", "dedup_key": "post1", "employer": "Acme", "title": "Chief HR Officer",
        "source_id": "job", "work_level": "unknown", "advertised_experience": {"value": ["20 years"]},
        "responsibility_evidence": {"source_id": "job", "source_sha256": source_hash, "quote": scope},
        "expectations": [{"expectation_id": "exp1", "source_wording": task, "dimension": "task", "source_id": "job", "source_sha256": source_hash}]}])
    save(release / "occupations.json", [{"slug": "example", "label": "Example", "aliases": ["Example"]}])
    manifest = {"release_id": "unit", "release_schema": "market-release/2", "agent_attribution": "Synthetic offline unit test"}
    save(release / "manifest.json", manifest)
    manifest_hash = digest(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    save(published / "current.json", {"current": "unit", "releases": [{"release_id": "unit", "manifest_sha256": manifest_hash}]})
    node = "onet:13-1071.00:local-task:policy"
    save(maps / "skills.json", {"schema": "role-atlas-mappings/1", "release_id": "unit", "roles": {"example": {
        "status": "established", "source_id": "onet", "category": "HR", "anchor": {"system": "O*NET-SOC", "code": "13-1071.00", "title": "Human Resources Specialists", "fit": "partial"}}},
        "backbone": [{"id": node, "role": "example", "source_id": "onet", "extract_sha256": source_rows[1]["extract_sha256"], "quote": policy, "short_label": "HR policies", "source_note": "Synthetic test; exact real O*NET task text, no equivalence claim."}],
        "phrases": [{"role": "example", "phrase": task, "backbone_id": node, "source_note": "Synthetic test of a reviewed task alignment."}], "sectors": {"51": "Information"}})
    save(maps / "employers.json", [{"employer": "Acme", "status": "verified", "naics": "51", "source_id": "job", "quote": company, "source_note": "Synthetic test: explicit primary employer NAICS statement."}])
    mapping = json.loads((maps / "skills.json").read_text())
    for row in [*mapping["roles"].values(), *mapping["backbone"], *mapping["phrases"]]:
        row.update(confidence="close", rationale="Synthetic bounded task alignment for validation tests.")
    save(maps / "skills.json", mapping)
    return published, maps


class LevelTriageTests(unittest.TestCase):
    def test_scope_beats_weak_title_and_years(self):
        posting = {"title": "Chief Senior Director", "advertised_experience": {"value": ["20 years"]}}
        decision = triage(posting, ["Own day-to-day execution of campaigns."])
        self.assertEqual(decision.level, 2)
        self.assertEqual(decision.weak["title"], posting["title"])
        self.assertEqual(triage({"title": "Junior", "advertised_experience": {"value": []}}, ["You will own the architecture."]).level, 4)
        self.assertIsNone(triage(posting, []).level)
        self.assertIsNone(triage(posting, ["Experience independently handling employee relations issues"]).level)
        self.assertIsNone(triage(posting, ["At least 10 years owning architecture"]).level)

    def test_conflicts_and_management_remain_countable_unassigned(self):
        decision = triage({}, ["Work under supervision.", "You will own the architecture."])
        self.assertIsNone(decision.level)
        self.assertEqual(decision.reason, "conflicting_strong_scope_signals")
        self.assertEqual({s["level"] for s in decision.strong}, {1, 4})
        self.assertIsNone(triage({}, ["Manage your team and evaluate direct reports."]).level)
        self.assertIsNone(triage({"work_level": "people_manager"}, ["Own day-to-day execution of work."]).level)
        self.assertEqual(triage({}, ["Work under supervision."]).level, 1)
        self.assertEqual(triage({}, ["Own end-to-end delivery of projects."]).level, 3)
        self.assertEqual(triage({}, ["Set company-wide priorities."]).level, 5)


class FloorTests(unittest.TestCase):
    def rows(self, n, employers=8):
        return [{"dedup_key": str(i), "employer": f"Employer {i % employers}",
                 "expectations": [{"dimension": "task", "source_wording": "Synthetic duty."}]} for i in range(n)]

    def test_edges_exact_floors_and_each_rejection(self):
        mentions = {"a": {str(i) for i in range(40)}, "b": {str(i) for i in range(25)}}
        kept, dropped = cooccurrences(self.rows(60), mentions)
        self.assertEqual(dropped, [])
        self.assertEqual((kept[0]["lift"], kept[0]["postings"], kept[0]["employers"]), (1.5, 25, 8))
        for changed, rows, reason in (({**mentions, "b": {str(i) for i in range(24)}}, self.rows(60), "postings"),
                                      ({**mentions, "a": {str(i) for i in range(41)}}, self.rows(60), "lift"),
                                      (mentions, self.rows(60, 7), "employers")):
            kept, dropped = cooccurrences(rows, changed)
            self.assertEqual(kept, [])
            self.assertEqual(dropped[0]["failed_floors"], [reason])
        self.assertEqual(cooccurrences(self.rows(2), {"a": {"0"}, "b": {"1"}}), ([], []))

    def test_sector_floor_never_padded(self):
        mentions = {"skill.a": {str(i) for i in range(15)}}
        profile = sector_profile(self.rows(30), "2026-Q4", mentions)
        self.assertEqual(profile["skill_shares"], [{"skill": "skill.a", "share": 0.5, "timing": "coincident"}])
        self.assertEqual(profile["distinct_duties"], ["Synthetic duty."])
        self.assertEqual(profile["certifications"], [])
        self.assertIsNone(sector_profile(self.rows(29), "2026-Q4", mentions))
        self.assertIsNone(sector_profile(self.rows(30, 7), "2026-Q4", mentions))
        rows = self.rows(30)
        rows[0]["expectations"] += [{"dimension": "credential", "basis": "employer_preference", "source_wording": "Credential X"},
                                   {"dimension": "knowledge", "source_wording": "Employment law"}]
        profile = sector_profile(rows, "2026-Q4", mentions)
        self.assertEqual(profile["certifications"][0]["asked_as"], "preferred")
        self.assertEqual(profile["certifications"][0]["posting_share"], 1 / 30)
        self.assertEqual(profile["certifications"][0]["issuer"], "Not recorded")
        self.assertEqual(profile["domain_knowledge"], ["Employment law"])


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = EvidenceInputs.read(RELEASE_ROOT)
        cls.records, cls.audit = cls.inputs.build()

    def test_actual_counts_no_invented_level_history_or_sectors(self):
        expected = {"account-executive": (5, 5, 12, 12), "forward-deployed-engineer": (5, 3, 11, 14),
                    "growth-manager": (4, 2, 5, 1), "hr-generalist": (5, 5, 10, 10)}
        for record in self.records:
            key = record.id.removeprefix("role.")
            n, unassigned, skills, dropped = expected[key]
            report = self.audit["roles"][key]
            self.assertEqual(report["postings_in"], n)
            self.assertEqual(report["postings_per_level"]["unassigned"], unassigned)
            self.assertEqual(sum(report["postings_per_level"].values()), n)
            self.assertEqual(report["skills_by_status"], {"core": 0, "rising": 0, "early": skills, "fading": 0})
            self.assertEqual((report["edges_kept"], report["edges_dropped"]), (0, dropped))
            self.assertEqual((report["sectors_with_profiles"], report["sectors_without_profiles"]), (0, 20))
            self.assertTrue(all(i.profile is None and i.evidence.postings == 0 for i in record.industries))
            self.assertEqual(record.data_kind, "research")
            self.assertIsNone(record.demand_window.compared_to)
            self.assertEqual(record.demand_window.id, "2026-Q4")
            for skill in record.skill_map.skills:
                self.assertEqual(len(skill.windows), 1)
                self.assertIn("unassigned", skill.windows[0].levels)
                self.assertEqual(skill.forecast.basis, "none")
                self.assertIsNone(skill.forecast.probability)
                self.assertIsNone(skill.forecast.base_rate)
                self.assertIn("no historical base rate", skill.forecast.reason)
                self.assertIn("published expectation excerpts", skill.forecast.claim)
                self.assertIn("published expectation excerpts", skill.evidence[0].reading)
            self.assertEqual(RoleRecord.from_json(record.to_json()), record)
        links = [link for p in self.audit["roles"]["account-executive"]["postings"] for link in p["skill_links"]]
        accuracy = next(link for link in links if link["phrase"] == "Ensure high forecasting accuracy and consistency")
        self.assertEqual(accuracy["mapping_confidence"], "loose")
        self.assertIsNone(accuracy["backbone_id"])
        skill = next(s for s in self.records[0].skill_map.skills if s.id == accuracy["skill"])
        self.assertEqual(skill.source_links, ["P"])
        self.assertEqual([source.family for source in skill.evidence], ["P"])
        self.assertEqual(self.audit["roles"]["forward-deployed-engineer"]["postings_per_level"]["L4"], 2)
        self.assertEqual(self.audit["roles"]["growth-manager"]["postings_per_level"]["L2"], 1)
        self.assertEqual(self.audit["roles"]["growth-manager"]["postings_per_level"]["L4"], 1)

    def test_byte_identical_across_output_locations_and_repeat_runs(self):
        with tempfile.TemporaryDirectory() as d, patch("socket.create_connection", side_effect=AssertionError("offline only")):
            a, b = Path(d) / "a", Path(d) / "b"
            self.inputs.write(a)
            EvidenceInputs.read(RELEASE_ROOT).write(b)
            self.inputs.write(a)
            def files(root):
                return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(files(a), files(b))
            self.assertEqual(len(files(a)), 5)
            reads = RoleAtlasReads.configured(a / "roles")
            index = reads.list_roles()
            self.assertEqual(index["data_kind"], "research")
            self.assertIsNone(index["fixture_caveat"])
            for role in index["roles"]:
                payload = reads.role(role["id"], index["release_id"])
                self.assertEqual(payload["status"], "ok")
                self.assertNotIn("presentation", payload)
                for skill in payload["role"]["skill_map"]["skills"]:
                    self.assertEqual(reads.expectation(skill["id"], index["release_id"])["status"], "ok")
            (b / "unrelated.txt").write_text("do not overwrite")
            with self.assertRaisesRegex(ValueError, "unrelated"):
                self.inputs.write(b)

    def test_research_rest_mcp_pairs_preserve_nullable_output(self):
        import anyio
        import httpx2
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from skills_vector.market.core import CatalogStore
        from skills_vector.market.web import create_market_app

        async def exercise():
            with tempfile.TemporaryDirectory() as d:
                output = Path(d) / "out"
                self.inputs.write(output)
                reads = RoleAtlasReads.configured(output / "roles")
                release = self.records[0].release_id
                skill = self.records[0].skill_map.skills[0].id
                pairs = [("/v1/roles", "list_roles", {}),
                         ("/v1/roles/account-executive", "get_role", {"role": "account-executive"}),
                         ("/v1/roles/forward-deployed-engineer", "get_role", {"role": "forward-deployed-engineer"}),
                         ("/v1/roles/account-executive/levels/L3", "get_role_level", {"role": "account-executive", "level": "L3"}),
                         ("/v1/roles/account-executive/graph", "get_role_graph", {"role": "account-executive"}),
                         ("/v1/roles/account-executive/industries/51", "get_role_industry", {"role": "account-executive", "naics": "51"}),
                         ("/v1/shifts?since=2026-Q3", "list_shifts", {"since": "2026-Q3"}),
                         (f"/v1/expectations/{skill}@{release}", "get_expectation", {"expectation_id": skill})]
                app = create_market_app(CatalogStore(Path(d) / "legacy"), role_catalog=reads)
                async with app.router.lifespan_context(app), httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as http:
                    async with streamable_http_client("http://127.0.0.1:8000/api/mcp", http_client=http) as (incoming, outgoing):
                        async with ClientSession(incoming, outgoing) as session:
                            await session.initialize()
                            for path, tool, args in pairs:
                                response = await http.get(path + ("&" if "?" in path else "?") + "release=" + release)
                                result = await session.call_tool(tool, {**args, "release_id": release})
                                self.assertEqual(response.status_code, 200)
                                self.assertFalse(result.is_error)
                                self.assertEqual(result.structured_content, response.json())
                                self.assertEqual(response.json()["data_kind"], "research")
                                self.assertIsNone(response.json()["fixture_caveat"])
                                self.assertNotIn("presentation", response.json())
        anyio.run(exercise)

    def test_shifts_sort_unavailable_after_numeric_without_inventing_zero(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "out"
            self.inputs.write(output)
            path = output / "roles/account-executive.json"
            payload = json.loads(path.read_text())
            numeric = payload["skill_map"]["skills"][0]
            numeric["forecast"].update(basis="backtest", probability=0, base_rate=0, reason=None)
            path.write_bytes(canonical(payload))
            items = RoleAtlasReads.configured(output / "roles").shifts("2026-Q3")["current"]
            self.assertEqual(items[0]["skill_id"], numeric["id"])
            self.assertEqual(items[0]["forecast"]["probability"], 0)
            self.assertTrue(all(item["forecast"]["probability"] is None for item in items[1:]))

    def test_unknown_levels_missing_denominators_and_empty_expectations(self):
        payload = self.records[0].to_dict()
        self.assertTrue(all(s["first_level"] is None for s in payload["skill_map"]["skills"]))
        self.assertEqual(payload["skill_map"]["skills"][0]["share_by_level"], [None] * 5)
        self.assertEqual(payload["ladder"]["levels"][0]["expectations"], [])
        for mutate, message in ((lambda p: p["skill_map"]["skills"][0]["share_by_level"].__setitem__(0, 1), "positive coverage"),
                                (lambda p: p["skill_map"]["skills"][0]["share_by_level"].__setitem__(0, 0), "missing assigned denominators"),
                                (lambda p: p["skill_map"]["skills"][0]["windows"][0].update(levels=[]), "levels"),
                                (lambda p: p["skill_map"]["skills"][0]["windows"][0].update(levels=["manager"]), "levels"),
                                (lambda p: p["skill_map"]["skills"][0].update(first_level=1), "observed mention")):
            bad = copy.deepcopy(payload)
            mutate(bad)
            with self.assertRaisesRegex(RoleValidationError, message):
                RoleRecord.from_dict(bad)
        known = next(r for r in self.records if r.id == "role.forward-deployed-engineer").to_dict()
        known["skill_map"]["skills"][0]["share_by_level"][3] = None
        with self.assertRaisesRegex(RoleValidationError, "missing assigned denominators"):
            RoleRecord.from_dict(known)
        for level in known["ladder"]["levels"]:
            for expected in level["expectations"]:
                self.assertIsNone(expected["introduced_at"])
        payload["skill_map"]["skills"] = []
        self.assertEqual(RoleRecord.from_dict(payload).skill_map.skills, [])

    def test_cadence_and_output_guards(self):
        self.assertEqual(window_of("2026-10-02T00:00:00Z", "monthly"), "2026-10")
        records, _ = self.inputs.build("monthly")
        self.assertTrue(all(r.demand_window.window_length == "monthly" for r in records))
        self.assertTrue(all(s.forecast.resolves == "2027-10" for r in records for s in r.skill_map.skills))
        with self.assertRaisesRegex(ValueError, "cadence"):
            self.inputs.build("weekly")
        for path in (RELEASE_ROOT / "new", RELEASE_ROOT.parent, MAPPING_ROOT / "new"):
            with self.assertRaisesRegex(ValueError, "protected"):
                self.inputs.write(path)

    def test_verified_mapping_and_mutated_input_rejections(self):
        mutations = [
            ("maps/employers.json", lambda v: v[0].update(source_note=""), "source note"),
            ("maps/employers.json", lambda v: v[0].update(status="unverified"), "unverified"),
            ("maps/employers.json", lambda v: v[0].update(quote="Not in this source"), "evidence"),
            ("maps/employers.json", lambda v: v[0].update(source_id="onet"), "employer source"),
            ("maps/employers.json", lambda v: v[0].update(naics="99"), "NAICS"),
            ("maps/employers.json", lambda v: v.clear(), "every employer"),
            ("maps/skills.json", lambda v: v["phrases"][0].update(phrase="Unobserved phrase"), "not in the published"),
            ("published/releases/unit/postings.json", lambda v: v[0].update(context_dimensions={"customer_industry": {"quote": "Acme primary employer sector is NAICS 51."}}), "customer industry"),
            ("maps/skills.json", lambda v: v["backbone"][0].update(quote="Invented official task"), "backbone quote"),
            ("maps/skills.json", lambda v: v["phrases"].append(v["phrases"][0]), "ambiguous"),
            ("maps/skills.json", lambda v: v["phrases"][0].update(backbone_id="unknown"), "backbone id"),
            ("maps/skills.json", lambda v: v["phrases"][0].update(confidence="guessed"), "confidence"),
            ("maps/skills.json", lambda v: v["phrases"][0].pop("rationale"), "rationale"),
            ("maps/skills.json", lambda v: v["backbone"][0].update(rationale=" "), "rationale"),
            ("maps/skills.json", lambda v: v.update(release_id="different"), "another release"),
            ("published/current.json", lambda v: v.update(current="unpublished"), "published index"),
            ("published/releases/unit/postings.json", lambda v: v.append(v[0]), "duplicate admitted"),
            ("published/releases/unit/postings.json", lambda v: v[0]["expectations"][0].update(source_sha256="wrong"), "source mismatch"),
            ("published/releases/unit/manifest.json", lambda v: v.update(release_id="changed"), "manifest hash"),
        ]
        for name, mutate, message in mutations:
            with self.subTest(name=name, message=message), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                published, maps = synthetic_tree(base)
                inputs = EvidenceInputs.read(published, maps)
                records, audit = inputs.build()
                self.assertEqual(records[0].ladder.levels[1].evidence.postings, 1)
                self.assertEqual(audit["roles"]["example"]["postings_per_level"]["L2"], 1)
                self.assertEqual(records[0].industries[0].evidence.postings, 1)
                self.assertIsNone(records[0].industries[0].profile)
                path = base / name
                value = json.loads(path.read_text())
                mutate(value)
                path.write_bytes(canonical(value))
                with self.assertRaisesRegex(ValueError, message):
                    EvidenceInputs.read(published, maps)

    def test_output_symlink_cannot_escape_into_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            published, maps = synthetic_tree(base)
            output = base / "out"
            output.mkdir()
            (output / "roles").symlink_to(published / "releases/unit", target_is_directory=True)
            before = {str(p.relative_to(published)): p.read_bytes() for p in published.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "unsafe output"):
                EvidenceInputs.read(published, maps).write(output)
            self.assertEqual(before, {str(p.relative_to(published)): p.read_bytes() for p in published.rglob("*") if p.is_file()})

    def test_mapping_confidence_controls_official_links_not_posting_counts(self):
        for confidence in ("exact", "close", "loose"):
            with self.subTest(confidence=confidence), tempfile.TemporaryDirectory() as d:
                published, maps = synthetic_tree(Path(d))
                path = maps / "skills.json"
                mapping = json.loads(path.read_text())
                mapping["phrases"][0]["confidence"] = confidence
                path.write_bytes(canonical(mapping))
                records, audit = EvidenceInputs.read(published, maps).build()
                skill = records[0].skill_map.skills[0]
                self.assertEqual(skill.windows[0].share, 1)
                self.assertEqual(skill.source_links, ["P"] if confidence == "loose" else ["P", "O"])
                self.assertEqual([s.family for s in skill.evidence], ["P"] if confidence == "loose" else ["P", "O"])
                link = audit["roles"]["example"]["postings"][0]["skill_links"][0]
                self.assertEqual(link["mapping_confidence"], confidence)
                self.assertEqual(link["backbone_id"] is None, confidence == "loose")
                self.assertIsNotNone(link["proposed_backbone_id"])

    def test_extract_hash_and_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            published, maps = synthetic_tree(Path(d))
            release = published / "releases/unit"
            (release / "job.txt").write_text("changed")
            with self.assertRaisesRegex(ValueError, "extract hash"):
                EvidenceInputs.read(published, maps)
        with tempfile.TemporaryDirectory() as d:
            published, maps = synthetic_tree(Path(d))
            path = published / "releases/unit/sources.json"
            sources = json.loads(path.read_text())
            sources[0]["extract_path"] = "../../outside.txt"
            path.write_bytes(canonical(sources))
            with self.assertRaisesRegex(ValueError, "unsafe/missing"):
                EvidenceInputs.read(published, maps)


if __name__ == "__main__":
    unittest.main()
