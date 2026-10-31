"""M10 rules/3: reviewed non-skill rules, exact qualification spans and ranking."""
from copy import deepcopy
import hashlib
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from skills_vector.market.role_artifacts import read_json
from skills_vector.market.role_atlas import Measurement, derive_status, RoleValidationError
from skills_vector.market.role_pipeline import employer_weighted_share, sector_sample_note
from skills_vector.market.skill_rules import SkillRules, TABLE


def posting(quote, *, section="requirements", employer="Unum"):
    text = "Intro. " + quote
    return text, {"source_id": "authored", "employer": employer, "dedup_key": "one", "extraction_method": "flat-headings/1",
                  "expectations": [{"source_wording": quote, "start": 7, "end": len(text), "source_id": "authored",
                                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(), "expectation_id": "parent",
                                    "section": section, "dimension": "capability"}]}


class Rules3Tests(unittest.TestCase):
    def setUp(self):
        self.rules = SkillRules()

    def test_each_reviewed_rule_has_positive_and_real_duty_negative(self):
        positives = {
            "company_profile": "Company: Unum is a global benefits company.",
            "company_voice": "We are a growing company.",
            "employer_profile": "Unum is a benefits company.",
            "company_marketing": "Follow us on LinkedIn.",
            "pay_terms": "Unless actual salary is indicated, pay depends on location.",
            "pay_eligibility": "This role is eligible for a bonus.",
            "benefit_terms": "All benefits are subject to plan terms.",
            "eeo_policy": "Unum Group is committed to providing reasonable accommodations for any applicant.",
            "application_legal": "Applicants must pass a background check.",
            "catch_all": "May perform other duties as assigned.",
            "heading_label": "Location: Boston.",
            "generic_fragment": "or",
            "employer_pay_fragment": "Unum",
            "pay_literal": "$100000",
            "location_literal": "TX",
            "hiring_guidance": "To support you, guidance is available during our hiring process.",
            "empty_fragment": "...",
            "company_culture": "Our employees work in the office four days per week.",
            "pay_update": "Should the location change during the hiring process, the base pay range may change.",
            "hiring_status": "The position will be posted until a candidate is selected.",
            "logistics_label": "The requirements listed below are representative of the role.",
            "education_residual": "Or equivalent education and training.",
            "experience_residual": "Strong experience (",
            "office_logistics": "Learn more about life at Unum.",
        }
        self.assertEqual(set(positives), {r["id"] for r in self.rules.rows})
        for rule in self.rules.rows:
            pattern = rule["pattern"].replace("{employer}", "Unum")
            import re
            with self.subTest(rule=rule["id"]):
                self.assertIsNotNone(re.search(pattern, positives[rule["id"]], re.I))
                self.assertIsNone(re.search(pattern, "Interpret and explain HR policies.", re.I))
                self.assertIsNotNone(self.rules.rejection(positives[rule["id"]], "Unum", " Pay Transparency Statement"))
        for duty in ("Interpret and explain HR policies.", "Manage benefits enrollment and explain health insurance plans.",
                     "Investigate employee complaints under EEO and accommodation policies.",
                     "Perform background checks and maintain compliant records.",
                     "Analyze compensation ranges and recommend salary bands.",
                     "Plan office locations and workforce requirements.", "At Unum, interpret HR policies.",
                     "Coordinate visa documentation for staff."):
            with self.subTest(duty=duty):
                self.assertIsNone(self.rules.rejection(duty, "Unum"))

    def test_unum_shapes_and_catchalls_drop_but_keep_exact_duty(self):
        cases = ["Company: Unum At Unum Group, we're a benefits provider.",
                 "Our 10,000+ employees work across operations in several countries.",
                 "Through our Unum brands, our company offers insurance.",
                 "Colonial Life is headquartered in a city.", "All benefits are subject to the terms of individual plans.",
                 "May perform other duties as assigned."]
        for quote in cases:
            text, p = posting(quote)
            self.rules.process(p, text)
            self.assertEqual(p["expectations"], [])
            self.assertTrue(p["skill_filter"]["emptied"])
            self.assertEqual(p["skill_filter"]["candidates_before"], 1)
            self.assertEqual(len(p["skill_filter"]["drops"]), 1)
            d = p["skill_filter"]["drops"][0]
            self.assertEqual(text[d["start"]:d["end"]], quote)
        text, p = posting("Interpret and explain HR policies.", section="responsibilities")
        before = deepcopy(p["expectations"])
        self.rules.process(p, text)
        self.assertEqual(p["expectations"], before)
        self.assertFalse(p["skill_filter"]["emptied"])

    def test_compound_education_years_domains_are_separate_exact_spans(self):
        quote = ("High School Diploma, GED, or equivalent certification At least 7 years of client facing experience OR "
                 "at least 7 years of consulting experience OR a combination At least 7 years of experience in Human Resources")
        text, p = posting(quote)
        self.rules.process(p, text)
        self.assertEqual(p["min_years"], 7)
        self.assertEqual(p["education"], ["high_school_or_equivalent"])
        self.assertEqual([e["source_wording"] for e in p["expectations"]],
                         ["client facing experience", "consulting experience", "Human Resources"])
        self.assertEqual(p["skill_filter"]["drops_by_reason"], {"education": 1, "years": 3})
        self.assertEqual(p["native_extraction_method"], "flat-headings/1")
        self.assertEqual(p["extraction_method"], "rules/3")
        for e in p["expectations"]:
            self.assertEqual(text[e["start"]:e["end"]], e["source_wording"])
            self.assertEqual(e["source_sha256"], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(e["parent_expectation_id"], "parent")
        text, preferred = posting("At least 9 years of HR experience", section="preferred")
        self.rules.process(preferred, text)
        self.assertIsNone(preferred["min_years"])
        self.assertTrue(all(e["section"] == "preferred" for e in preferred["expectations"]))
        text, software = posting("Experience with MS Excel and MS Office.")
        self.rules.process(software, text)
        self.assertEqual(software["education"], [])
        self.assertEqual(software["expectations"][0]["source_wording"], "Experience with MS Excel and MS Office.")

    def test_employer_weighted_ranking_not_posting_count_and_status_unchanged(self):
        rows = [{"employer": "Large", "dedup_key": str(i)} for i in range(100)] + [
            {"employer": "Small", "dedup_key": "small"}]
        self.assertEqual(employer_weighted_share(rows, {str(i) for i in range(100)}), .5)
        self.assertEqual(employer_weighted_share(rows, {"small"}), .5)
        self.assertAlmostEqual(employer_weighted_share(rows, {"0", "small"}), .505)
        self.assertEqual(employer_weighted_share(rows, set()), 0)
        # Ranking uses the pair (distinct employers, weighted share), not raw volume.
        self.assertGreater((2, .505), (1, .5))
        windows = [Measurement(q, .30, 10, .1, "same", [1], "coincident", .01) for q in ("2026-Q3", "2026-Q4")]
        self.assertEqual(derive_status(windows, []), "core")
        windows[-1].employer_weighted_share = float("nan")
        with self.assertRaises(RoleValidationError):
            derive_status(windows, [])

    def test_sample_descriptor_only_uses_verified_distinct_employers(self):
        rows = [{"employer": "A", "dedup_key": str(i)} for i in range(100)] + [{"employer": "B", "dedup_key": "b"}]
        sectors = {"51": "Information", "52": "Finance"}
        employers = {"a": {"status": "verified", "naics": "51"}, "b": {"status": "unverified", "naics": None}}
        note = sector_sample_note(rows, employers, sectors)
        self.assertIn("1/2 employers verified", note)
        self.assertIn("remaining sectors unverified", note)
        self.assertNotIn("mostly", note)
        employers["a"].update(status="unverified", naics=None)
        self.assertEqual(sector_sample_note(rows, employers, sectors), "Sample sectors: sector mix unverified")

    def test_fresh_rules3_import_never_loads_providers_or_extra_http(self):
        code = """import builtins, socket
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith((\'openai\', \'anthropic\', \'httpx\', \'requests\', \'skills_vector.market.agent\', \'skills_vector.market.remote\')):
        raise AssertionError(\'forbidden dependency\')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError(\'network\'))
from skills_vector.market.skill_rules import SkillRules
assert SkillRules().rejection(\'Interpret and explain HR policies.\', \'Example\') is None
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_table_version_and_source_span_rejections(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "table.json"
            value = read_json(TABLE)
            value["method"] = "unknown"
            import json
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                SkillRules(path)
        text, p = posting("Interpret and explain HR policies.")
        p["expectations"][0]["start"] = 0
        with self.assertRaises(ValueError):
            self.rules.process(p, text)
        text, p = posting("Other duties as assigned.")
        p["expectations"] = []
        self.rules.process(p, text)
        self.assertTrue(p["skill_filter"]["empty_before"])
        self.assertFalse(p["skill_filter"]["emptied"])


if __name__ == "__main__":
    unittest.main()
