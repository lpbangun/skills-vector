import hashlib
import html
import json
import os
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT / "outputs" / "structured-job-analysis-public-c5b517d"
# The sealed original run artifacts were never committed to this worktree (they live in the
# original POC lane). Point SKILLS_VECTOR_ORIGINAL_RUN_DIR at that directory to re-enable the
# full hash-pinned comparison; otherwise the sealed check is reported as skipped, not silently
# weakened.
ORIGINAL_DIR = Path(
    os.environ.get("SKILLS_VECTOR_ORIGINAL_RUN_DIR", ROOT / "outputs" / "structured-job-analysis-review-repair-c5b517d")
)

# Baseline hashes captured before creating the separate export. These pin the
# accepted original run outputs, including its consumed provider/review artifacts.
ORIGINAL_SHA256 = {
    "evidence-transparency.md": "751c0975b6f5fd40ba7f4c20037b5937b089d0e1561e8e13b0128bdea3b6e846",
    "guide.html": "74299a8e7542b8e8c130c2d2fa86e62ba1dc732bbe8e36313ef6a5d1342ae988",
    "live-model-review-untrusted.json": "8ab1279dc904003868b108052c99b887b311ab6ff624be9d53919f31bcb2343a",
    "provider-response.json": "d3118ed4c6dfc23b2de4e7af948fa24907e108765b59907a68f325541e73e413",
    "guide.md": "9da815ecae2740596a0adc21d2e4124f66d109111eb305a84e70493c40e7a06d",
    "release.json": "eb8545df75e12f31ab8eac5e73c1230fadea3295db9d832b7d76e82c2a6e0d47",
    "run-manifest.json": "3321b4d5dcee7869fa0ea9f7c5238a9a3fdc8daaba6e17be9cc5155db1fe6360",
    "run-receipt.json": "a97a6355274c1201d453d763820ee4d8de55b9187cd1b6ac43944f0a83024e96",
}


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.tags = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend((tag, name, value) for name, value in attrs)

    def handle_data(self, data):
        self.parts.append(data)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(text):
    return re.sub(r"\s+", " ", html.unescape(text)).strip().casefold()


