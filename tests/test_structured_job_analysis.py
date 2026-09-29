from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from skills_vector.cli import build_parser, main
from skills_vector.structured_job_analysis import (
    APPROVED_RESOURCE_CONFIG_PATH,
    APPROVED_RESOURCE_CONFIG_SHA256,
    DEFAULT_BASE_CORPUS_DIR,
    DEFAULT_BENCHMARK_DIR,
    MATCHED_EVIDENCE_POLICY,
    MATCHED_RESOURCE_CONFIG,
    EXPECTED_BENCHMARK_MANIFEST_SHA256,
    MAX_FINAL_SPEND_USD,
    MAX_INPUT_TOKENS_UPPER_BOUND,
    MAX_OUTPUT_TOKENS,
    MAX_PLANNED_PROVIDER_REQUESTS,
    MAX_SERIALIZED_REQUEST_BYTES,
    PINNED_MODEL_ID,
    REVIEW_REPAIR_RESOURCE_CONFIG,
    REVIEW_REPAIR_RESOURCE_CONFIG_PATH,
    REVIEW_REPAIR_RESOURCE_CONFIG_SHA256,
    LIVE_SYSTEM_PROMPT,
    SNAPSHOT_IDS,
    StructuredAnalysisError,
    _acquire_final_lock,
    _live_run_lock_path,
    _read_approved_resource_config,
    _request_body,
    _serialized_request_bytes,
    build_release,
    estimate_request_token_ceilings,
    load_frozen_corpus,
    preflight_request_batch,
    render_markdown,
    run_pipeline,
    validate_citations,
    validate_live_review,
    validate_resource_config,
)


RESOURCE_CONFIG = MATCHED_RESOURCE_CONFIG


def write_approved_resource_config(path: Path) -> Path:
    path.write_bytes(APPROVED_RESOURCE_CONFIG_PATH.read_bytes())
    path.chmod(0o444)
    return path


