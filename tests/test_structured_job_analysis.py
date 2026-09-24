from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skills_vector.structured_job_analysis import (
    DEFAULT_BASE_CORPUS_DIR,
    DEFAULT_BENCHMARK_DIR,
    EXPECTED_BENCHMARK_MANIFEST_SHA256,
    MAX_FINAL_SPEND_USD,
    PINNED_MODEL_ID,
    SNAPSHOT_IDS,
    StructuredAnalysisError,
    _acquire_final_lock,
    build_release,
    load_frozen_corpus,
    preflight_request_batch,
    run_pipeline,
    validate_citations,
    validate_resource_config,
)


RESOURCE_CONFIG = {
    "model": PINNED_MODEL_ID,
    "provider": "deepinfra",
    "sampling": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048},
    "caps": {
        "max_inference_requests": 2,
        "max_inference_cost_usd": MAX_FINAL_SPEND_USD,
        "max_retrieval_requests": 0,
        "max_wall_minutes": 15,
    },
}


class StructuredJobAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_frozen_corpus(DEFAULT_BENCHMARK_DIR, DEFAULT_BASE_CORPUS_DIR)

    def test_real_frozen_dev_corpus_builds_anchored_output_with_honest_boundaries(self) -> None:
        release = build_release(self.corpus, "a" * 40)
        validate_citations(release, self.corpus)
        self.assertEqual(release["schema_version"], "skills-vector.poc-output.v1")
        self.assertEqual(release["run"]["corpus_snapshot_ids"], list(SNAPSHOT_IDS))
        self.assertEqual(release["run"]["round"], 1)
        self.assertEqual(release["demand_layer"]["claims_allowed"], False)
        self.assertEqual(release["demand_layer"]["denominators"]["employers_scanned"], 23)
        self.assertEqual(release["demand_layer"]["denominators"]["board_total_openings"], 5129)
        self.assertEqual(release["demand_layer"]["denominators"]["admitted_postings"], 14)
        self.assertEqual(release["demand_layer"]["denominators"]["dev_unique_postings"], 7)
        self.assertEqual(release["demand_layer"]["denominators"]["dev_employers"], 4)
        self.assertIn("INSUFFICIENT", release["demand_layer"]["denominators"]["current_status_under_policy"])

        v1_ids = {source["id"] for source in self.corpus.base_manifest["sources"]}
        units = release["work_units"]
        self.assertTrue(units)
        for unit in units:
            self.assertTrue(set(unit["method_fields"]["onet_anchor_ids"]) <= v1_ids)
            self.assertFalse(unit["method_fields"]["practitioner_validated"])
            self.assertEqual(unit["method_fields"]["proficiency"]["source"], "desk-research")
            self.assertNotIn("level", unit["method_fields"]["proficiency"])
        core_tasks = [u for u in units if u["kind"] == "task" and u["method_fields"].get("classification") == "common_core"]
        context_tasks = [u for u in units if u["kind"] == "task" and u["method_fields"].get("context_adaptation")]
        competencies = [u for u in units if u["kind"] == "competency"]
        self.assertEqual(len(core_tasks), 6)
        self.assertEqual(len(context_tasks), 4)
        self.assertEqual(len(competencies), 9)
        all_tasks = [unit for unit in units if unit["kind"] == "task"]
        self.assertTrue(all(task["method_fields"]["task_competency_links"] for task in all_tasks))
        self.assertTrue(all(link["evidence"] and link["direction"] == "task-to-required-competency" for task in all_tasks for link in task["method_fields"]["task_competency_links"]))
        self.assertEqual(len(release["coverage_audit"]["duty_area_coverage"]), 6)
        self.assertEqual(set(release["coverage_audit"]["context_addition_units"]), {task["unit_id"] for task in context_tasks})
        self.assertTrue(any("literal title" in note for note in release["limitations"]))
        self.assertTrue(any("recruiting-only scope" in note.casefold() for note in release["negative_findings"]))
        self.assertIn("People Operations", release["role"]["title_variant_validation"]["variant"])
        self.assertEqual(release["role"]["title_variant_validation"]["literal_title_posting_ids"], [])
        self.assertEqual(release["coverage_audit"]["excluded_role_scan"]["director_or_executive_titles_in_dev"], [])
        self.assertEqual(release["coverage_audit"]["excluded_role_scan"]["recruiting_only_work_units"], [])
        self.assertIn("absent", release["role"]["title_variant_validation"]["status"])
        self.assertEqual(release["provenance"][0]["source_id"], "onet_hr_specialist")
        self.assertEqual(len(release["provenance"]), 13)
        self.assertEqual(self.corpus.benchmark_manifest_sha256, EXPECTED_BENCHMARK_MANIFEST_SHA256)

    def test_demand_counts_have_only_dev_task_denominators_and_traceable_hits(self) -> None:
        release = build_release(self.corpus, "b" * 40)
        task_ids = {u["unit_id"] for u in release["work_units"] if u["kind"] == "task"}
        for row in release["demand_layer"]["coverage_by_unit"]:
            self.assertIn(row["unit_id"], task_ids)
            self.assertEqual(row["dev_posting_denominator"], 7)
            self.assertEqual(row["dev_employer_denominator"], 4)
            self.assertEqual(row["matched_dev_postings"], len(row["matches"]))
            for match in row["matches"]:
                self.assertIn(match["quote"], self.corpus.source_texts[match["source_id"]])
                self.assertEqual(match["evidence_role"], "counts_only_phrase_screen")
        for unit in release["work_units"]:
            if unit["kind"] == "competency":
                self.assertEqual(unit["demand"]["postings_denominator"], 0)
                self.assertIn("No posting coverage", unit["demand"]["notes"])

    def test_offline_pipeline_writes_machine_markdown_html_and_receipt_without_provider(self) -> None:
        revision = "c" * 40
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "artifacts"
            with patch("skills_vector.structured_job_analysis._call_provider_once", side_effect=AssertionError("offline must not call provider")):
                first = run_pipeline(output_dir=output, mode="offline", candidate_revision=revision, command="offline test invocation")
            self.assertEqual(first["status"], "passed_local_build")
            self.assertEqual(first["provider_calls"], 0)
            release_path = output / "release.json"
            guide_path = output / "guide.md"
            html_path = output / "guide.html"
            receipt_path = output / "run-receipt.json"
            self.assertTrue(all(path.is_file() for path in (release_path, guide_path, html_path, receipt_path)))
            release = json.loads(release_path.read_text())
            self.assertEqual(release["run"]["resource_ledger"]["inference_requests"], 0)
            self.assertIn("DACUM-informed desk research", guide_path.read_text())
            html_doc = html_path.read_text()
            self.assertIn("<html lang=\"en\">", html_doc)
            self.assertIn("People Operations", html_doc)
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["commands_run"][0]["exit_code"], 0)
            self.assertEqual(receipt["manifest_sha256"], EXPECTED_BENCHMARK_MANIFEST_SHA256)

    def test_live_resource_pin_and_hard_half_dollar_ceiling(self) -> None:
        validate_resource_config(RESOURCE_CONFIG)
        invalid_model = json.loads(json.dumps(RESOURCE_CONFIG))
        invalid_model["model"] = "other/model"
        with self.assertRaisesRegex(StructuredAnalysisError, "pinned"):
            validate_resource_config(invalid_model)
        invalid_cost = json.loads(json.dumps(RESOURCE_CONFIG))
        invalid_cost["caps"]["max_inference_cost_usd"] = 0.50001
        with self.assertRaisesRegex(StructuredAnalysisError, "USD 0.50"):
            validate_resource_config(invalid_cost)
        invalid_retrieval = json.loads(json.dumps(RESOURCE_CONFIG))
        invalid_retrieval["caps"]["max_retrieval_requests"] = 1
        with self.assertRaisesRegex(StructuredAnalysisError, "retrieval"):
            validate_resource_config(invalid_retrieval)
        credential_config = json.loads(json.dumps(RESOURCE_CONFIG))
        credential_config["api_key"] = "never-store-this"
        with self.assertRaisesRegex(StructuredAnalysisError, "credential fields"):
            validate_resource_config(credential_config)

    def test_preflight_sums_all_planned_requests_before_any_provider_call(self) -> None:
        request = {"model": PINNED_MODEL_ID, "messages": [{"role": "user", "content": "bounded test"}], "max_tokens": 2048}
        per_request, total = preflight_request_batch([request, request], RESOURCE_CONFIG)
        self.assertEqual(len(per_request), 2)
        self.assertAlmostEqual(total, sum(per_request))
        self.assertLessEqual(total, RESOURCE_CONFIG["caps"]["max_inference_cost_usd"])
        too_many = json.loads(json.dumps(RESOURCE_CONFIG))
        too_many["caps"]["max_inference_requests"] = 1
        with self.assertRaisesRegex(StructuredAnalysisError, "request cap"):
            preflight_request_batch([request, request], too_many)
        too_expensive = json.loads(json.dumps(RESOURCE_CONFIG))
        too_expensive["caps"]["max_inference_cost_usd"] = 0.000001
        with self.assertRaisesRegex(StructuredAnalysisError, "whole-run preflight"):
            preflight_request_batch([request, request], too_expensive)

    def test_final_live_mode_requires_freeze_confirmation_config_and_key_before_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            provider = Mock(side_effect=AssertionError("provider must not be called"))
            with self.assertRaisesRegex(StructuredAnalysisError, "freeze-sha"):
                run_pipeline(
                    output_dir=output,
                    mode="live-final",
                    candidate_revision="d" * 40,
                    confirm_final_run=True,
                    resource_config_path=Path(tmp) / "not-used.json",
                    api_key="not-a-real-key",
                    _live_lock_path=Path(tmp) / "one-shot.json",
                    _provider_call=provider,
                )
            provider.assert_not_called()
            self.assertFalse(output.exists())

    def test_mocked_live_final_path_is_single_shot_redacts_credentials_and_never_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "resource.json"
            config_path.write_text(json.dumps(RESOURCE_CONFIG))
            lock_path = root / "one-shot.json"
            output = root / "output"
            calls: list[dict[str, object]] = []
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }

            def fake_provider(body: dict[str, object], key: str) -> dict[str, object]:
                calls.append({"body": body, "key": key})
                return {
                    "choices": [{"message": {"content": json.dumps({"reviews": [{
                        "unit_id": "task-policy-guidance",
                        "stance": "unclear",
                        "reason": "This mock review is a mechanics check only.",
                        "evidence": [citation],
                    }]})}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                }

            receipt = run_pipeline(
                mode="live-final",
                resource_config_path=config_path,
                freeze_sha="e" * 40,
                confirm_final_run=True,
                candidate_revision="e" * 40,
                output_dir=output,
                api_key="unit-test-secret-never-write",
                _live_lock_path=lock_path,
                _provider_call=fake_provider,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["key"], "unit-test-secret-never-write")
            self.assertEqual(receipt["provider_calls"], 1)
            self.assertEqual(receipt["status"], "passed_one_live_review")
            self.assertFalse(receipt["preflight"]["automatic_retry"])
            self.assertLessEqual(receipt["preflight"]["whole_run_estimate_usd"], MAX_FINAL_SPEND_USD)
            serialized = "\n".join(path.read_text() for path in output.iterdir() if path.is_file())
            self.assertNotIn("unit-test-secret-never-write", serialized)
            review = json.loads((output / "live-model-review-untrusted.json").read_text())
            self.assertEqual(review["status"], "untrusted_model_review_not_validation")
            self.assertIn("not practitioner validation", review["notice"])
            lock = json.loads(lock_path.read_text())
            self.assertEqual(lock["status"], "completed")
            with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                _acquire_final_lock(lock_path, {"status": "started"})

    def test_provider_secret_echo_is_discarded_and_receipt_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "resource.json"
            config_path.write_text(json.dumps(RESOURCE_CONFIG))
            secret = "mock-provider-secret-never-persist"
            output = root / "output"
            lock = root / "one-shot.json"
            echoed = {"choices": [{"message": {"content": json.dumps({"reviews": [], "reason": secret})}}]}
            with self.assertRaisesRegex(StructuredAnalysisError, "credential material"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="f" * 40,
                    confirm_final_run=True,
                    candidate_revision="f" * 40,
                    output_dir=output,
                    api_key=secret,
                    _live_lock_path=lock,
                    _provider_call=lambda _body, _key: echoed,
                )
            persisted = "\n".join(path.read_text() for path in output.iterdir() if path.is_file())
            self.assertNotIn(secret, persisted)
            receipt = json.loads((output / "run-receipt.json").read_text())
            self.assertEqual(receipt["status"], "failed_after_one_shot_consumed")
            self.assertEqual(json.loads(lock.read_text())["status"], "failed_consumed_no_retry")
            self.assertFalse((output / "live-model-review-untrusted.json").exists())

    def test_failed_one_shot_consumes_lock_and_redacts_key_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            _acquire_final_lock(path, {"status": "started"})
            with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                _acquire_final_lock(path, {"status": "retry"})
            self.assertEqual(json.loads(path.read_text())["status"], "started")


if __name__ == "__main__":
    unittest.main()
