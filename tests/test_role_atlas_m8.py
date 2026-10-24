"""M8 exact lexical grouping, publication quotes, policy and large-catalog controls."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from skills_vector.market.onet_backbone import Backbone
from skills_vector.market.posting_volume import VolumePolicy, write_snapshot
from skills_vector.market.role_artifacts import canonical, read_json
from skills_vector.market.role_pipeline import EvidenceInputs, skill_identity, triage
from skills_vector.market.role_quotes import notice, project, ranges
from skills_vector.market.role_terms import ToolTerms, SOFTWARE, EXAMPLES, TERMS, derive_examples

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/samples/posting-volume"


class TermsTests(unittest.TestCase):
    def test_every_software_alias_positive_boundary_and_case(self):
        matcher = ToolTerms()
        for row in read_json(SOFTWARE)["rows"]:
            for alias in row["aliases"]:
                context = " programming languages software skills and experience with design tools."
                text = "Use " + alias["text"] + context
                matches = matcher.match(text)
                target = "onet31:example:" + row["id"]
                self.assertIn(target, {m["id"] for m in matches}, (row["id"], alias))
                self.assertNotIn(target, {m["id"] for m in matcher.match("prefix" + alias["text"] + "suffix" + context)}, alias)
                hit = next(m for m in matches if m["id"] == target)
                self.assertEqual(text[hit["start"]:hit["end"]], hit["matched_text"])
                self.assertTrue(hit["native_bindings"])
                self.assertEqual((hit["family"], hit["kind"]), ("O", "software-example"))
        for text in ("Drive Go-to-market strategy with software experience.", "Go and do great work.", "Experience supporting R&D teams.", "Excel in collaboration.", "A snowflake fell.", "python/sql/snowflake", "Join Figma and build our business.", "Experience growing C-suite relationships with insurance buyers.", "Build skills from C-level to end-users.", "Own MQL-to-SQL conversion.", "Lead within MongoDB GTM.", "Build a plan internally at Databricks.", "Work with the Databricks technical team."):
            self.assertEqual(matcher.match(text), [], text)

    def test_every_posting_term_alias_positive_negative_and_p_only(self):
        matcher = ToolTerms()
        for row in read_json(TERMS)["rows"]:
            for alias in row["aliases"]:
                context = " using synthetic AI models and prompts."
                text = "Build " + alias["text"] + context
                matches = matcher.match(text)
                self.assertIn(row["id"], {m["id"] for m in matches}, alias)
                self.assertNotIn(row["id"], {m["id"] for m in matcher.match("prefix" + alias["text"] + "suffix" + context)}, alias)
                match = next(m for m in matches if m["id"] == row["id"])
                self.assertEqual((match["family"], match["kind"]), ("P", "posting-term"))
                self.assertNotIn("source_zip_sha256", match)
                key, data, _ = skill_identity({"source_wording": text, "term_match": match}, "forward-deployed-engineer", {}, {}, rules=True)
                self.assertEqual(key, "skill.posting." + row["id"])
                self.assertIsNone(data["node"])
        for text in ("Use agents to answer customer support calls.", "Run employee evals.", "Fine tuning the marketing campaign.", "Give a prompt reply."):
            self.assertEqual(matcher.match(text), [], text)

    def test_multiple_tools_terms_and_specific_products_not_substrings(self):
        matches = ToolTerms().match("Build LLM agents with Python, SQL, Snowflake and React Native.")
        self.assertEqual({m["id"] for m in matches}, {"term.llm", "term.agents", "onet31:example:python", "onet31:example:sql", "onet31:example:snowflake", "onet31:example:react-native"})
        self.assertNotIn("onet31:example:java", {m["id"] for m in ToolTerms().match("Use JavaScript.")})
        self.assertEqual(Backbone(canonical=True).match("forward-deployed-engineer", "No prior experience with Python is required."), [])
        expanded = Backbone(canonical=True).expand("forward-deployed-engineer", [{"source_wording": "Build LLM agents with Python and SQL."}])
        self.assertTrue(any("term_match" in e for e in expanded))
        self.assertTrue(any(e.get("backbone_match", {}).get("kind") == "software-example" for e in expanded))
        self.assertTrue(all(e["source_wording"] == "Build LLM agents with Python and SQL." for e in expanded))

    def test_invalid_alias_provenance_short_names_and_official_promotion_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); software = read_json(SOFTWARE); examples = read_json(EXAMPLES); terms = read_json(TERMS)
            sw, ex, lex = root / "software.json", root / "examples.json", root / "terms.json"
            def save():
                sw.write_text(canonical(software)); examples["aliases_sha256"] = hashlib.sha256(sw.read_bytes()).hexdigest()
                ex.write_text(canonical(examples)); lex.write_text(canonical(terms))
            save(); ToolTerms(sw, ex, lex)
            software["short_allowlist"] = []; save()
            with self.assertRaisesRegex(ValueError, "short software"): ToolTerms(sw, ex, lex)
            software = read_json(SOFTWARE); terms["rows"][0]["family"] = "O"; save()
            with self.assertRaisesRegex(ValueError, "must stay P"): ToolTerms(sw, ex, lex)
            terms = read_json(TERMS); save(); sw.write_text(sw.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "hash mismatch"): ToolTerms(sw, ex, lex)

    def test_official_example_derivation_native_format_and_missing_binding(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); db = root / "db"; db.mkdir()
            native = db / "Software Skills.txt"
            native.write_text("O*NET-SOC Code\tWorkplace Example\tElement ID\tElement Name\n15-1252.00\tPython\t2.E.1.d\tSynthetic language category\n")
            aliases = root / "aliases.json"; aliases.write_text(canonical({"rows": [{"id": "python", "source_examples": ["Python"]}]}))
            a, b = root / "a.json", root / "b.json"
            self.assertEqual(derive_examples(db, a, aliases), derive_examples(db, b, aliases))
            self.assertEqual(a.read_bytes(), b.read_bytes())
            aliases.write_text(canonical({"rows": [{"id": "missing", "source_examples": ["Not in native file"]}]}))
            with self.assertRaisesRegex(ValueError, "exact native"): derive_examples(db, root / "bad.json", aliases)


class PublicationAndPolicyTests(unittest.TestCase):
    def test_sentence_ranges_abbreviations_offsets_and_no_full_paragraph_export(self):
        quote = 'Build widgets in Python. Deploy models with SQL (e.g. PostgreSQL). Write documentation!'
        self.assertEqual([quote[a:b] for a,b in ranges(quote)], ['Build widgets in Python.', 'Deploy models with SQL (e.g. PostgreSQL).', 'Write documentation!'])
        description = "Responsibilities\n" + quote
        expected = {"source_wording": quote, "start": 17, "end": 17+len(quote), "source_sha256": hashlib.sha256(description.encode()).hexdigest(), "expectation_id": "synthetic-parent"}
        posting = {"source_id": "synthetic-source", "expectations": [expected]}
        out = project(posting, description)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(description[e["start"]:e["end"]] == e["source_wording"] for e in out))
        self.assertTrue(all(e["parent_expectation_id"] == "synthetic-parent" for e in out))
        self.assertTrue(all(e["source_wording"] != quote for e in out))
        expected["source_wording"] = "forged quote"
        with self.assertRaisesRegex(ValueError, "source-bound"): project(posting, description)

    def test_notice_quarantine_does_not_remove_hr_duties_or_programming(self):
        for text in ("WA", "$149,000—$157,000 USD", "For US based candidates, the base pay range follows.", "Please read more about our benefits offerings here.", "Additionally, this role is eligible for a new hire equity grant."):
            self.assertTrue(notice(text), text)
        for text in ("Own compensation and benefits programs.", "Manage global employee equity programs.", "Build models with Python, R and SQL."):
            self.assertFalse(notice(text), text)

    def test_m7_r2_same_level_agreement_named_duties_do_not_assign(self):
        p = {"occupation_slug": "account-executive", "title": "Territory (Mid-Market) Account Executive"}
        self.assertEqual(triage(p, []).level, 3)
        self.assertEqual(triage({**p, "title": "Enterprise Corporate Account Executive"}, []).level, 4)
        self.assertEqual(triage({**p, "title": "Named Account Executive"}, []).level, 5)
        self.assertIsNone(triage({**p, "title": "Account Executive"}, ["Own a named book of accounts."]).level)
        self.assertEqual(triage({**p, "title": "Enterprise Account Executive"}, ["Own named accounts."]).level, 4)
        self.assertIsNone(triage({**p, "title": "SMB Account Executive"}, ["Own Enterprise accounts."]).level)
        old = read_json(ROOT / "docs/role-atlas/audits/M7-r1-levels.json")
        new = read_json(ROOT / "docs/role-atlas/audits/M8-levels.json")
        self.assertEqual(sum(c["changed"] for c in new["cases"]), 5)
        self.assertEqual([c["job_id"] for c in old["cases"]], [c["job_id"] for c in new["cases"]])
        self.assertEqual([c["level"] for c in old["cases"]], [c["previous_level"] for c in new["cases"]])
        self.assertEqual([hashlib.sha256(c["quote"].encode()).hexdigest() for c in old["cases"]], [c["previous_quote_sha256"] for c in new["cases"]])

    def test_synthetic_native_snapshot_pipeline_tools_terms_determinism_and_family(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); payloads = root / "payloads"; payloads.mkdir()
            value = read_json(SAMPLES / "policy.sample.json")
            value["roles"] = {"forward-deployed-engineer": value["roles"]["forward-deployed-engineer"]}
            value["boards"] = []
            for i in range(5):
                native = {"jobs": [{"id": i + 1, "title": "Forward Deployed Engineer", "absolute_url": f"https://job-boards.greenhouse.io/synthetic/jobs/{i}", "location": {"name": "New York, NY, United States"}, "content": "<h2>Responsibilities</h2><li>Build LLM agents with Python, SQL and Snowflake.</li><li>Own the architecture.</li>"}]}
                (payloads / f"{i}.json").write_text(json.dumps(native))
                value["boards"].append({"ats": "greenhouse", "token": f"synthetic{i}", "employer": f"Synthetic {i}", "source_note": "Authored synthetic employer identity.", "payload": f"{i}.json"})
            policy = VolumePolicy.from_value(value, root / "policy.json")
            write_snapshot(policy, root / "capture", "2026-Q4", payload_root=payloads, retrieved_at="2026-10-24T00:00:00Z")
            inputs = EvidenceInputs.from_snapshots([root / "capture/2026-Q4"], ROOT / "preview/release")
            records, _ = inputs.build(); role = records[0]
            self.assertEqual(role.demand_window.postings, 5)
            byid = {s.id: s for s in role.skill_map.skills}
            self.assertEqual(byid["skill.onet31.example.python"].source_links, ["P", "O"])
            for key in ("skill.posting.term.llm", "skill.posting.term.agents"):
                self.assertEqual(byid[key].source_links, ["P"])
                self.assertEqual({s.family for s in byid[key].evidence}, {"P"})
            self.assertTrue(all(s.forecast.basis == "none" for s in role.skill_map.skills))
            inputs.write(root / "a"); inputs.write(root / "b")
            self.assertEqual({str(p.relative_to(root / "a")):p.read_bytes() for p in (root / "a").rglob("*") if p.is_file()},
                             {str(p.relative_to(root / "b")):p.read_bytes() for p in (root / "b").rglob("*") if p.is_file()})

    def test_ledger_hashes_each_source_once_without_weakening_pinning(self):
        from skills_vector.market.role_atlas import RoleCatalogStore
        from skills_vector.market.role_artifacts import digest
        from skills_vector.market.role_ledger import create_ledger, track_record
        record = RoleCatalogStore.fixtures().role("account-executive")
        with patch("skills_vector.market.role_ledger.digest", wraps=digest) as calls:
            ledger = create_ledger([record])
            self.assertEqual(sum(c.args[0] is record for c in calls.call_args_list), 1)
        self.assertTrue(all(e["source_sha256"] == digest(record) for e in ledger["entries"]))
        with patch("skills_vector.market.role_ledger.digest", wraps=digest) as calls:
            report = track_record([record], ledger)
            self.assertEqual(sum(c.args[0] is record for c in calls.call_args_list), 1)
        self.assertEqual(report["tracked_count"], len(ledger["entries"]))
        changed = copy.deepcopy(record); changed["label"] += " changed"
        with self.assertRaisesRegex(ValueError, "source record mismatch"): track_record([changed], ledger)

    def test_checked_in_research_release_quotes_terms_ledger_and_top20(self):
        from skills_vector.market.role_artifacts import load_roles
        from skills_vector.market.role_ledger import read_ledger, track_record
        from skills_vector.market.role_reads import RoleAtlasReads
        release = ROOT / "research/role-atlas/2026-Q4"
        records = load_roles(release / "roles")
        self.assertEqual(len(records), 4)
        self.assertEqual({r["data_kind"] for r in records}, {"research"})
        self.assertEqual({r["demand_window"]["id"] for r in records}, {"2026-Q4"})
        self.assertEqual(sum(r["demand_window"]["postings"] for r in records), 489)
        for record in records:
            for skill in record["skill_map"]["skills"]:
                self.assertEqual(skill["forecast"]["basis"], "none")
                self.assertIsNone(skill["forecast"]["probability"])
                if skill["id"].startswith("skill.posting.term."):
                    self.assertEqual(skill["source_links"], ["P"])
                    self.assertEqual({s["family"] for s in skill["evidence"]}, {"P"})
        from skills_vector.market.role_publication import read_audit
        audit, status = read_audit(release)
        self.assertIn(status, ("audit verified locally", "audit not available locally"))
        self.assertNotIn("descriptions", {p.name for p in release.iterdir()})
        for role in (audit["roles"].values() if audit else []):
            for posting in role["postings"]:
                for link in posting["skill_links"]:
                    self.assertEqual(len(ranges(link["phrase"])), 1)
                    self.assertEqual(link["projection_extraction"], "sentence-spans/1")
        ledger = read_ledger(release / "ledger.json")
        stats = track_record(records, ledger)
        self.assertEqual(stats["tracked_count"], sum(len(r["skill_map"]["skills"]) for r in records))
        self.assertEqual(stats["basis_counts"], {"none": stats["tracked_count"], "fixture": 0, "backtest": 0})
        self.assertEqual((stats["scored_count"], stats["brier_score"], stats["calibration_bins"]), (0,None,[]))
        self.assertEqual(stats["backtest"]["status"], "not run: no historical posting archive")
        reads = RoleAtlasReads.configured(release / "roles", ledger_path=release / "ledger.json")
        result = reads.role("forward-deployed-engineer")
        self.assertEqual(result["role"], next(r for r in records if r["id"] == "role.forward-deployed-engineer"))
        fde = result["role"]
        top = sorted(fde["skill_map"]["skills"], key=lambda s:(-s["windows"][-1]["share"], 0 if s["id"].startswith(("skill.onet31.","skill.posting.term.")) else 1, s["id"]))[:20]
        self.assertTrue({"skill.posting.term.agents","skill.posting.term.evals","skill.posting.term.llm","skill.posting.term.mlops"} <= {s["id"] for s in top})
        raw_top = {s["id"] for s in top if not s["id"].startswith(("skill.onet31.","skill.posting.term."))}
        matcher = ToolTerms()
        for posting in (audit["roles"]["forward-deployed-engineer"]["postings"] if audit else []):
            for link in posting["skill_links"]:
                if link["skill"] in raw_top:
                    self.assertEqual(matcher.match(link["phrase"]), [], "applicable software/term alias left as a raw top-20 phrasing")

    def test_new_match_audit_is_frozen_and_unfavorable_cases_are_retained(self):
        audit = read_json(ROOT / "docs/role-atlas/audits/M8-matches.json")
        global_cases = audit["selections"]["global"]
        self.assertNotEqual(global_cases["seed"], 3100)
        self.assertEqual(len(global_cases["cases"]), 30)
        self.assertEqual(sum(c["judged_correct"] for c in global_cases["cases"]), 29)
        self.assertTrue(all(c["quote"] and c["rationale"] for c in global_cases["cases"]))
        self.assertEqual(audit["selections"]["hr-generalist"]["cases"], [])
        self.assertEqual(sum(c["judged_correct"] for c in audit["selections"]["forward-deployed-engineer"]["cases"]), 29)

    def test_overview_floor_boundaries_and_full_record_not_filtered(self):
        source = (ROOT / "preview/role-atlas.js").read_text()
        helpers = source[source.index("  var expandedRows"):source.index("  function skillRow")]
        code = 'let current={raw:{skill_map:{skills:Array(100)}}};function role(){return current};var state={role:"test"};' + helpers + '''
const a=overviewFloor();current.raw.skill_map.skills=Array(101);const b=overviewFloor();
const hidden=!!rowVisible({employers:2,status:"early"}),shown=!!rowVisible({employers:3,status:"early"});
expandedRows["test:early"]=true;console.log(JSON.stringify([a,b,hidden,shown,!!rowVisible({employers:1,status:"early"}),current.raw.skill_map.skills.length]));'''
        result = subprocess.run(["node", "-e", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [0,3,False,True,True,101])


if __name__ == "__main__":
    unittest.main()
