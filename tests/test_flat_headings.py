"""M9-r4 flattened-text headings and source-bound dispatch/replay."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from skills_vector.market.flat_headings import HEADINGS, METHOD, eligible
from skills_vector.market.posting_sections import extract as structural
from skills_vector.market.posting_volume import VolumePolicy, extract_sections, write_snapshot
from skills_vector.market.role_artifacts import read_json
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.structured_career import job_nodes, local_transport

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/samples/jsonld"
FLAT = (
    "Account Executive Responsibilities Strategic advisory and business case. "
    "Serve as a partner and explain the responsibilities of the team. "
    "Required qualifications Experience with SQL. Preferred Qualifications Familiarity with Python. "
    "About Authored We build useful products. Benefits Health insurance is available. "
    "Pay Salary depends on location. Compensation Bonus is available. "
    "Equal Opportunity We welcome everyone. Accommodations Requests are handled separately."
)


class FlatHeadingsTests(unittest.TestCase):
    def test_inline_sections_sentence_spans_decoy_and_excluded_tails(self):
        out = extract_sections(FLAT, employer="Authored")
        self.assertEqual(out["extraction_method"], METHOD)
        self.assertEqual(out["description"], FLAT)
        self.assertEqual(out["source_sha256"], hashlib.sha256(FLAT.encode()).hexdigest())
        self.assertTrue(out["has_responsibilities"])
        self.assertEqual(out["sections"]["responsibilities"], [
            "Strategic advisory and business case.",
            "Serve as a partner and explain the responsibilities of the team.",
        ])
        self.assertEqual(out["sections"]["requirements"], ["Experience with SQL."])
        self.assertEqual(out["sections"]["preferred"], ["Familiarity with Python."])
        self.assertEqual(len(out["expectations"]), 4)
        self.assertEqual([b["section"] for b in out["section_boundaries"]], [
            "responsibilities", "requirements", "preferred", "company", "benefits", "benefits", "benefits", "legal", "legal"])
        for e in out["expectations"]:
            self.assertEqual(FLAT[e["start"]:e["end"]], e["quote"])
        self.assertNotIn("the responsibilities", [b["heading"] for b in out["section_boundaries"]])

    def test_clause_case_capitalization_and_short_title_boundaries(self):
        for text in ("We discuss Responsibilities With the team.",
                     "Responsibilities include routine duties.",
                     "responsibilities Build systems.", "We describe the responsibilities of the team."):
            with self.subTest(text=text):
                self.assertFalse(extract_sections(text)["has_responsibilities"])
        for text in ("We build products. Responsibilities Build systems.",
                     "Senior Account Executive Responsibilities Build systems."):
            self.assertEqual(extract_sections(text)["sections"]["responsibilities"], ["Build systems."])
        # A capitalized heading in a long sentence is not silently treated as a clause.
        text = "We discuss the organization and how we assign Responsibilities Build systems."
        self.assertFalse(extract_sections(text)["has_responsibilities"])
        # Strict capitalization intentionally does not accept a numeric-only qualifier start.
        out = extract_sections("Required qualifications 5+ years in sales.")
        self.assertNotIn("requirements", out["sections"])

    def test_pay_transparency_intro_is_a_stopping_boundary_not_a_duty(self):
        text = ("Responsibilities Build products. "
                "The minimum and maximum full-time annual salaries for this role are listed below, by location. "
                "Please review the local salary range.")
        out = extract_sections(text)
        self.assertEqual(out["sections"]["responsibilities"], ["Build products."])
        self.assertEqual(out["section_boundaries"][-1]["section"], "legal")

    def test_all_requested_literal_variants(self):
        requested = {
            "responsibilities": ["Responsibilities", "What you'll do", "What You'll Do", "Your Impact",
                                 "Key Responsibilities", "About the role"],
            "requirements": ["Required qualifications", "Basic Qualifications", "Minimum Qualifications",
                             "What you'll bring", "What You'll Bring", "Qualifications", "About You"],
            "preferred": ["Preferred qualifications", "Preferred Qualifications"],
        }
        for section, headings in requested.items():
            for heading in headings:
                with self.subTest(heading=heading):
                    out = extract_sections(heading + " Build systems.")
                    self.assertIn(heading, HEADINGS[section])
                    self.assertEqual(out["sections"][section], ["Build systems."])

    def test_markup_and_line_breaks_reject_flat_path_and_preserve_rules_output(self):
        for text in ("<h2>Responsibilities</h2><p>Build systems.</p>",
                     "<DIV>Responsibilities Build systems.</DIV>",
                     "<span>Responsibilities Build systems.</span>",
                     "<strong>Responsibilities</strong> Build systems.",
                     "Responsibilities\n- Build systems.", "Responsibilities\rBuild systems.",
                     "Responsibilities\u2028Build systems."):
            with self.subTest(text=text):
                self.assertFalse(eligible(text))
                actual = extract_sections(text, omit_contacts=False)
                self.assertEqual(actual, structural(text))
                self.assertNotIn("extraction_method", actual)
                with self.assertRaisesRegex(ValueError, "without block tags"):
                    extract_sections(text, method=METHOD)
        prose = "You will manage accounts and deliver projects."
        self.assertEqual(extract_sections(prose, omit_contacts=False), structural(prose))
        legacy = extract_sections(FLAT, flat_headings=False, omit_contacts=False)
        self.assertEqual(legacy, structural(FLAT))
        self.assertNotIn("extraction_method", extract_sections(FLAT, method="rules/1"))

    def test_flat_capture_method_recipe_exact_offsets_and_deterministic_replay(self):
        value = read_json(SAMPLES / "policy.sample.json")
        policy = VolumePolicy.from_value(value, SAMPLES / "policy.sample.json")
        base = {b["origin"].rstrip("/"): local_transport(SAMPLES, b["payload"]) for b in policy.value["boards"]}

        def transport(url):
            origin = next(o for o in base if url.startswith(o + "/"))
            status, headers, body = base[origin](url)
            if "/careers/HR-Generalist" in url:
                node = deepcopy(job_nodes(body.decode())[0])
                node["description"] = FLAT
                body = ('<script type="application/ld+json">' + json.dumps(node) + '</script>').encode()
            return status, headers, body

        with tempfile.TemporaryDirectory() as d, patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
            root = Path(d)
            for label in ("one", "two"):
                write_snapshot(policy, root / label, "2026-Q4", payload_root=SAMPLES,
                               retrieved_at="2026-10-15T00:00:00Z", transport=transport)
            snapshot = root / "one/2026-Q4"
            for path in snapshot.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (root / "two/2026-Q4" / path.relative_to(snapshot)).read_bytes())
            manifest = read_json(snapshot / "manifest.json")
            self.assertEqual(manifest["extraction_method"], "rules/2")
            self.assertEqual(manifest["flat_extraction"]["method"], METHOD)
            self.assertEqual(manifest["flat_extraction"]["sha256"],
                             hashlib.sha256((ROOT / "src/skills_vector/market/flat_headings.py").read_bytes()).hexdigest())
            self.assertEqual(sum(r["admitted"] for r in manifest["roles"].values()), 10)
            rows = read_json(snapshot / "postings.json")
            self.assertEqual({p["extraction_method"] for p in rows}, {"rules/2", METHOD})
            for row in rows:
                expected = METHOD if row["occupation_slug"] == "hr-generalist" else "rules/2"
                self.assertEqual(row["extraction_method"], expected)
                text = (snapshot / row["description_path"]).read_text()
                for quote in row["expectations"]:
                    self.assertEqual(text[quote["start"]:quote["end"]], quote["source_wording"])
            inputs = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release")
            inputs.write(root / "a")
            inputs.write(root / "b")
            for path in (root / "a").rglob("*.json"):
                self.assertEqual(path.read_bytes(), (root / "b" / path.relative_to(root / "a")).read_bytes())


if __name__ == "__main__":
    unittest.main()