def mock_review_payload(
    citation: dict[str, str],
    *,
    release: dict | None = None,
    fenced: bool = False,
    usage: dict[str, object] | None = None,
) -> dict[str, object]:
    reviews = [{
        "unit_id": "task-policy-guidance",
        "stance": "unclear",
        "reason": "Permissioned mechanics fixture; not a product review.",
        "evidence": [citation],
    }]
    if release is not None:
        reviews = [{
            "unit_id": unit["unit_id"],
            "stance": "unclear",
            "reason": "Permissioned mechanics fixture; not a product review.",
            "evidence": [{key: unit["evidence"][0][key] for key in ("source_id", "locator", "quote")}],
        } for unit in release["work_units"]]
    content = json.dumps({"reviews": reviews})
    if fenced:
        content = f"```json\n{content}\n```"
    payload: dict[str, object] = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


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
            self.assertEqual(set(unit["reader_actions"]), {"ic_hr_practitioner", "job_seeker", "hiring_manager"})
            self.assertTrue(all(action.strip() for action in unit["reader_actions"].values()))
        core_tasks = [u for u in units if u["kind"] == "task" and u["method_fields"].get("classification") == "common_core"]
        context_tasks = [u for u in units if u["kind"] == "task" and u["method_fields"].get("context_adaptation")]
        competencies = [u for u in units if u["kind"] == "competency"]
        self.assertEqual(len(core_tasks), 9)
        self.assertEqual(len(context_tasks), 4)
        self.assertEqual(len(competencies), 9)
        all_tasks = [unit for unit in units if unit["kind"] == "task"]
        self.assertTrue(all(task["method_fields"]["task_competency_links"] for task in all_tasks))
        self.assertTrue(all(link["evidence"] and link["direction"] == "task-to-required-competency" for task in all_tasks for link in task["method_fields"]["task_competency_links"]))
        self.assertEqual(len(release["coverage_audit"]["duty_area_coverage"]), 9)
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
        transparency = release["extraction_transparency"]
        self.assertEqual(transparency["admitted_source_count"], 13)
        self.assertEqual({row["source_id"] for row in transparency["admitted_sources"]}, set(self.corpus.sources))
        self.assertGreater(transparency["decision_counts"]["included"], 0)
        self.assertGreater(transparency["decision_counts"]["excluded"], 0)
        self.assertGreater(transparency["decision_counts"]["unresolved"], 0)
        for source in transparency["admitted_sources"]:
            self.assertTrue(source["decisions"], source["source_id"])
            for decision in source["decisions"]:
                self.assertIn(decision["status"], {"INCLUDED", "EXCLUDED", "UNRESOLVED"})
                self.assertTrue(decision["rationale"].strip())
                self.assertTrue(decision["evidence"])
                for evidence in decision["evidence"]:
                    self.assertEqual(evidence["source_id"], source["source_id"])
                    self.assertIn(evidence["quote"], self.corpus.source_texts[source["source_id"]])
        onet_decisions = [row for row in transparency["admitted_sources"] if row["source_id"] == "onet_hr_specialist"][0]["decisions"]
        self.assertTrue(any(row["status"] == "UNRESOLVED" and "benefit-plan" in row["item"] for row in onet_decisions))
        self.assertTrue(any(row["status"] == "EXCLUDED" and "Recruiting-only" in row["item"] for row in onet_decisions))
        priorities = release["skill_priorities"]
        self.assertEqual([row["rank"] for row in priorities], [1, 2, 3, 4, 5])
        for priority in priorities:
            self.assertEqual(priority["evidence_strength"]["occupational_anchor_source_count"], 1)
            self.assertEqual(priority["evidence_strength"]["occupational_anchor_source_ids"], ["onet_hr_specialist"])
            self.assertEqual(set(priority["practice_and_demonstration"]), {"ic_hr_practitioner", "job_seeker", "hiring_manager"})
            self.assertTrue(priority["context_variation"]["variation_limit"])
            self.assertTrue(priority["context_variation"]["evidence"])
        self.assertEqual(release["run"]["publication_status"], "offline_guide")
        self.assertFalse(release["run"]["model_review_accepted"])
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

    def test_rendered_markdown_has_no_trailing_whitespace(self) -> None:
        markdown = render_markdown(build_release(self.corpus, "c" * 40))
        trailing_lines = [number for number, line in enumerate(markdown.splitlines(), 1) if line.rstrip() != line]
        self.assertEqual(trailing_lines, [])

    def test_audience_actions_are_adjacent_to_machine_claims_and_reconcile_to_the_guide(self) -> None:
        release = build_release(self.corpus, "b" * 40)
        guide = render_markdown(release)
        audiences = ("ic_hr_practitioner", "job_seeker", "hiring_manager")
        for unit in release["work_units"]:
            claim_position = guide.find(unit["statement"])
            self.assertGreaterEqual(claim_position, 0, unit["unit_id"])
            for audience in audiences:
                action = unit["reader_actions"][audience]
                self.assertIn(action, guide)
                self.assertGreater(guide.find(action), claim_position)
                self.assertLess(guide.find(action), claim_position + 1800)
        for actions in (
            release["role"]["reader_actions"],
            release["role"]["title_variant_validation"]["reader_actions"],
            release["demand_layer"]["reader_actions"],
        ):
            self.assertEqual(set(actions), set(audiences))
            for action in actions.values():
                self.assertIn(action, guide)
        units = {unit["unit_id"]: unit for unit in release["work_units"]}
        for row in release["demand_layer"]["coverage_by_unit"]:
            self.assertEqual(row["reader_actions"], units[row["unit_id"]]["reader_actions"])
            for action in row["reader_actions"].values():
                self.assertIn(action, guide)
        self.assertIn("practical prompts, not proficiency ratings", guide)
        self.assertLess(guide.find("## Prioritized skill practice"), guide.find("## Task–competency backbone"))
        self.assertIn("not a posting-frequency, importance, or proficiency ranking", guide)
        for priority in release["skill_priorities"]:
            self.assertIn(priority["skill"], guide)
            self.assertIn(priority["evidence_strength"]["label"], guide)
            self.assertIn(priority["context_variation"]["statement"], guide)
            for action in priority["practice_and_demonstration"].values():
                self.assertIn(action, guide)
        self.assertIn("evidence-transparency.md", guide)
        self.assertTrue(all(unit["method_fields"]["practitioner_validated"] is False for unit in release["work_units"]))

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
            manifest_path = output / "run-manifest.json"
            evidence_path = output / "evidence-transparency.md"
            self.assertTrue(all(path.is_file() for path in (release_path, guide_path, html_path, evidence_path, receipt_path, manifest_path)))
            release = json.loads(release_path.read_text())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(release["run"]["mode"], "offline")
            self.assertEqual(release["run"]["model"], "deterministic-structured-job-analysis.v1")
            self.assertEqual(release["run"]["provider"], "local")
            self.assertEqual(release["run"]["execution_label"], "offline deterministic generator; zero model inference and zero provider calls")
            self.assertEqual(release["run"]["publication_status"], "offline_guide")
            self.assertEqual(release["run"]["model_review_status"], "not_requested")
            self.assertFalse(release["run"]["model_review_accepted"])
            self.assertEqual(release["run"]["resource_ledger"]["inference_requests"], 0)
            self.assertEqual(release["run"]["resource_ledger"]["inference_cost_usd_estimate"], 0.0)
            self.assertIsNone(release["run"]["resource_ledger"]["measured_provider_cost_usd"])
            self.assertEqual(release["run"]["resource_ledger"]["provider_usage"]["status"], "not_applicable")
            self.assertEqual(release["run"]["config_hash"], APPROVED_RESOURCE_CONFIG_SHA256)
            self.assertEqual(manifest["config_hash"], APPROVED_RESOURCE_CONFIG_SHA256)
            self.assertEqual(release["run"]["round"], 1)
            self.assertEqual(manifest["evidence_policy"], MATCHED_EVIDENCE_POLICY)
            for field in ("model", "provider", "sampling", "caps", "corpus_snapshot_ids", "evidence_policy"):
                if field == "evidence_policy":
                    self.assertEqual(manifest[field], MATCHED_EVIDENCE_POLICY)
                elif field == "corpus_snapshot_ids":
                    self.assertEqual(manifest[field], list(SNAPSHOT_IDS))
                else:
                    self.assertEqual(manifest[field], RESOURCE_CONFIG[field])
            self.assertEqual(manifest["execution"]["model"], "deterministic-structured-job-analysis.v1")
            self.assertEqual(manifest["execution"]["provider"], "local")
            self.assertEqual(manifest["execution"]["inference_requests"], 0)
            self.assertEqual(manifest["execution"]["publication_status"], "offline_guide")
            self.assertFalse(manifest["execution"]["model_review_accepted"])
            self.assertIn("DACUM-informed desk research", guide_path.read_text())
            self.assertIn("INCLUDED", evidence_path.read_text())
            html_doc = html_path.read_text()
            self.assertIn("<html lang=\"en\">", html_doc)
            self.assertIn("People Operations", html_doc)
            self.assertIn("Prioritized skill practice", html_doc)
            self.assertIn('href=\"evidence-transparency.md\"', html_doc)
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["commands_run"][0]["exit_code"], 0)
            self.assertEqual(receipt["manifest_sha256"], EXPECTED_BENCHMARK_MANIFEST_SHA256)

    def test_offline_wall_clock_includes_full_pipeline_not_only_release_assembly(self) -> None:
        original_loader = load_frozen_corpus

        def delayed_load(*args, **kwargs):
            time.sleep(0.15)
            return original_loader(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            with patch("skills_vector.structured_job_analysis.load_frozen_corpus", side_effect=delayed_load):
                run_pipeline(output_dir=output, mode="offline", candidate_revision="d" * 40)
            release = json.loads((output / "release.json").read_text())
            wall_minutes = release["run"]["resource_ledger"]["wall_clock_minutes"]
            self.assertGreaterEqual(wall_minutes * 60, 0.10)
            self.assertEqual(release["run"]["mode"], "offline")

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
        reasoning_enabled = json.loads(json.dumps(RESOURCE_CONFIG))
        reasoning_enabled["sampling"]["reasoning_effort"] = "high"
        with self.assertRaisesRegex(StructuredAnalysisError, 'reasoning_effort must be exactly "none"'):
            validate_resource_config(reasoning_enabled)

    def test_review_repair_config_is_pinned_and_reserves_adequate_conservative_output_budget(self) -> None:
        repair_config, repair_hash = _read_approved_resource_config(REVIEW_REPAIR_RESOURCE_CONFIG_PATH)
        self.assertEqual(repair_config, REVIEW_REPAIR_RESOURCE_CONFIG)
        self.assertEqual(repair_hash, REVIEW_REPAIR_RESOURCE_CONFIG_SHA256)
        self.assertEqual(RESOURCE_CONFIG["sampling"]["max_tokens"], 2048)
        self.assertEqual(repair_config["sampling"]["max_tokens"], MAX_OUTPUT_TOKENS)
        self.assertIn("every supplied work unit exactly once", LIVE_SYSTEM_PROMPT)
        self.assertIn("at most 20 words", LIVE_SYSTEM_PROMPT)
        self.assertIn("smallest set of exact citations", LIVE_SYSTEM_PROMPT)

        release = build_release(self.corpus, "c" * 40)
        matched_request = _request_body(release, RESOURCE_CONFIG)
        repair_request = _request_body(release, repair_config)
        matched_ceilings = estimate_request_token_ceilings(matched_request)
        repair_ceilings = estimate_request_token_ceilings(repair_request)
        self.assertEqual(repair_request["max_tokens"], 8192)
        self.assertEqual(repair_ceilings["output_tokens_upper_bound_including_reasoning"], 8192)
        self.assertEqual(
            repair_ceilings["total_input_plus_output_including_reasoning_tokens_upper_bound"],
            repair_ceilings["input_tokens_upper_bound"] + 8192,
        )
        self.assertGreater(repair_ceilings["cost_upper_bound_usd"], matched_ceilings["cost_upper_bound_usd"])
        _, total = preflight_request_batch([repair_request], repair_config)
        self.assertAlmostEqual(total, repair_ceilings["cost_upper_bound_usd"])
        self.assertLessEqual(total, repair_config["caps"]["max_inference_cost_usd"])
        self.assertLessEqual(total, MAX_FINAL_SPEND_USD)

    def test_live_request_deduplicates_citations_without_dropping_any_review_reference(self) -> None:
        release = build_release(self.corpus, "e" * 40)
        body = _request_body(release, RESOURCE_CONFIG)
        serialized = _serialized_request_bytes(body)
        self.assertLessEqual(len(serialized), MAX_SERIALIZED_REQUEST_BYTES)
        evidence = json.loads(body["messages"][1]["content"])
        catalog = evidence["evidence_catalog"]
        catalog_refs = [
            (row["source_id"], row["locator"], row["quote"])
            for row in catalog
        ]
        self.assertEqual(len(catalog_refs), len(set(catalog_refs)))
        packed_units = {row["unit_id"]: row for row in evidence["work_units"]}
        self.assertEqual(set(packed_units), {unit["unit_id"] for unit in release["work_units"]})
        all_release_refs = set()
        for unit in release["work_units"]:
            packed = packed_units[unit["unit_id"]]
            expected_unit_refs = {
                (item["source_id"], item["locator"], item["quote"])
                for item in unit["evidence"]
            }
            actual_unit_refs = {catalog_refs[index] for index in packed["evidence_ref_ids"]}
            self.assertEqual(actual_unit_refs, expected_unit_refs, unit["unit_id"])
            self.assertEqual(
                len(packed["task_competency_links"]),
                len(unit["method_fields"]["task_competency_links"]),
            )
            for packed_link, link in zip(
                packed["task_competency_links"],
                unit["method_fields"]["task_competency_links"],
                strict=True,
            ):
                expected_link_refs = {
                    (item["source_id"], item["locator"], item["quote"])
                    for item in link["evidence"]
                }
                actual_link_refs = {
                    catalog_refs[index] for index in packed_link["evidence_ref_ids"]
                }
                self.assertEqual(actual_link_refs, expected_link_refs, unit["unit_id"])
            all_release_refs.update(expected_unit_refs)
            for link in unit["method_fields"]["task_competency_links"]:
                all_release_refs.update(
                    (item["source_id"], item["locator"], item["quote"])
                    for item in link["evidence"]
                )
        self.assertEqual(set(catalog_refs), all_release_refs)
        ceilings = estimate_request_token_ceilings(body)
        self.assertEqual(ceilings["input_bytes"], len(serialized))
        self.assertLessEqual(ceilings["input_bytes"], MAX_SERIALIZED_REQUEST_BYTES)
        self.assertEqual(ceilings["reasoning_effort"], "none")

    def test_live_review_validation_rejects_omitted_supplied_work_units(self) -> None:
        release = build_release(self.corpus, "b" * 40)
        unit = release["work_units"][0]
        citation = {key: unit["evidence"][0][key] for key in ("source_id", "locator", "quote")}
        payload = mock_review_payload(citation)
        with self.assertRaisesRegex(
            StructuredAnalysisError,
            "exactly one review for every supplied work unit",
        ):
            validate_live_review(payload, release, self.corpus)

    def test_preflight_enforces_exact_request_reasoning_input_and_output_ceilings(self) -> None:
        request = {
            "model": PINNED_MODEL_ID,
            "messages": [{"role": "user", "content": "bounded test"}],
            "max_tokens": RESOURCE_CONFIG["sampling"]["max_tokens"],
            "reasoning_effort": "none",
        }
        per_request, total = preflight_request_batch([request], RESOURCE_CONFIG)
        self.assertEqual(len(per_request), MAX_PLANNED_PROVIDER_REQUESTS)
        self.assertAlmostEqual(total, sum(per_request))
        self.assertLessEqual(total, RESOURCE_CONFIG["caps"]["max_inference_cost_usd"])
        ceilings = estimate_request_token_ceilings(request)
        self.assertEqual(ceilings["reasoning_effort"], "none")
        self.assertEqual(ceilings["reasoning_tokens_additional_allowance"], 0)
        self.assertEqual(
            ceilings["output_tokens_upper_bound_including_reasoning"],
            RESOURCE_CONFIG["sampling"]["max_tokens"],
        )
        self.assertEqual(
            ceilings["total_input_plus_output_including_reasoning_tokens_upper_bound"],
            ceilings["input_tokens_upper_bound"] + RESOURCE_CONFIG["sampling"]["max_tokens"],
        )
        self.assertLessEqual(ceilings["input_bytes"], MAX_SERIALIZED_REQUEST_BYTES)
        self.assertLessEqual(ceilings["input_tokens_upper_bound"], MAX_INPUT_TOKENS_UPPER_BOUND)
        self.assertEqual(len(ceilings["request_sha256"]), 64)

        with self.assertRaisesRegex(StructuredAnalysisError, "exactly 1 planned provider request"):
            preflight_request_batch([request, request], RESOURCE_CONFIG)
        with self.assertRaisesRegex(StructuredAnalysisError, 'reasoning_effort must be exactly "none"'):
            estimate_request_token_ceilings({**request, "reasoning_effort": "high"})
        with self.assertRaisesRegex(StructuredAnalysisError, "pinned resource configuration"):
            estimate_request_token_ceilings({**request, "max_tokens": 2049})
        oversized = {**request, "messages": [{"role": "user", "content": "x" * MAX_SERIALIZED_REQUEST_BYTES}]}
        with self.assertRaisesRegex(StructuredAnalysisError, "serialized provider request"):
            estimate_request_token_ceilings(oversized)

        too_expensive = json.loads(json.dumps(RESOURCE_CONFIG))
        too_expensive["caps"]["max_inference_cost_usd"] = 0.000001
        with self.assertRaisesRegex(StructuredAnalysisError, "whole-run preflight"):
            preflight_request_batch([request], too_expensive)

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

    def test_cli_routes_corrective_selector_and_does_not_offer_lock_path_override(self) -> None:
        parsed = build_parser().parse_args([
            "structured-job-analysis", "--mode", "offline", "--corrective-round-2",
        ])
        self.assertTrue(parsed.corrective_round_2)
        for argv in (
            ["structured-job-analysis", "--mode", "live-final", "--live-lock-path", "/tmp/arbitrary-lock.json"],
            ["structured-job-analysis", "--mode", "live-final", "--corrective-round-2=/tmp/arbitrary-lock.json"],
        ):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                build_parser().parse_args(argv)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "offline-output"
            runner = Mock(return_value={"status": "passed_local_build", "run_id": "mock-run"})
            with patch("skills_vector.structured_job_analysis.run_pipeline", runner), redirect_stdout(io.StringIO()):
                result = main([
                    "structured-job-analysis", "--mode", "offline", "--corrective-round-2",
                    "--output-dir", str(output),
                ])
            self.assertEqual(result, 0)
            self.assertTrue(runner.call_args.kwargs["corrective_round_2"])

    def test_corrective_selection_keeps_freeze_confirmation_config_and_key_preflights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "corrective-r2.json"
            config_path = write_approved_resource_config(root / "resource.json")
            cases = (
                ("freeze-sha", {"confirm_final_run": True, "resource_config_path": config_path, "api_key": "mock-key"}),
                ("explicit --confirm-final-run", {"freeze_sha": "c" * 40, "resource_config_path": config_path, "api_key": "mock-key"}),
                ("parent-approved --resource-config", {"freeze_sha": "c" * 40, "confirm_final_run": True, "api_key": "mock-key"}),
                ("DEEPINFRA_API_KEY is required", {"freeze_sha": "c" * 40, "confirm_final_run": True, "resource_config_path": config_path, "api_key": ""}),
            )
            provider = Mock(side_effect=AssertionError("failed corrective preflight must not call provider"))
            with patch("skills_vector.structured_job_analysis.CORRECTIVE_R2_LIVE_LOCK", lock):
                for index, (message, options) in enumerate(cases):
                    with self.subTest(preflight=message), self.assertRaisesRegex(StructuredAnalysisError, message):
                        run_pipeline(
                            mode="live-final",
                            corrective_round_2=True,
                            candidate_revision="c" * 40,
                            output_dir=root / f"preflight-{index}",
                            _provider_call=provider,
                            **options,
                        )
                    self.assertFalse(lock.exists())
            provider.assert_not_called()

    def test_original_default_lock_still_blocks_and_remains_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_lock = root / "original-consumed.json"
            original_bytes = b'{"status":"failed_consumed_no_retry","run_id":"original"}\n'
            original_lock.write_bytes(original_bytes)
            corrective_lock = root / "corrective-r2.json"
            config_path = write_approved_resource_config(root / "resource.json")
            provider = Mock(side_effect=AssertionError("consumed original lock must block before provider"))
            with patch("skills_vector.structured_job_analysis.DEFAULT_LIVE_LOCK", original_lock), patch(
                "skills_vector.structured_job_analysis.CORRECTIVE_R2_LIVE_LOCK", corrective_lock
            ):
                with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                    run_pipeline(
                        mode="live-final",
                        resource_config_path=config_path,
                        freeze_sha="c" * 40,
                        confirm_final_run=True,
                        candidate_revision="c" * 40,
                        output_dir=root / "original-output",
                        api_key="mock-key-not-a-credential",
                        _provider_call=provider,
                    )
            provider.assert_not_called()
            self.assertEqual(original_lock.read_bytes(), original_bytes)
            self.assertFalse(corrective_lock.exists())
            self.assertFalse((root / "original-output").exists())

    def test_corrective_round_2_uses_only_its_fixed_lock_and_blocks_second_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_lock = root / "original-consumed.json"
            original_bytes = b'{"status":"failed_consumed_no_retry","run_id":"original"}\n'
            original_lock.write_bytes(original_bytes)
            corrective_lock = root / "corrective-r2.json"
            config_path = write_approved_resource_config(root / "resource.json")
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }
            provider = Mock(return_value=mock_review_payload(
                citation,
                release=build_release(self.corpus, "e" * 40),
                usage={"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
            ))
            with patch("skills_vector.structured_job_analysis.DEFAULT_LIVE_LOCK", original_lock), patch(
                "skills_vector.structured_job_analysis.CORRECTIVE_R2_LIVE_LOCK", corrective_lock
            ):
                receipt = run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="e" * 40,
                    confirm_final_run=True,
                    corrective_round_2=True,
                    candidate_revision="e" * 40,
                    output_dir=root / "corrective-output",
                    api_key="mock-key-not-a-credential",
                    _provider_call=provider,
                )
                self.assertEqual(receipt["status"], "passed_one_live_review")
                self.assertTrue(corrective_lock.is_file())
                self.assertEqual(json.loads(corrective_lock.read_text())["status"], "completed")
                self.assertEqual(original_lock.read_bytes(), original_bytes)
                with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                    run_pipeline(
                        mode="live-final",
                        resource_config_path=config_path,
                        freeze_sha="e" * 40,
                        confirm_final_run=True,
                        corrective_round_2=True,
                        candidate_revision="e" * 40,
                        output_dir=root / "corrective-retry-output",
                        api_key="mock-key-not-a-credential",
                        _provider_call=provider,
                    )
            provider.assert_called_once()
            self.assertEqual(original_lock.read_bytes(), original_bytes)
            self.assertEqual(json.loads(corrective_lock.read_text())["status"], "completed")
            self.assertFalse((root / "corrective-retry-output").exists())

    def test_corrective_selector_is_rejected_in_offline_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "offline-output"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main([
                    "structured-job-analysis", "--mode", "offline", "--corrective-round-2",
                    "--output-dir", str(output),
                ])
            self.assertEqual(result, 2)
            self.assertIn("live-only flags cannot be used in offline mode", stdout.getvalue())
            self.assertFalse(output.exists())

    def test_live_mode_rejects_any_resource_config_hash_change_before_provider_or_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "altered-resource.json"
            altered = APPROVED_RESOURCE_CONFIG_PATH.read_bytes().replace(b'"max_wall_minutes": 30', b'"max_wall_minutes": 29')
            config_path.write_bytes(altered)
            config_path.chmod(0o444)
            output = root / "output"
            lock = root / "one-shot.json"
            provider = Mock(side_effect=AssertionError("altered config must not call provider"))
            with self.assertRaisesRegex(StructuredAnalysisError, "hash mismatch"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="e" * 40,
                    confirm_final_run=True,
                    candidate_revision="e" * 40,
                    output_dir=output,
                    api_key="mock-key",
                    _live_lock_path=lock,
                    _provider_call=provider,
                )
            provider.assert_not_called()
            self.assertFalse(lock.exists())
            self.assertFalse(output.exists())

    def test_repair_config_live_run_uses_candidate_config_identity_and_records_a_only_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            candidate = "c" * 40
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }
            provider = Mock(return_value=mock_review_payload(
                citation,
                release=build_release(self.corpus, candidate),
                usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
            ))
            with patch("skills_vector.structured_job_analysis.REPO_ROOT", root / "repo"):
                identity_path = _live_run_lock_path(
                    REVIEW_REPAIR_RESOURCE_CONFIG_SHA256,
                    candidate,
                    corrective_round_2=False,
                )
                receipt = run_pipeline(
                    mode="live-final",
                    resource_config_path=REVIEW_REPAIR_RESOURCE_CONFIG_PATH,
                    freeze_sha=candidate,
                    confirm_final_run=True,
                    candidate_revision=candidate,
                    output_dir=output,
                    api_key="repair-config-test-key",
                    _provider_call=provider,
                )
                self.assertTrue(identity_path.is_file())
                lock_record = json.loads(identity_path.read_text())
                self.assertEqual(lock_record["status"], "completed")
                self.assertEqual(lock_record["candidate_revision"], candidate)
                self.assertEqual(lock_record["resource_config_hash"], REVIEW_REPAIR_RESOURCE_CONFIG_SHA256)
                self.assertNotEqual(
                    identity_path,
                    _live_run_lock_path(
                        REVIEW_REPAIR_RESOURCE_CONFIG_SHA256,
                        "d" * 40,
                        corrective_round_2=False,
                    ),
                )
                self.assertEqual(receipt["config_hash"], REVIEW_REPAIR_RESOURCE_CONFIG_SHA256)
                self.assertEqual(receipt["preflight"]["request_token_ceilings"][0]["output_tokens_upper_bound_including_reasoning"], 8192)
                self.assertIn("max_tokens=8192", receipt["cost_reservation"]["basis"])
                release = json.loads((output / "release.json").read_text())
                self.assertEqual(release["run"]["sampling"]["max_tokens"], 8192)
                self.assertEqual(release["run"]["config_hash"], REVIEW_REPAIR_RESOURCE_CONFIG_SHA256)
                self.assertIn("not the matched historical A/B comparison", release["run"]["resource_config_scope"])
                manifest = json.loads((output / "run-manifest.json").read_text())
                self.assertEqual(manifest["config_hash"], REVIEW_REPAIR_RESOURCE_CONFIG_SHA256)
                self.assertEqual(manifest["sampling"]["max_tokens"], 8192)
                self.assertIn("not the matched historical A/B comparison", manifest["resource_config_scope"])
                provider.assert_called_once()

                with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                    run_pipeline(
                        mode="live-final",
                        resource_config_path=REVIEW_REPAIR_RESOURCE_CONFIG_PATH,
                        freeze_sha=candidate,
                        confirm_final_run=True,
                        candidate_revision=candidate,
                        output_dir=root / "second-output",
                        api_key="repair-config-test-key",
                        _provider_call=provider,
                    )
                provider.assert_called_once()
                self.assertFalse((root / "second-output").exists())

    def test_live_cost_guard_blocks_mock_provider_before_one_shot_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            output = root / "output"
            lock = root / "one-shot.json"
            provider = Mock(side_effect=AssertionError("over-cap run must not call provider"))
            with patch("skills_vector.structured_job_analysis.estimate_request_cost", return_value=MAX_FINAL_SPEND_USD + 0.01):
                with self.assertRaisesRegex(StructuredAnalysisError, "whole-run preflight"):
                    run_pipeline(
                        mode="live-final",
                        resource_config_path=config_path,
                        freeze_sha="f" * 40,
                        confirm_final_run=True,
                        candidate_revision="f" * 40,
                        output_dir=output,
                        api_key="mock-key",
                        _live_lock_path=lock,
                        _provider_call=provider,
                    )
            provider.assert_not_called()
            self.assertFalse(lock.exists())
            receipt = json.loads((output / "run-receipt.json").read_text())
            self.assertEqual(receipt["status"], "blocked_by_preflight_no_provider_call")
            self.assertEqual(receipt["provider_calls"], 0)
            self.assertEqual(receipt["commands_run"][0]["exit_code"], 2)
            self.assertFalse((output / "release.json").exists())

    def test_mocked_live_final_path_accepts_fenced_strict_review_and_retains_raw_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
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
                return mock_review_payload(
                    citation,
                    release=build_release(self.corpus, "e" * 40),
                    usage={"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                )

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
            self.assertEqual(calls[0]["body"]["reasoning_effort"], "none")
            self.assertEqual(calls[0]["body"]["max_tokens"], RESOURCE_CONFIG["sampling"]["max_tokens"])
            self.assertEqual(calls[0]["key"], "unit-test-secret-never-write")
            preflight = receipt["preflight"]
            ceilings = preflight["request_token_ceilings"][0]
            self.assertEqual(
                ceilings["request_sha256"],
                hashlib.sha256(json.dumps(calls[0]["body"], ensure_ascii=False).encode("utf-8")).hexdigest(),
            )
            self.assertLessEqual(ceilings["input_bytes"], MAX_SERIALIZED_REQUEST_BYTES)
            self.assertLessEqual(ceilings["input_tokens_upper_bound"], MAX_INPUT_TOKENS_UPPER_BOUND)
            self.assertEqual(json.loads(lock_path.read_text())["request_sha256"], ceilings["request_sha256"])
            self.assertEqual(preflight["reasoning_effort"], "none")
            self.assertIn(
                f"included in the max_tokens={RESOURCE_CONFIG['sampling']['max_tokens']} output ceiling",
                preflight["reasoning_accounting"],
            )
            self.assertEqual(receipt["provider_calls"], 1)
            self.assertEqual(receipt["status"], "passed_one_live_review")
            self.assertEqual(receipt["provider_response_validation"], "accepted_strict_semantics")
            self.assertEqual(receipt["provider_response"]["retention_status"], "retained")
            self.assertEqual(receipt["config_hash"], APPROVED_RESOURCE_CONFIG_SHA256)
            self.assertFalse(receipt["preflight"]["automatic_retry"])
            self.assertLessEqual(receipt["preflight"]["whole_run_cost_upper_bound_usd"], MAX_FINAL_SPEND_USD)
            self.assertLessEqual(receipt["inference_cost_usd_estimate"], receipt["inference_cost_usd_upper_bound"])
            self.assertIsNone(receipt["measured_provider_cost_usd"])
            serialized = "\n".join(path.read_text() for path in output.iterdir() if path.is_file())
            self.assertNotIn("unit-test-secret-never-write", serialized)
            release = json.loads((output / "release.json").read_text())
            self.assertEqual(release["run"]["config_hash"], APPROVED_RESOURCE_CONFIG_SHA256)
            self.assertEqual(release["run"]["model"], PINNED_MODEL_ID)
            self.assertEqual(release["run"]["provider"], "deepinfra")
            self.assertEqual(release["run"]["sampling"], RESOURCE_CONFIG["sampling"])
            self.assertEqual(release["run"]["resource_caps"], RESOURCE_CONFIG["caps"])
            self.assertEqual(release["run"]["publication_status"], "model_reviewed_release")
            self.assertEqual(release["run"]["model_review_status"], "accepted")
            self.assertTrue(release["run"]["model_review_accepted"])
            manifest = json.loads((output / "run-manifest.json").read_text())
            self.assertEqual(manifest["config_hash"], APPROVED_RESOURCE_CONFIG_SHA256)
            for field in ("model", "provider", "sampling", "caps", "corpus_snapshot_ids", "evidence_policy"):
                self.assertEqual(manifest[field], {
                    "model": RESOURCE_CONFIG["model"],
                    "provider": RESOURCE_CONFIG["provider"],
                    "sampling": RESOURCE_CONFIG["sampling"],
                    "caps": RESOURCE_CONFIG["caps"],
                    "corpus_snapshot_ids": list(SNAPSHOT_IDS),
                    "evidence_policy": MATCHED_EVIDENCE_POLICY,
                }[field])
            review = json.loads((output / "live-model-review-untrusted.json").read_text())
            self.assertEqual(review["status"], "untrusted_model_review_not_validation")
            self.assertIn("not practitioner validation", review["notice"])
            response_record = json.loads((output / "provider-response.json").read_text())
            raw_body = base64.b64decode(response_record["raw_body_base64"])
            self.assertEqual(response_record["provider_usage"]["total_tokens"], 140)
            self.assertEqual(response_record["retention_status"], "retained")
            lock = json.loads(lock_path.read_text())
            self.assertEqual(lock["status"], "completed")
            with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                _acquire_final_lock(lock_path, {"status": "started"})

    def test_fenced_json_review_is_accepted_after_strict_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            output = root / "output"
            lock = root / "one-shot.json"
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }
            payload = mock_review_payload(
                citation,
                release=build_release(self.corpus, "9" * 40),
                fenced=True,
                usage={"prompt_tokens": 110, "completion_tokens": 36, "total_tokens": 146},
            )
            receipt = run_pipeline(
                mode="live-final",
                resource_config_path=config_path,
                freeze_sha="9" * 40,
                confirm_final_run=True,
                candidate_revision="9" * 40,
                output_dir=output,
                api_key="fenced-json-test-key",
                _live_lock_path=lock,
                _provider_call=lambda _body, _key: payload,
            )
            self.assertEqual(receipt["status"], "passed_one_live_review")
            self.assertEqual(receipt["provider_response_validation"], "accepted_strict_semantics")
            retained = json.loads(base64.b64decode(json.loads((output / "provider-response.json").read_text())["raw_body_base64"]))
            self.assertTrue(retained["choices"][0]["message"]["content"].startswith("```json\n"))
            self.assertTrue(json.loads((output / "release.json").read_text())["run"]["model_review_accepted"])

    def test_malformed_review_without_usage_is_retained_and_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            output = root / "output"
            lock = root / "one-shot.json"
            malformed = {"choices": [{"message": {"content": "```json\n{\"reviews\":[}\n```"}, "finish_reason": "stop"}]}
            with self.assertRaisesRegex(StructuredAnalysisError, "not valid review JSON"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="8" * 40,
                    confirm_final_run=True,
                    candidate_revision="8" * 40,
                    output_dir=output,
                    api_key="malformed-review-test-key",
                    _live_lock_path=lock,
                    _provider_call=lambda _body, _key: malformed,
                )
            receipt = json.loads((output / "run-receipt.json").read_text())
            evidence = json.loads((output / "provider-response.json").read_text())
            raw_body = json.loads(base64.b64decode(evidence["raw_body_base64"]))
            release = json.loads((output / "release.json").read_text())
            self.assertEqual(receipt["provider_usage"]["status"], "missing")
            self.assertIsNone(receipt["inference_cost_usd_estimate"])
            self.assertGreater(receipt["inference_cost_usd_upper_bound"], 0)
            self.assertTrue(evidence["response_available"])
            self.assertIn("```json", raw_body["choices"][0]["message"]["content"])
            self.assertEqual(release["run"]["publication_status"], "offline_guide_after_rejected_review")
            self.assertFalse(release["run"]["model_review_accepted"])
            self.assertFalse((output / "live-model-review-untrusted.json").exists())
            self.assertEqual(json.loads(lock.read_text())["status"], "failed_consumed_no_retry")

    def test_length_terminated_completion_is_retained_and_rejected_even_if_review_json_parses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            output = root / "output"
            lock = root / "one-shot.json"
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }
            truncated = mock_review_payload(
                citation,
                usage={"prompt_tokens": 5744, "completion_tokens": 2048, "total_tokens": 7792},
            )
            truncated["choices"][0]["finish_reason"] = "length"
            provider = Mock(return_value=truncated)
            with self.assertRaisesRegex(StructuredAnalysisError, "did not finish normally"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="8" * 40,
                    confirm_final_run=True,
                    candidate_revision="8" * 40,
                    output_dir=output,
                    api_key="truncated-review-test-key",
                    _live_lock_path=lock,
                    _provider_call=provider,
                )

            receipt = json.loads((output / "run-receipt.json").read_text())
            evidence = json.loads((output / "provider-response.json").read_text())
            raw_payload = json.loads(base64.b64decode(evidence["raw_body_base64"]))
            release = json.loads((output / "release.json").read_text())
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(raw_payload["choices"][0]["finish_reason"], "length")
            self.assertTrue(evidence["response_available"])
            self.assertEqual(evidence["retention_status"], "retained")
            self.assertEqual(receipt["provider_usage"]["status"], "reported")
            self.assertEqual(receipt["provider_usage"]["output_tokens"], 2048)
            self.assertIsNotNone(receipt["provider_usage_cost_estimate_usd"])
            self.assertIsNone(receipt["measured_provider_cost_usd"])
            self.assertEqual(receipt["status"], "failed_after_one_shot_consumed")
            self.assertEqual(receipt["provider_response_validation"], "rejected")
            self.assertEqual(release["run"]["publication_status"], "offline_guide_after_rejected_review")
            self.assertFalse(release["run"]["model_review_accepted"])
            self.assertFalse((output / "live-model-review-untrusted.json").exists())
            self.assertEqual(json.loads(lock.read_text())["status"], "failed_consumed_no_retry")

    def test_semantically_invalid_reference_retains_response_usage_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            output = root / "output"
            lock = root / "one-shot.json"
            invalid_review = {
                "reviews": [{
                    "unit_id": "task-policy-guidance",
                    "stance": "challenge",
                    "reason": "This quote resolves in the corpus but was not supplied for this unit.",
                    "evidence": [{
                        "source_id": "onet_hr_specialist",
                        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                        "quote": "Address employee relations issues, such as harassment allegations, work complaints, or other employee concerns.",
                    }],
                }],
            }
            payload = {
                "choices": [{"message": {"content": json.dumps(invalid_review)}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            }
            observed_before_validation: dict[str, object] = {}
            actual_validator = validate_live_review

            def check_persisted_first(provider_payload, release, corpus):
                observed_before_validation["response_exists"] = (output / "provider-response.json").is_file()
                saved = json.loads((output / "run-receipt.json").read_text())
                observed_before_validation["usage"] = saved["provider_usage"]
                return actual_validator(provider_payload, release, corpus)

            with patch("skills_vector.structured_job_analysis.validate_live_review", side_effect=check_persisted_first):
                with self.assertRaisesRegex(StructuredAnalysisError, "citation was not supplied"):
                    run_pipeline(
                        mode="live-final",
                        resource_config_path=config_path,
                        freeze_sha="7" * 40,
                        confirm_final_run=True,
                        candidate_revision="7" * 40,
                        output_dir=output,
                        api_key="strict-semantic-test-key",
                        _live_lock_path=lock,
                        _provider_call=lambda _body, _key: payload,
                    )
            receipt = json.loads((output / "run-receipt.json").read_text())
            response_record = json.loads((output / "provider-response.json").read_text())
            release = json.loads((output / "release.json").read_text())
            self.assertTrue(observed_before_validation["response_exists"])
            self.assertEqual(observed_before_validation["usage"]["input_tokens"], 120)
            self.assertEqual(receipt["provider_usage"]["status"], "reported")
            self.assertEqual(response_record["provider_usage"]["output_tokens"], 30)
            self.assertIsNotNone(receipt["provider_usage_cost_estimate_usd"])
            self.assertIsNone(receipt["measured_provider_cost_usd"])
            self.assertEqual(release["run"]["publication_status"], "offline_guide_after_rejected_review")
            self.assertFalse(release["run"]["model_review_accepted"])
            self.assertFalse((output / "live-model-review-untrusted.json").exists())
            self.assertEqual(json.loads(lock.read_text())["status"], "failed_consumed_no_retry")

    def test_provider_failure_before_response_records_usage_unavailable_and_consumes_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            output = root / "output"
            lock = root / "one-shot.json"

            def fail_before_response(_body, _key):
                raise TimeoutError("mock provider connection failed before response")

            with self.assertRaisesRegex(StructuredAnalysisError, "TimeoutError"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="6" * 40,
                    confirm_final_run=True,
                    candidate_revision="6" * 40,
                    output_dir=output,
                    api_key="before-response-test-key",
                    _live_lock_path=lock,
                    _provider_call=fail_before_response,
                )
            receipt = json.loads((output / "run-receipt.json").read_text())
            evidence = json.loads((output / "provider-response.json").read_text())
            release = json.loads((output / "release.json").read_text())
            self.assertFalse(evidence["response_available"])
            self.assertEqual(receipt["provider_usage"]["status"], "unavailable")
            self.assertIsNone(receipt["inference_cost_usd_estimate"])
            self.assertGreater(receipt["reserved_cost_upper_bound_usd"], 0)
            self.assertEqual(release["run"]["publication_status"], "offline_guide_after_failed_live_review")
            self.assertFalse(release["run"]["model_review_accepted"])
            self.assertFalse((output / "live-model-review-untrusted.json").exists())
            self.assertEqual(json.loads(lock.read_text())["status"], "failed_consumed_no_retry")

    def test_provider_output_over_limit_consumes_one_shot_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            lock = root / "one-shot.json"
            output = root / "output"
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }
            payload = {
                "choices": [{"message": {"content": json.dumps({"reviews": [{
                    "unit_id": "task-policy-guidance",
                    "stance": "unclear",
                    "reason": "Mocked output usage exceeds the bounded completion budget.",
                    "evidence": [citation],
                }]})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": MAX_OUTPUT_TOKENS + 1},
            }
            calls: list[dict[str, object]] = []

            def fake_provider(body: dict[str, object], _key: str) -> dict[str, object]:
                calls.append(body)
                return payload

            with self.assertRaisesRegex(StructuredAnalysisError, "output usage exceeded max_tokens including reasoning"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="b" * 40,
                    confirm_final_run=True,
                    candidate_revision="b" * 40,
                    output_dir=output,
                    api_key="unit-test-over-limit-secret",
                    _live_lock_path=lock,
                    _provider_call=fake_provider,
                )
            self.assertEqual(len(calls), 1)
            receipt = json.loads((output / "run-receipt.json").read_text())
            self.assertEqual(receipt["status"], "failed_after_one_shot_consumed")
            self.assertEqual(receipt["provider_usage"]["output_tokens"], MAX_OUTPUT_TOKENS + 1)
            self.assertIsNotNone(receipt["provider_usage_cost_estimate_usd"])
            self.assertIsNone(receipt["measured_provider_cost_usd"])
            self.assertEqual(receipt["cost_reservation"]["status"], "consumed_after_provider_failure")
            self.assertEqual(json.loads(lock.read_text())["status"], "failed_consumed_no_retry")
            with self.assertRaisesRegex(StructuredAnalysisError, "already started"):
                run_pipeline(
                    mode="live-final",
                    resource_config_path=config_path,
                    freeze_sha="b" * 40,
                    confirm_final_run=True,
                    candidate_revision="b" * 40,
                    output_dir=output,
                    api_key="unit-test-over-limit-secret",
                    _live_lock_path=lock,
                    _provider_call=fake_provider,
                )
            self.assertEqual(len(calls), 1)

    def test_missing_provider_usage_consumes_reserved_upper_bound_without_inventing_actuals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
            lock = root / "one-shot.json"
            output = root / "output"
            citation = {
                "source_id": "onet_hr_specialist",
                "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
                "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
            }
            observed_before_call: dict[str, object] = {}

            def fake_provider(_body: dict[str, object], _key: str) -> dict[str, object]:
                prior = json.loads((output / "run-receipt.json").read_text())
                observed_before_call.update(prior)
                return mock_review_payload(
                    citation,
                    release=build_release(self.corpus, "a" * 40),
                )

            receipt = run_pipeline(
                mode="live-final",
                resource_config_path=config_path,
                freeze_sha="a" * 40,
                confirm_final_run=True,
                candidate_revision="a" * 40,
                output_dir=output,
                api_key="unit-test-no-usage-secret",
                _live_lock_path=lock,
                _provider_call=fake_provider,
            )
            upper_bound = receipt["preflight"]["whole_run_estimate_upper_bound_usd"]
            self.assertEqual(observed_before_call["cost_reservation"]["status"], "reserved_before_provider_call")
            self.assertEqual(receipt["status"], "passed_one_live_review_usage_unreported")
            self.assertEqual(receipt["provider_usage"], {
                "status": "missing", "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None,
            })
            self.assertIsNone(receipt["provider_usage_cost_estimate_usd"])
            self.assertIsNone(receipt["inference_cost_usd_estimate"])
            self.assertEqual(receipt["inference_cost_usd_upper_bound"], upper_bound)
            self.assertEqual(receipt["cost_reservation"]["status"], "consumed_usage_unreported")
            review = json.loads((output / "live-model-review-untrusted.json").read_text())
            self.assertEqual(review["usage"]["status"], "missing")
            release = json.loads((output / "release.json").read_text())
            ledger = release["run"]["resource_ledger"]
            self.assertIsNone(ledger["inference_cost_usd_estimate"])
            self.assertEqual(ledger["inference_cost_usd_upper_bound"], upper_bound)
            self.assertEqual(ledger["provider_usage"]["input_tokens"], None)
            self.assertIsNone(ledger["provider_usage_cost_estimate_usd"])
            self.assertEqual(json.loads(lock.read_text())["status"], "completed_usage_unreported")

    def test_provider_secret_echo_is_discarded_and_receipt_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_approved_resource_config(root / "resource.json")
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
