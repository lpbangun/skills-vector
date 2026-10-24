"""M7 synthetic structures and deterministic official lookup tests. No live IO."""
from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skills_vector.market.onet_backbone import Backbone, LOOKUP, RULES, ZIP_SHA256, derive
from skills_vector.market.posting_volume import VolumePolicy, extract_sections, write_snapshot
from skills_vector.market.role_artifacts import canonical, read_json
from skills_vector.market.role_pipeline import EvidenceInputs, taxonomy_signal, triage
from skills_vector.market.role_segments import DEFAULT_POLICY, load_policy, segment_signals
from skills_vector.market.role_signals import import_signals
from skills_vector.market.roster_probe import probe
from skills_vector.market_cli import run

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/samples/posting-volume"


class SectionsV2Tests(unittest.TestCase):
    def test_synthetic_heading_structures_not_employer_ids(self):
        for heading in ("What you’ll achieve", "The impact you will have:", "A day in the life (Responsibilities)",
                        "What you will work on", "In particular, you will be:", "In this role:"):
            for wrap in ("<h3>{}</h3>", "<p><strong>{}</strong></p>", "<p>{}</p>"):
                html = wrap.format(heading) + "<ul><li>Build synthetic test widgets.</li></ul><p><b>What we look for:</b></p><li>Experience with a test editor.</li><h2>Benefits</h2><li>Paid time off.</li>"
                result = extract_sections(html)
                self.assertEqual(result["sections"]["responsibilities"], ["Build synthetic test widgets."])
                self.assertEqual(result["sections"]["requirements"], ["Experience with a test editor."])
                for expected in result["expectations"]:
                    self.assertEqual(result["description"][expected["start"]:expected["end"]], expected["quote"])
        negative = extract_sections("<h2>Benefits</h2><p>In this role we offer paid time off.</p><h2>About the company</h2><li>Build our culture.</li>")
        self.assertFalse(negative["has_responsibilities"])
        self.assertEqual(negative["expectations"], [])

    def test_no_heading_is_conservative_and_non_duty_spans_dropped(self):
        current = extract_sections("<p>You will build synthetic widgets.</p>")
        self.assertTrue(current["has_responsibilities"])
        self.assertFalse(extract_sections("<li>Build widgets.</li>")["has_responsibilities"])
        self.assertFalse(extract_sections("<p>We build widgets for customers.</p>")["has_responsibilities"])
        body = "<h2>Responsibilities</h2><li>Task categories:</li><li>Build synthetic widgets.</li><p>We are committed to an in-office culture.</p><p>We encourage you to apply.</p>"
        self.assertEqual(extract_sections(body)["sections"]["responsibilities"], ["Build synthetic widgets."])
        old = "<p><strong>The impact you will have:</strong></p><li>Build widgets.</li>"
        self.assertFalse(extract_sections(old, method="rules/1")["has_responsibilities"])
        self.assertTrue(extract_sections(old)["has_responsibilities"])
        with self.assertRaises(ValueError): extract_sections(old, method="future/99")

    def test_frozen_legacy_snapshot_validated_before_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); policy = VolumePolicy.read(SAMPLES / "policy.sample.json")
            write_snapshot(policy, root / "capture", "2026-Q4", payload_root=SAMPLES, retrieved_at="2026-10-15T00:00:00Z", extraction_method="rules/1")
            snapshot = root / "capture/2026-Q4"
            before = {str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
            inputs = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release")
            records, _ = inputs.build()
            self.assertEqual(sum(r.demand_window.postings for r in records), 48)
            ae = next(r for r in records if r.id == "role.account-executive")
            self.assertTrue(all("M7-r1.md" in level.scope for level in ae.ladder.levels))
            self.assertTrue(any("SMB customer scope" in level.scope for level in ae.ladder.levels))
            self.assertIn("ae_segments.json", inputs.hashes)
            self.assertIn("role_segments.py", inputs.hashes)
            self.assertEqual({p["extraction_method"] for p in inputs.postings}, {"rules/2"})
            self.assertEqual(before, {str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()})
            original_read = Path.read_bytes
            def changed_recipe(path):
                value = original_read(path)
                return value + b"\n# synthetic recipe change\n" if path.name == "posting_sections.py" else value
            with patch.object(Path, "read_bytes", changed_recipe):
                changed = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release")
            self.assertNotEqual(inputs.history_groups, changed.history_groups)
            inputs.write(root / "one"); inputs.write(root / "two")
            self.assertEqual({str(p.relative_to(root / "one")): p.read_bytes() for p in (root / "one").rglob("*") if p.is_file()},
                             {str(p.relative_to(root / "two")): p.read_bytes() for p in (root / "two").rglob("*") if p.is_file()})
            path = next((snapshot / "descriptions").glob("*.raw")); path.write_bytes(path.read_bytes() + b"forged")
            with self.assertRaisesRegex(ValueError, "hash"):
                EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release")


class BackboneTests(unittest.TestCase):
    def test_exact_close_loose_unmatched_and_unsafe_aliases(self):
        b = Backbone()
        exact = b.match("account-executive", "Identify potential customers.")
        self.assertEqual(exact[0]["confidence"], "exact")
        self.assertEqual(exact[0]["id"], "onet31:dwa:4.A.1.b.1.b.2")
        software = b.match("account-executive", "Maintain synthetic customer notes using Salesforce CRM.")
        self.assertTrue(any(m["id"] == "onet31:software:2.E.6.c" for m in software))
        self.assertTrue(all(m["method"] and m["matched_text"] and m["source_zip_sha256"] == ZIP_SHA256 for m in software))
        for text in ("Identify business drivers behind all opportunities.", "Ensure high forecasting accuracy and consistency.",
                     "Enjoy a career in software (database).", "No prior experience in cold calling is required.",
                     "Use LinkedIn SalesNavigator."):
            self.assertEqual(b.match("account-executive", text), [], text)
        first = b.match("account-executive", "Identify potential customers.")
        first[0]["confidence"] = "loose"
        self.assertEqual(b.match("account-executive", "Identify potential customers.")[0]["confidence"], "exact")
        with tempfile.TemporaryDirectory() as d:
            rule = {"id": "4.A.1.b.1.b.2", "name": "synthetic-loose", "pattern": "Synthetic ambiguous wording", "confidence": "loose", "source_note": "Synthetic nonpromotion test."}
            path = Path(d) / "rules.json"; path.write_text(canonical([rule]))
            loose = Backbone(rules=path)
            e = {"source_wording": "Synthetic ambiguous wording", "dimension": "task"}
            expanded = loose.expand("account-executive", [e])
            self.assertNotIn("backbone_match", expanded[0])
            self.assertEqual(expanded[0]["backbone_proposals"][0]["confidence"], "loose")

    def test_software_and_dwa_dedup_same_span_keeps_original_quote(self):
        b = Backbone()
        original = {"source_wording": "Build a sales pipeline using Salesforce CRM.", "dimension": "task"}
        rows = b.expand("account-executive", [original])
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual({r["source_wording"] for r in rows}, {original["source_wording"]})
        self.assertEqual(len({r["backbone_match"]["id"] for r in rows}), len(rows))

    def test_derived_lookup_integrity_and_tiny_real_column_format_builder(self):
        lookup = read_json(LOOKUP)
        self.assertEqual((len(lookup["dwas"]), len(lookup["software"])), (94, 76))
        self.assertEqual(lookup["source_zip_sha256"], ZIP_SHA256)
        self.assertIn("CC BY 4.0", lookup["attribution"])
        self.assertEqual(set(lookup["source_files"]), {"GWAs to IWAs to DWAs.txt", "Tasks to DWAs.txt", "Software Skills.txt"})
        positives = {
            "market-trends": "Monitor competitive market trends", "prospecting": "Identify new customers",
            "sales-quotes": "Create customer quotes", "product-demos": "Conduct product demonstrations",
            "outreach": "Make outbound calls", "sales-negotiation": "Negotiate sales terms",
            "sales-records": "Maintain customer records", "sales-contracts": "Prepare sales contracts",
            "proposals": "Develop customer proposals", "explain-products": "Explain technical product information",
            "recommend-products": "Recommend products for customer needs", "advise-product-use": "Advise customers on products",
            "sales-campaigns": "Collaborate with marketing", "software-design": "Design software applications",
            "technical-support": "Provide technical support", "hr-advice": "Advise leaders about HR",
            "business-relationships": "Build business relationships", "pipeline-management": "Manage a sales pipeline",
            "close-sales": "Close new business", "product-learning": "Build understanding of products",
            "territory-business-plans": "Develop territory plan", "market-feedback": "Share customer feedback",
            "customer-discovery": "Identify customer needs", "cross-team-coordination": "Collaborate with teams",
            "sales-presentations": "Deliver sales presentations", "forecast-data-analysis": "Analyze forecast data"}
        for row in read_json(RULES):
            regex = re.compile(row["pattern"], re.I)
            self.assertIsNotNone(regex.search(positives[row["name"]]), row["name"])
            self.assertIsNone(regex.search("Synthetic company benefits and holiday policy."))
            self.assertIn(row["confidence"], ("exact", "close", "loose"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); db = root / "db"; db.mkdir()
            (db / "GWAs to IWAs to DWAs.txt").write_text("GWA Element ID\tIWA Element ID\tDWA Element ID\tDWA Element Name\n4.A.1\t4.A.1.a\t4.A.1.b.1.b.2\tIdentify potential customers.\n")
            (db / "Tasks to DWAs.txt").write_text("O*NET-SOC Code\tTask ID\tDWA Element ID\tDate\tDomain Source\n41-4012.00\t1\t4.A.1.b.1.b.2\t01/2026\tAnalyst\n")
            (db / "Software Skills.txt").write_text("O*NET-SOC Code\tWorkplace Example\tElement ID\tElement Name\tHot Technology\tIn Demand\n41-4012.00\tSample CRM\t2.E.6.c\tCRM software\tY\tY\n")
            mappings = root / "mappings.json"; mappings.write_text(canonical({"roles": {"account-executive": {"anchor": {"code": "41-4012.00"}}}}))
            # No global candidates in this tiny source; do not pretend missing ids exist.
            with patch("skills_vector.market.onet_backbone.read_json", wraps=read_json) as reader:
                def read(path):
                    return [] if path == RULES else read_json(path)
                reader.side_effect = read
                one, two = root / "one.json", root / "two.json"
                self.assertEqual(derive(db, mappings, one), derive(db, mappings, two))
                self.assertEqual(one.read_bytes(), two.read_bytes())
                (db / "Software Skills.txt").write_text("wrong header\n")
                with self.assertRaises((KeyError, ValueError)):
                    derive(db, mappings, root / "bad.json")

    def test_taxonomy_presence_does_not_claim_hot_technology_or_market_confirmation(self):
        for kind in ("dwa", "software"):
            result = taxonomy_signal({"kind": kind, "id": "synthetic-taxonomy", "method": "synthetic-exact", "confidence": "exact", "matched_text": "Synthetic tool"})
            self.assertEqual((result["family"], result["timing"], result["direction"]), ("O", "lagging", "presence"))
            self.assertNotEqual(result["source_kind"], "onet_hot_technologies")
            self.assertIn("not upward confirmation or a Hot Technology flag", result["reading"])

    def test_31_hot_technologies_import_uses_real_renamed_columns(self):
        path = ROOT / "tests/samples/role-atlas/Software Skills.sample.tsv"
        bundle = import_signals("hot-technologies", path, "2026-10-23T00:00:00Z", sample=True)
        self.assertEqual(len(bundle["records"]), 1)
        row = bundle["records"][0]
        self.assertEqual(row["label"], "Sample CRM tool")
        self.assertEqual((row["signal"]["family"], row["signal"]["timing"]), ("M", "coincident"))
        self.assertIn("2.E.6.c", row["signal"]["reading"])
        self.assertNotIn("commodity", row["signal"]["reading"])


class ScopeAndRosterTests(unittest.TestCase):
    def test_current_duty_scope_not_title_tenure_or_qualification(self):
        p = {"occupation_slug": "account-executive", "title": "Senior Account Executive II"}
        for quote in ("Manage the full sales cycle for a bounded book.", "Manage the complete sales cycle, from initial contact to close, meeting quarterly sales quotas.",
                      "Lead multi-stakeholder sales cycles.", "Architect and execute current go-to-market strategies.",
                      "Own your pipeline and keep CRM hygiene.", "Prospect and exceed quota.", "Independently manage accounts."):
            self.assertIsNone(triage(p, [quote]).level, quote)
        self.assertIsNone(triage(p, ["Manage the full sales cycle for a bounded book."], role_scope_rules=False).level)
        self.assertEqual(triage(p, ["Manage the full sales cycle.", "Work under supervision."]).level, 1)
        for quote in ("Experience managing the full sales cycle.", "Proven ability to own architecture.", "10 years managing sales cycles."):
            self.assertIsNone(triage(p, [quote]).level)
        self.assertIsNone(triage(p, []).level)

    def test_reviewed_segments_positive_negative_and_other_roles(self):
        names = ["SMB", "Commercial", "Velocity", "Select", "Mid-Market", "Territory", "Enterprise", "Corporate",
                 "State Enterprise", "Public Sector Named Account", "Strategic", "Named", "Global", "Key"]
        policy = load_policy()
        for row, name in zip(policy["rows"], names):
            p = {"occupation_slug": "account-executive", "title": name + " Account Executive"}
            decision = triage(p, [])
            self.assertEqual(decision.level, row["level"], name)
            self.assertEqual({s["segment"] for s in decision.strong}, {row["segment"]})
            p["title"] = "Senior Account Executive II"
            self.assertEqual(triage(p, ["Own " + name + " accounts."]).level, row["level"], name)
            for quote in ("Experience managing " + name + " accounts.", "Do not own " + name + " accounts.", "Use " + name + " software."):
                self.assertIsNone(triage(p, [quote]).level, quote)
            p.update(occupation_slug="forward-deployed-engineer", title=name + " Engineer")
            self.assertIsNone(triage(p, []).level)
        with tempfile.TemporaryDirectory() as d:
            data = read_json(DEFAULT_POLICY); data["rows"][0]["rationale"] = ""
            path = Path(d) / "bad.json"; path.write_text(canonical(data))
            with self.assertRaises(ValueError): load_policy(path)

    def test_segment_conflicts_and_generic_geography_are_not_hidden(self):
        p = {"occupation_slug": "account-executive", "title": "Enterprise Account Executive"}
        self.assertEqual(triage(p, ["Create and manage a pipeline of Enterprise accounts.", "Meet quarterly sales quotas."]).level, 4)
        self.assertEqual(triage(p, ["Develop strategic account plans.", "Expand reach within assigned territory."]).level, 4)
        self.assertEqual(triage({**p, "title": "Strategic Account Executive"}, ["Manage the complete sales cycle and meet quota."]).level, 5)
        for title, duties in (("SMB Account Executive", ["Own Enterprise accounts."]),
                               ("Enterprise Account Executive", ["Own Named accounts."]),
                               ("Commercial SMB Account Executive", []), ("Territory (Mid-Market) Account Executive", [])):
            result = triage({**p, "title": title}, duties)
            self.assertIsNone(result.level)
            self.assertEqual(result.reason, "conflicting_customer_segments")
        self.assertEqual(triage(p, ["Manage direct reports."]).reason, "people_management_is_a_linked_track")
        p["title"] = "Account Executive"
        for quote in ("Develop strategic account plans.", "Drive global growth strategy.", "Build key decision-maker relationships.",
                      "Manage the complete sales cycle and meet quarterly sales quotas.", "Expand reach within assigned territory."):
            self.assertIsNone(triage(p, [quote]).level, quote)

    def test_roster_probe_offline_totals_draft_and_cli_no_network(self):
        with tempfile.TemporaryDirectory() as d, patch("socket.socket", side_effect=AssertionError("no live calls")):
            out = Path(d) / "draft.json"
            result = probe(SAMPLES / "policy.sample.json", out, payload_root=SAMPLES)
            self.assertFalse(result["reviewed"])
            self.assertEqual(len(result["boards"]), 6)
            self.assertTrue(all(r["exists"] and r["postings_total"] == 8 and not r["reviewed"] for r in result["boards"]))
            self.assertTrue(all(r["matched_titles"]["account-executive"] == 2 and r["source_note"] for r in result["boards"]))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run(["posting-volume", "roster-probe", "--candidates", str(SAMPLES / "policy.sample.json"), "--payload-root", str(SAMPLES), "--output", str(Path(d) / "cli.json")]), 0)
            bad = read_json(SAMPLES / "policy.sample.json"); bad["boards"][0]["sample_status"] = 404
            path = Path(d) / "candidates.json"; path.write_text(canonical(bad))
            missing = probe(path, Path(d) / "missing.json", payload_root=SAMPLES)
            self.assertTrue(any(r["exists"] is False and r["postings_total"] is None for r in missing["boards"]))
            bad["boards"][0]["token"] = "../escape"; path.write_text(canonical(bad))
            with self.assertRaises(ValueError): probe(path, Path(d) / "bad.json", payload_root=SAMPLES)

    def test_fractional_live_percent_labels_are_rounded_only_for_display(self):
        source = (ROOT / "preview/role-atlas.js").read_text()
        helpers = source[source.index("  function percentage"):source.index("  function hasOfficial")]
        result = subprocess.run(["node", "-e", helpers + 'console.log(JSON.stringify([shareLabel(51.92307692307692),shareLabel(null),shareLabel(0),shareLabel(100)]));'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["51.9%", "—", "0%", "100%"])

    def test_rechecked_labels_and_heldout_audit_preserve_unfavorable_results(self):
        root = ROOT / "docs/role-atlas/audits"
        old = read_json(root / "M7-levels.json")
        revised = read_json(root / "M7-r1-levels.json")
        self.assertEqual([c["job_id"] for c in old["cases"]], [c["job_id"] for c in revised["cases"]])
        self.assertEqual(sum(c["changed"] for c in revised["cases"]), 34)
        self.assertEqual([c["level"] for c in old["cases"]], [c["previous_level"] for c in revised["cases"]])
        self.assertTrue(all(c["rationale"] and c["quote_origin"] in ("native_title", "current_duty") for c in revised["cases"]))
        held = read_json(root / "M7-r1-sections.json")
        self.assertNotEqual(held["seed"], read_json(root / "M7-sections.json")["seed"])
        self.assertEqual(len(held["cases"]), 20)
        self.assertFalse(set(held["excluded_job_ids"]) & {c["job_id"] for c in held["cases"]})
        self.assertEqual(sum(len(c["responsibility_span_hashes"]) for c in held["cases"]), 129)
        self.assertEqual(sum(not c["judged_correct"] for c in held["cases"]), 1)
        self.assertEqual(sum(len(c["wrong_spans"]) for c in held["cases"]), 1)
        html = (ROOT / "preview/role-atlas.html").read_text()
        self.assertIn("a reviewed role-specific scope policy", html)
        self.assertNotIn("title and years are weak signals", html)

    def test_audit_files_are_explicit_manual_labels_not_model_accuracy(self):
        root = ROOT / "docs/role-atlas/audits"
        for name, n in (("M7-sections.json", 20), ("M7-matches.json", 30), ("M7-levels.json", 40)):
            data = read_json(root / name)
            self.assertEqual(len(data["cases"]), n)
            self.assertIn("not independent expert", data["reviewer"])
            self.assertTrue(all(case["quote"] for case in data["cases"]))
        matches = read_json(root / "M7-matches.json")
        self.assertEqual(sum(c["judged_correct"] for c in matches["cases"]), 29)
        self.assertFalse(matches["cases"][-1]["judged_correct"])


if __name__ == "__main__":
    unittest.main()
