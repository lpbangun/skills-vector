"""M6 no-model lane, native board samples and quarter-to-quarter evidence gates."""
from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skills_vector.cli import main as cli
from skills_vector.market.limits import POSTING_VOLUME_LIMITS, RESEARCH_LIMITS
from skills_vector.market.posting_volume import (VolumePolicy, capture, extract_sections, fair_sample,
                                                normalize_phrase, write_snapshot)
from skills_vector.market.role_artifacts import canonical, digest, read_json
from skills_vector.market.role_atlas import RoleRecord, SOURCE_RULES
from skills_vector.market.role_method import method_policy
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.role_reads import RoleAtlasReads

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/samples/posting-volume"
PUBLISHED = ROOT / "preview/release"
STAMP = "2026-10-15T00:00:00Z"


def stress_policy(root: Path, per_employer: int = 30) -> VolumePolicy:
    """Ten employers, native templates expanded to 300 postings/role; all explicitly fixture."""
    original = read_json(SAMPLES / "policy.sample.json")
    boards = []
    for index in range(10):
        template = original["boards"][index % 6]
        ats, token = template["ats"], f"sample-stress-{index}"
        native = read_json(SAMPLES / template["payload"])
        templates = native if ats == "lever" else native["jobs"]
        jobs = []
        for slug in original["roles"]:
            title = original["roles"][slug][0]
            example = next(j for j in templates if (j["text"] if ats == "lever" else j["title"]).endswith(title))
            for number in range(per_employer):
                job = copy.deepcopy(example)
                jid = str(1000000 + index * 10000 + list(original["roles"]).index(slug) * 1000 + number) if ats == "greenhouse" else f"{token}-{slug}-{number}"
                job["id"] = int(jid) if ats == "greenhouse" else jid
                field, url = (("absolute_url", f"https://boards.greenhouse.io/{token}/jobs/{jid}") if ats == "greenhouse" else
                              ("hostedUrl", f"https://jobs.lever.co/{token}/{jid}") if ats == "lever" else
                              ("jobUrl", f"https://jobs.ashbyhq.com/{token}/{jid}"))
                job[field] = url
                jobs.append(job)
        name = token + ".sample.json"
        (root / name).write_text(canonical(jobs if ats == "lever" else {"jobs": jobs}))
        boards.append({**template, "employer": f"Sample stress employer {index}", "token": token, "payload": name})
    original["boards"] = boards
    path = root / "policy.sample.json"; path.write_text(canonical(original))
    return VolumePolicy.read(path)