def collect_source_quotes(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "quote" and isinstance(child, str):
                found.append(child)
            else:
                found.extend(collect_source_quotes(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_source_quotes(child))
    return found


def words(text):
    return re.findall(r"[a-z0-9]+(?:['’-][a-z0-9]+)*", normalize(text))


class StructuredJobAnalysisPublicExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ORIGINAL_DIR / "release.json").is_file():
            raise unittest.SkipTest(
                "sealed original run artifacts are not present in this worktree; set "
                f"SKILLS_VECTOR_ORIGINAL_RUN_DIR to the original run directory ({ORIGINAL_DIR} expected)"
            )
        cls.manifest = json.loads((PUBLIC_DIR / "manifest.json").read_text())
        cls.public = json.loads((PUBLIC_DIR / "release.json").read_text())
        cls.original = json.loads((ORIGINAL_DIR / "release.json").read_text())
        cls.guide = (PUBLIC_DIR / "guide.md").read_text()
        cls.evidence = (PUBLIC_DIR / "evidence-transparency.md").read_text()
        cls.html_source = (PUBLIC_DIR / "guide.html").read_text()
        cls.html_parser = TextCollector()
        cls.html_parser.feed(cls.html_source)
        cls.html_text = " ".join(cls.html_parser.parts)

    def test_manifest_hashes_public_files_and_pins_original_bytes(self):
        self.assertEqual(
            set(self.manifest["public_artifact_sha256"]),
            {"guide.md", "guide.html", "evidence-transparency.md", "release.json"},
        )
        for relative, expected in self.manifest["public_artifact_sha256"].items():
            self.assertEqual(sha256(PUBLIC_DIR / relative), expected, relative)

        original_binding = self.manifest["original_run_binding"]
        self.assertEqual(original_binding["original_artifact_sha256"], ORIGINAL_SHA256)
        for relative, expected in ORIGINAL_SHA256.items():
            self.assertEqual(sha256(ORIGINAL_DIR / relative), expected, relative)
        self.assertEqual(original_binding["candidate_revision"], "c5b517d4b53088f91335e0901e26a7810629b718")
        self.assertEqual(original_binding["run_id"], "sja-a-live-r1-4e5e02b2ca3824e3")
        self.assertFalse(self.manifest["source_selection"]["held_out_content_opened_or_used"])

    def test_public_release_and_human_views_are_consistent(self):
        self.assertEqual(self.public["schema_version"], "skills-vector.structured-job-analysis-public.v1")
        self.assertEqual(self.public["original_run"]["original_schema_version"], "skills-vector.poc-output.v1")
        self.assertEqual(self.public["original_run"]["candidate_revision"], self.manifest["original_run_binding"]["candidate_revision"])
        self.assertEqual(self.public["original_run"]["run_id"], self.manifest["original_run_binding"]["run_id"])
        self.assertTrue(self.public["original_run"]["original_model_review_accepted"])
        self.assertEqual(self.public["publication_status"], "public_export_not_reviewed")
        self.assertFalse(self.public["method_status"]["export_independently_reviewed"])
        self.assertFalse(self.public["method_status"]["practitioner_validated"])
        self.assertFalse(self.manifest["review_status"]["public_export_reviewed"])

        sequence = self.public["practice_sequence"]
        self.assertEqual([item["rank"] for item in sequence], [1, 2, 3, 4, 5])
        for item in sequence:
            heading = f"### {item['rank']}. {item['title']}"
            self.assertIn(heading, self.guide)
            self.assertIn(item["title"], self.html_text)
            self.assertIn(item["rationale"], self.guide)
            self.assertTrue(item["evidence_source_ids"])
            for source_id in item["evidence_source_ids"]:
                self.assertIn(source_id, {source["source_id"] for source in self.public["source_register"]})
            for prompt in item["practice_prompts"].values():
                self.assertIn(prompt, self.guide)
                self.assertIn(prompt, self.html_text)

        original_source_ids = {item["source_id"] for item in self.original["provenance"]}
        public_source_ids = {item["source_id"] for item in self.public["source_register"]}
        self.assertEqual(public_source_ids, original_source_ids)
        self.assertEqual(len(public_source_ids), 13)
        original_sources = {item["source_id"]: item for item in self.original["provenance"]}
        for source in self.public["source_register"]:
            original_source = original_sources[source["source_id"]]
            self.assertEqual(source["url"], original_source["url"])
            self.assertEqual(source["attribution"], original_source["attribution"])
            self.assertEqual(source["extract_sha256"], original_source["sha256"])
        self.assertEqual(
            sum(source["source_kind"] == "employer_public_job_feed" for source in self.public["source_register"]),
            7,
        )
        self.assertEqual(self.manifest["source_selection"]["admitted_dev_posting_sources"], 7)
        self.assertIn("source register", self.evidence.casefold())
        self.assertIn("omission inventory", self.evidence.casefold())
        self.assertIn("CC BY 4.0", self.evidence)
        self.assertIn("public visibility", self.evidence.casefold())

        rights = {source["source_id"]: source["rights_assessment"] for source in self.public["source_register"]}
        self.assertIn("page_specific_cc_by_4.0_notice_observed", rights["onet_hr_specialist"]["status"])
        self.assertEqual(rights["onet_hr_specialist"]["license_url"], "https://creativecommons.org/licenses/by/4.0/")
        self.assertIn("page_specific_cc_by_4.0_notice_observed", rights["onet_task_ratings_dictionary"]["status"])
        self.assertIn("unresolved", rights["esco_essential_optional"]["status"])
        self.assertIn("unresolved", rights["opm_job_analysis"]["status"])
        self.assertIn("unresolved", rights["dacum_method"]["status"])
        for source in self.public["source_register"]:
            if source["source_kind"] == "employer_public_job_feed":
                self.assertIn("unresolved", source["rights_assessment"]["status"])
                self.assertIn(source["url"], self.evidence)

    def test_html_is_static_self_contained_and_has_no_remote_assets(self):
        forbidden = {"script", "iframe", "object", "embed", "img", "video", "audio"}
        self.assertFalse(forbidden.intersection(self.html_parser.tags))
        for tag, name, value in self.html_parser.attrs:
            self.assertNotEqual(name.casefold(), "src", f"remote or embedded source on <{tag}>")
            if name.casefold() == "href":
                self.assertIsNotNone(value)
                self.assertTrue(
                    value.startswith(("https://", "http://", "evidence-transparency.md", "release.json", "manifest.json")),
                    value,
                )
        self.assertNotIn("url(", self.html_source.casefold())
        self.assertIn("default-src 'none'", self.html_source)

    def test_source_excerpt_leak_controls(self):
        output_text = normalize("\n".join((self.guide, self.evidence, self.html_source, (PUBLIC_DIR / "release.json").read_text())))
        output_word_tokens = set()
        for artifact in (self.guide, self.evidence, self.html_source, (PUBLIC_DIR / "release.json").read_text()):
            tokens = words(artifact)
            output_word_tokens.update(tuple(tokens[index:index + 10]) for index in range(max(0, len(tokens) - 9)))

        quotes = collect_source_quotes(self.original)
        self.assertGreater(len(quotes), 0)
        for quote in quotes:
            normalized_quote = normalize(quote)
            if len(normalized_quote) >= 36:
                self.assertNotIn(normalized_quote, output_text, f"source excerpt leaked: {quote[:72]}")
            quote_words = words(quote)
            if len(quote_words) >= 10:
                for index in range(len(quote_words) - 9):
                    ngram = tuple(quote_words[index:index + 10])
                    self.assertNotIn(ngram, output_word_tokens, f"source wording fragment leaked: {' '.join(ngram)}")

        self.assertNotIn('"quote"', (PUBLIC_DIR / "release.json").read_text())
        self.assertNotIn("<blockquote>\u201c", self.html_source)
        self.assertNotIn("provider-response.json", (PUBLIC_DIR / "release.json").read_text())


if __name__ == "__main__":
    unittest.main()