class SectionAndPolicyTests(unittest.TestCase):
    def test_headings_lists_plaintext_and_verbatim_offsets(self):
        source = '<h2>About us</h2><p>Company advertising.</p><h2>Responsibilities</h2><ul><li>Own <strong>end-to-end</strong> delivery of projects &amp; systems.</li><li>Prepare reports.</li></ul><h2>Requirements</h2><li>Experience with SQL.</li><h2>Nice to have</h2><li>Python is a plus.</li><h2>Benefits</h2><li>Paid time off.</li><h2>Legal</h2><p>Equal opportunity employer.</p>'
        result = extract_sections(source)
        self.assertTrue(result["has_responsibilities"])
        self.assertEqual(len(result["expectations"]), 4)
        self.assertEqual(result["expectations"][0]["quote"], "Own end-to-end delivery of projects & systems.")
        for expected in result["expectations"]:
            self.assertEqual(result["description"][expected["start"]:expected["end"]], expected["quote"])
            self.assertEqual(expected["source_sha256"], result["source_sha256"])
            self.assertNotIn("Company advertising", expected["quote"])
        nested = extract_sections('<h2>Responsibilities</h2><ul><li><p>Own the<br>architecture.</p><p>Deliver the design.</p></li></ul>')
        self.assertEqual(len(nested["expectations"]), 1)
        item = nested["expectations"][0]
        self.assertIn("Deliver the design.", item["quote"])
        self.assertEqual(nested["description"][item["start"]:item["end"]], item["quote"])
        plain = extract_sections("Responsibilities:\n- Independently handle client work.\nBenefits:\n- Paid time off.", is_html=False)
        self.assertEqual(plain["sections"]["responsibilities"], ["Independently handle client work."])
        missing = extract_sections("<p>Own end-to-end delivery.</p><h2>Benefits</h2><li>Paid time off.</li>")
        self.assertFalse(missing["has_responsibilities"])
        self.assertEqual(missing["expectations"], [])
        legal = extract_sections("<h2>Responsibilities</h2><li>All qualified applicants receive consideration.</li>")
        self.assertEqual(legal["expectations"], [])

    def test_normalization_labels_not_quotes_no_negation_or_semantic_matching(self):
        for source, expected in (("Experience with SQL is a plus.", "sql"), ("Ability to communicate.", "communicate"),
                                 ("EXPERIENCE IN Python.", "python"), ("Knowledge of HRIS is preferred", "hris")):
            self.assertEqual(normalize_phrase(source), expected)
        self.assertNotEqual(normalize_phrase("No experience with SQL."), "sql")
        self.assertNotEqual(normalize_phrase("Experience with NoSQL."), normalize_phrase("Experience with SQL."))
        self.assertNotEqual(normalize_phrase("Forecasting accuracy"), normalize_phrase("Record keeping"))
        from skills_vector.market.role_pipeline import triage
        quote = "Own the\r\narchitecture for this service."
        result = triage({}, [quote])
        self.assertEqual(result.level, 4)
        self.assertEqual(result.strong[0]["quote"], quote)
        self.assertIsNone(triage({"title": "Principal"}, []).level)

    def test_fair_cap_sparse_skewed_and_hard_limit(self):
        rows = [{"dedup_key": str(i), "employer": "Dominant" if i < 100 else f"Other {i}"} for i in range(110)]
        selected = fair_sample(rows, 100)
        self.assertTrue(selected)
        self.assertLessEqual(max(Counter(p["employer"] for p in selected).values()) / len(selected), .2)
        self.assertEqual(fair_sample([{"dedup_key": str(i), "employer": str(i % 4)} for i in range(100)], 500), [])
        for invalid in (0, 501, True):
            with self.assertRaises(ValueError): fair_sample(rows, invalid)
        with tempfile.TemporaryDirectory() as d:
            value = read_json(SAMPLES / "policy.sample.json")
            for changes in ({"max_postings_per_role": 501}, {"window_length": "monthly"}, {"data_kind": "unknown"}):
                bad = {**value, **changes}; path = Path(d) / "p.json"; path.write_text(canonical(bad))
                with self.assertRaises(ValueError): VolumePolicy.read(path)

    def test_bad_hosts_tokens_identity_missing_note_and_duplicate_board(self):
        value = read_json(SAMPLES / "policy.sample.json")
        for mutate in (lambda b: b.update(ats="evil"), lambda b: b.update(token="../escape"),
                       lambda b: b.update(source_note=""), lambda b: b.update(employer="")):
            bad = copy.deepcopy(value); mutate(bad["boards"][0])
            with self.assertRaises(ValueError): VolumePolicy.from_value(bad, Path("/tmp/policy.json"))
        bad = copy.deepcopy(value); bad["boards"].append(bad["boards"][0])
        with self.assertRaises(ValueError): VolumePolicy.from_value(bad, Path("/tmp/policy.json"))

    def test_canonical_method_all_rules_and_no_review_scaffolding(self):
        method = method_policy()
        self.assertEqual(method["window_length"], "quarterly")
        self.assertEqual(method["forecast_horizon_quarters"], 4)
        self.assertEqual({s["source_kind"]: (s["timing"], s["family"]) for s in method["sources"]}, SOURCE_RULES)
        self.assertTrue(all(s["good_for"].strip() and "reading" not in s for s in method["sources"]))
        html = (ROOT / "preview/role-atlas.html").read_text()
        self.assertNotIn('id="h-q"', html)
        self.assertNotIn("Still open", html)
        reads = RoleAtlasReads.configured(fixtures=True)
        one = reads.role("account-executive"); two = reads.role("hr-generalist")
        self.assertEqual(one["method"], two["method"])
        one["method"]["sources"].clear()
        self.assertTrue(reads.role("account-executive")["method"]["sources"])
        source = (ROOT / "preview/role-atlas.js").read_text()
        helpers = source[source.index("  function noun"):source.index("  function percentage")]
        probe = subprocess.run(["node", "-e", helpers + '''
console.log(JSON.stringify([noun(1,"employer"), noun(2,"employer"), noun(1,"posting"),
signalReading({source_kind:"postings",reading:"1 employers; 11 employers"}),
signalReading({source_kind:"onet_ratings",reading:"1 employers"})]));
'''], capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(json.loads(probe.stdout), ["employer", "employers", "posting", "1 employer; 11 employers", "1 employers"])

    def test_no_provider_import_even_through_real_cli_entrypoint(self):
        script = """
import builtins, sys
from pathlib import Path
old = builtins.__import__
banned = ('skills_vector.interpret','skills_vector.pipeline','skills_vector.budget','skills_vector.market.agent','skills_vector.market.pipeline','skills_vector.market.budgeting','skills_vector.market.research_config','openai','deepinfra')
def guarded(name, *args, **kwargs):
    if name.startswith(banned): raise AssertionError('model/provider import: '+name)
    return old(name,*args,**kwargs)
builtins.__import__=guarded
import socket
def no_network(*args, **kwargs): raise AssertionError("network access in offline lane")
socket.create_connection = no_network
socket.getaddrinfo = no_network
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
from skills_vector.cli import main
assert main(['market','posting-volume','capture','--policy',sys.argv[1],'--quarter','2026-Q4','--payload-root',sys.argv[2],'--retrieved-at','2026-10-15T00:00:00Z','--output',sys.argv[3]+'/captures']) == 0
assert main(['market','posting-volume','project','--snapshot',sys.argv[3]+'/captures/2026-Q4','--output',sys.argv[3]+'/roles']) == 0
assert main(['market','posting-volume','roster-probe','--candidates',sys.argv[1],'--payload-root',sys.argv[2],'--output',sys.argv[3]+'/roster.json']) == 0
assert not any(n.startswith(banned) for n in sys.modules)
try: guarded('skills_vector.market.agent')
except AssertionError: pass
else: raise AssertionError('import guard failed')
"""
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, "-c", script, str(SAMPLES / "policy.sample.json"), str(SAMPLES), d], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RESEARCH_LIMITS["max_model_calls"], 12)
        self.assertEqual(RESEARCH_LIMITS["max_postings_per_role"], 60)
        self.assertEqual(POSTING_VOLUME_LIMITS["max_postings_per_role"], 500)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.policy = VolumePolicy.read(SAMPLES / "policy.sample.json")
        for q, stamp in (("2026-Q3", "2026-09-01T00:00:00Z"), ("2026-Q4", STAMP)):
            write_snapshot(cls.policy, cls.root / "snapshots", q, payload_root=SAMPLES, retrieved_at=stamp)
        cls.snapshots = [cls.root / "snapshots/2026-Q3", cls.root / "snapshots/2026-Q4"]

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_sample_coverage_failures_quotes_and_determinism(self):
        manifest, files = capture(self.policy, "2026-Q4", payload_root=SAMPLES, retrieved_at=STAMP)
        again, second = capture(self.policy, "2026-Q4", payload_root=SAMPLES, retrieved_at=STAMP)
        self.assertEqual(canonical(manifest), canonical(again)); self.assertEqual(files, second)
        reports = manifest["roles"]
        self.assertEqual(sum(r["candidates"] for r in reports.values()), 48)
        self.assertEqual(sum(r["responsibilities"] for r in reports.values()), 42)
        self.assertEqual(sum(len(r["responsibility_failures"]) for r in reports.values()), 6)
        self.assertGreaterEqual(42 / 48, .8)
        for posting in json.loads(files["postings.json"]):
            self.assertEqual(posting["extraction_method"], "rules/2")
            description = files[posting["description_path"]].decode()
            for expected in posting["expectations"]:
                self.assertEqual(description[expected["start"]:expected["end"]], expected["source_wording"])
                self.assertEqual(expected["source_sha256"], posting["source_sha256"])
                self.assertNotIn(expected["section"], ("benefits", "company", "legal"))

    def test_two_quarters_projection_traceability_scope_and_no_fake_scores(self):
        inputs = EvidenceInputs.from_snapshots(self.snapshots, PUBLISHED)
        records, audit = inputs.build()
        self.assertEqual(len(records), 4)
        # The old AE fixture only names generic selling/project duties, not a reviewed segment.
        self.assertEqual({k: r["postings_per_level"]["unassigned"] for k, r in audit["roles"].items()},
                         {"account-executive": 12, "forward-deployed-engineer": 2, "growth-manager": 1, "hr-generalist": 2})
        for record in records:
            RoleRecord.from_dict(record.to_dict())
            self.assertEqual(record.data_kind, "fixture")
            self.assertEqual(record.demand_window.compared_to, "2026-Q3")
            self.assertTrue(all(i.profile is None for i in record.industries))
            for skill in record.skill_map.skills:
                self.assertEqual([w.window for w in skill.windows], ["2026-Q3", "2026-Q4"])
                self.assertEqual(skill.windows[0].comparison_group, skill.windows[1].comparison_group)
                self.assertIsNone(skill.forecast.probability)
            links = [link for p in audit["roles"][record.id.removeprefix("role.")]["postings"] for link in p["skill_links"]]
            self.assertTrue(any(link["phrase"] != link["normalized_phrase"] for link in links))
        hr = audit["roles"]["hr-generalist"]["postings"]
        self.assertTrue(any(p["reason"] == "conflicting_strong_scope_signals" for p in hr))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "a"; other = Path(d) / "b"
            inputs.write(out); inputs.write(other)
            self.assertEqual({str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()},
                             {str(p.relative_to(other)): p.read_bytes() for p in other.rglob("*") if p.is_file()})

    def test_tampering_dates_duplicate_quarter_and_protected_output_rejected(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); changed = root / "changed"
            shutil.copytree(self.snapshots[0], changed)
            row = read_json(changed / "postings.json")[0]
            with (changed / row["description_path"]).open("a") as handle: handle.write("forged")
            with self.assertRaisesRegex(ValueError, "hash"):
                EvidenceInputs.from_snapshots([changed], PUBLISHED)
            with self.assertRaisesRegex(ValueError, "duplicate quarterly"):
                EvidenceInputs.from_snapshots([self.snapshots[0], self.snapshots[0]], PUBLISHED)
            for stamp in ("2026-10-01", "2026-09-01T00:00:00Z"):
                with self.assertRaises(ValueError):
                    capture(self.policy, "2026-Q4", payload_root=SAMPLES, retrieved_at=stamp)
            with self.assertRaises(ValueError):
                write_snapshot(self.policy, ROOT / "preview/release", "2026-Q4", payload_root=SAMPLES, retrieved_at=STAMP)
            inputs = EvidenceInputs.from_snapshots(self.snapshots, PUBLISHED)
            with self.assertRaises(ValueError): inputs.write(self.snapshots[0] / "output")

    def test_us_rule_dedup_and_requirements_do_not_assign_scope(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); shutil.copytree(SAMPLES, root / "boards")
            path = root / "boards/greenhouse-sample-employer-1.sample.json"
            body = read_json(path)
            body["jobs"].append(body["jobs"][0])
            body["jobs"][1]["location"]["name"] = "London, UK"
            body["jobs"][0]["content"] = "&lt;h2&gt;Requirements&lt;/h2&gt;&lt;li&gt;Ability to own the architecture.&lt;/li&gt;"
            path.write_text(canonical(body))
            manifest, _ = capture(VolumePolicy.read(root / "boards/policy.sample.json"), "2026-Q4", payload_root=root / "boards", retrieved_at=STAMP)
            self.assertTrue(any(e["reason"] == "duplicate" for e in manifest["exclusions"]))
            self.assertTrue(any(e["reason"] == "not_explicit_US" for e in manifest["exclusions"]))
            write_snapshot(VolumePolicy.read(root / "boards/policy.sample.json"), root / "out", "2026-Q4", payload_root=root / "boards", retrieved_at=STAMP)
            _, report = EvidenceInputs.from_snapshots([root / "out/2026-Q4"], PUBLISHED).build()
            posting = next(p for p in report["roles"]["account-executive"]["postings"] if p["employer"] == "Sample employer 1")
            self.assertIsNone(posting["level"])

    def test_300_per_role_employer_cap_bytes_and_core_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); payload_root = root / "boards"; payload_root.mkdir()
            policy = stress_policy(payload_root)
            manifest, files = capture(policy, "2026-Q4", payload_root=payload_root, retrieved_at=STAMP)
            other, second = capture(policy, "2026-Q4", payload_root=payload_root, retrieved_at=STAMP)
            self.assertEqual(canonical(manifest), canonical(other)); self.assertEqual(files, second)
            for role, report in manifest["roles"].items():
                self.assertEqual(report["admitted"], 300, role)
                self.assertEqual(sorted(report["employers"].values()), [30] * 10)
            for q, stamp in (("2026-Q3", "2026-09-01T00:00:00Z"), ("2026-Q4", STAMP)):
                write_snapshot(policy, root / "captured", q, payload_root=payload_root, retrieved_at=stamp)
            records, _ = EvidenceInputs.from_snapshots([root / "captured/2026-Q3", root / "captured/2026-Q4"], PUBLISHED).build()
            for record in records:
                self.assertEqual(record.demand_window.postings, 300)
                self.assertTrue(any(s.status == "core" for s in record.skill_map.skills))
                self.assertFalse(any(s.status == "rising" for s in record.skill_map.skills))
                self.assertTrue(all(i.profile is None for i in record.industries))
            self.assertEqual(len(json.loads(files["postings.json"])), 1200)
            write_snapshot(policy, root / "captured-copy", "2026-Q4", payload_root=payload_root, retrieved_at=STAMP)
            a, b = root / "captured/2026-Q4", root / "captured-copy/2026-Q4"
            self.assertEqual({str(p.relative_to(a)): p.read_bytes() for p in a.rglob("*") if p.is_file()},
                             {str(p.relative_to(b)): p.read_bytes() for p in b.rglob("*") if p.is_file()})

    def test_changed_sampling_policy_does_not_create_comparable_history_and_empty_pool_is_honest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            policy = copy.deepcopy(self.policy.value); policy["max_postings_per_role"] = 10
            changed = VolumePolicy.from_value(policy, self.policy.path)
            write_snapshot(changed, root / "changed", "2026-Q4", payload_root=SAMPLES, retrieved_at=STAMP)
            records, report = EvidenceInputs.from_snapshots([self.snapshots[0], root / "changed/2026-Q4"], PUBLISHED).build()
            for record in records:
                self.assertIsNone(record.demand_window.compared_to)
                self.assertTrue(all(s.status == "early" for s in record.skill_map.skills))
            self.assertTrue(all(r["other_window_postings_not_compared"] == 12 for r in report["roles"].values()))
            policy["boards"] = policy["boards"][:4]
            empty = VolumePolicy.from_value(policy, self.policy.path)
            write_snapshot(empty, root / "empty", "2026-Q4", payload_root=SAMPLES, retrieved_at=STAMP)
            records, _ = EvidenceInputs.from_snapshots([root / "empty/2026-Q4"], PUBLISHED).build()
            self.assertTrue(all(r.demand_window.postings == 0 and r.skill_map.skills == [] for r in records))

    def test_rehashed_forged_quote_is_rejected_by_native_replay(self):
        import shutil, hashlib
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "snapshot"; shutil.copytree(self.snapshots[0], root)
            postings = read_json(root / "postings.json")
            postings[0]["expectations"][0]["source_wording"] = "A made-up expectation, not source text."
            (root / "postings.json").write_text(canonical(postings))
            manifest = read_json(root / "manifest.json")
            manifest["files"]["postings.json"] = hashlib.sha256((root / "postings.json").read_bytes()).hexdigest()
            manifest["snapshot_id"] = digest({k: v for k, v in manifest.items() if k != "snapshot_id"})
            (root / "manifest.json").write_text(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "exact rules"):
                EvidenceInputs.from_snapshots([root], PUBLISHED)

    def test_research_data_kind_and_verified_sector_map_require_actual_local_evidence(self):
        # Exercise the research-shaped branch in memory only, never promote samples.
        inputs = EvidenceInputs.from_snapshots(self.snapshots, PUBLISHED)
        inputs.data_kind = "research"
        records, _ = inputs.build()
        self.assertTrue(all(r.data_kind == "research" and RoleRecord.from_dict(r.to_dict()) for r in records))
        with tempfile.TemporaryDirectory() as d:
            import shutil
            root = Path(d)
            shutil.copytree(SAMPLES, root / "boards")
            for board_file in (root / "boards").glob("*.sample.json"):
                if board_file.name == "policy.sample.json": continue
                board_file.write_text(board_file.read_text().replace("No verified primary employer sector.", "Primary employer sector is NAICS 51 (authored sample)."))
            write_snapshot(VolumePolicy.read(root / "boards/policy.sample.json"), root / "captured", "2026-Q4", payload_root=root / "boards", retrieved_at=STAMP)
            snapshots = [root / "captured/2026-Q4"]
            inputs = EvidenceInputs.from_snapshots(snapshots, PUBLISHED)
            path = root / "employers.json"
            posting = inputs.postings[0]
            quote = "Primary employer sector is NAICS 51 (authored sample)."
            row = {"employer": posting["employer"], "status": "verified", "naics": "51", "source_id": posting["source_id"],
                   "quote": quote, "source_note": "Synthetic test of operator-declared primary-sector evidence, not a real industry finding.",
                   "basis": "primary_employer_sector"}
            path.write_text(canonical([row]))
            verified = EvidenceInputs.from_snapshots(snapshots, PUBLISHED, employer_map=path)
            self.assertEqual(verified.employers[posting["employer"].casefold()]["naics"], "51")
            for field, value in (("quote", "Not in the source"), ("basis", "customer_industry"), ("source_note", "")):
                bad = {**row, field: value}; path.write_text(canonical([bad]))
                with self.assertRaises(ValueError):
                    EvidenceInputs.from_snapshots(snapshots, PUBLISHED, employer_map=path)

    def test_live_path_only_with_injected_transport_and_no_real_network(self):
        from skills_vector.market.fetch import FetchResult
        from skills_vector.market.posting_volume import BOARDS
        policy = copy.deepcopy(self.policy.value); policy["data_kind"] = "research"
        policy = VolumePolicy.from_value(policy, self.policy.path)
        bodies = {BOARDS[b["ats"]].format(token=b["token"]): (SAMPLES / b["payload"]).read_bytes() for b in policy.value["boards"]}
        def local_fetch(url, **kwargs):
            return FetchResult(url=url, status=200, body=bodies[url], retrieved_at=STAMP)
        with patch("skills_vector.market.posting_volume.fetch_url", side_effect=local_fetch) as mocked, patch("socket.socket", side_effect=AssertionError("live board access forbidden")):
            manifest, _ = capture(policy, "2026-Q4", live=True)
            self.assertEqual(mocked.call_count, 6)
            self.assertEqual(sum(r["admitted"] for r in manifest["roles"].values()), 48)
            with self.assertRaises(ValueError):
                capture(policy, "2026-Q3", live=True)
        with self.assertRaisesRegex(ValueError, "sample payload"):
            capture(policy, "2026-Q4", payload_root=SAMPLES, retrieved_at=STAMP)

    def test_cli_and_offline_no_network_and_live_requires_non_sample_policy(self):
        with tempfile.TemporaryDirectory() as d, patch("socket.socket", side_effect=AssertionError("network forbidden")), redirect_stdout(io.StringIO()):
            self.assertEqual(cli(["market", "posting-volume", "capture", "--policy", str(SAMPLES / "policy.sample.json"),
                                  "--quarter", "2026-Q4", "--payload-root", str(SAMPLES), "--retrieved-at", STAMP, "--output", d]), 0)
            self.assertEqual(cli(["market", "posting-volume", "project", "--snapshot", d + "/2026-Q4", "--output", d + "/project"]), 0)
            with self.assertRaises(SystemExit):
                cli(["market", "posting-volume", "capture", "--policy", str(SAMPLES / "policy.sample.json"), "--quarter", "2026-Q4", "--live", "--output", d])


if __name__ == "__main__":
    unittest.main()
