"""Bounded all-role refresh, retention gates, and source-cache behavior."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_support import active_runtime_bundle, build_slice, publish_test_release  # noqa: E402
from skills_vector.market.publish import PublishError, merge_slice, publish_release  # noqa: E402
from skills_vector.market.core import CatalogStore  # noqa: E402
from skills_vector.market.refresh import (  # noqa: E402
    FIXED_ROLES,
    SourceEvidenceCache,
    load_refresh_policy,
    run_refresh_once,
)
from skills_vector.market.release import canonical_json, sha256_file, sha256_text  # noqa: E402


class RefreshBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.evidence = self.root / "operator-evidence"
        self.evidence.mkdir()
        self.release_root = self.root / "release"
        self.runtime, self.budget = active_runtime_bundle(self.root / "operator-config")
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.policy_path = self.root / "refresh-policy.json"
        self.policy_path.write_text(
            json.dumps({
                "schema_version": "skills-vector-operator-refresh/1",
                "cadence_hours": 1,
                "min_postings": 1,
                "max_postings": 2,
                "max_boards": 1,
            }),
            encoding="utf-8",
        )
        self.policy = load_refresh_policy(self.policy_path)
        self.checkpoint = self.root / "refresh-checkpoint.json"
        self._build_last_good_release()

    def tearDown(self) -> None:
        self.budget.close()
        self._tmp.cleanup()

    def _build_last_good_release(self) -> None:
        for slug in FIXED_ROLES:
            source_slice = build_slice(
                self.root / "initial-slices" / slug,
                run_id=f"last_good_{slug}",
                occupation=slug,
            )
            publish_test_release(self.release_root, source_slice)
        release = CatalogStore(self.release_root).release()
        assert release is not None
        self.initial_current_id = release.release_id

    def test_stale_merged_candidate_cannot_replace_a_newer_current_release(self) -> None:
        stale_slice = build_slice(
            self.root / "stale-slice",
            run_id="stale_candidate",
            occupation=FIXED_ROLES[0],
        )
        stale_candidate = merge_slice(self.release_root, stale_slice)
        concurrent_slice = build_slice(
            self.root / "concurrent-slice",
            run_id="concurrent_candidate",
            occupation=FIXED_ROLES[1],
        )
        concurrent_candidate = merge_slice(self.release_root, concurrent_slice)
        concurrent = publish_release(self.release_root, concurrent_candidate)
        pointer_after_concurrent_publish = (self.release_root / "current.json").read_bytes()

        with self.assertRaises(PublishError):
            publish_release(self.release_root, stale_candidate)

        self.assertEqual((self.release_root / "current.json").read_bytes(), pointer_after_concurrent_publish)
        self.assertEqual(CatalogStore(self.release_root).release().release_id, concurrent["release_id"])

    @staticmethod
    def _stamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _record_attempt(self, run_id: str, *, stage: str = "synthesis") -> float:
        model_role = "challenger" if stage == "challenge" else "primary"
        model_id = self.runtime.model_for(model_role)
        price = self.runtime.price_for(model_id)
        input_tokens, output_tokens = 100, 25
        request = f"synthetic isolated refresh evidence {run_id} {stage}".encode("utf-8")
        attempt = self.budget.reserve_attempt(
            run_id=run_id,
            attempt_key=f"{run_id}-{stage}",
            stage=stage,
            attempt_number=1,
            model_id=model_id,
            request_bytes=request,
            input_token_cap=input_tokens,
            output_token_cap=output_tokens,
            max_model_calls=self.runtime.limits["max_model_calls"],
        )
        cost = price.cost_usd(input_tokens, output_tokens)
        self.budget.finish_attempt(
            attempt["attempt_id"],
            actual_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_bytes=b"synthetic test-only settled response",
            finish_reason="stop",
            receipt={"test_only": True},
        )
        return cost

    @staticmethod
    def _hashes(directory: Path) -> dict[str, str]:
        return {
            path.relative_to(directory).as_posix(): sha256_file(path)
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    def _frozen_comparison(self, slug: str, *, retain_challenge: bool) -> dict[str, Any]:
        comparison_id = f"frozen_{slug}"
        comparison_dir = self.evidence / "runs" / comparison_id / "comparison" / "arms"
        passages = [{"source_id": f"source_{index}", "sha256": f"{index:064x}"} for index in range(1, 7)]
        passages_sha = sha256_text(canonical_json(passages))
        primary = build_slice(
            comparison_dir / "primary-only",
            run_id=comparison_id,
            occupation=slug,
        )
        challenger = build_slice(
            comparison_dir / "primary-plus-challenge",
            run_id=comparison_id,
            occupation=slug,
        )
        primary_claims = json.loads((primary / "claims.json").read_text(encoding="utf-8"))
        challenger_claims = json.loads((challenger / "claims.json").read_text(encoding="utf-8"))
        retained_id = f"clm_retained_{slug.replace('-', '_')}"
        if not any(row["id"] == retained_id for row in challenger_claims):
            extra = dict(challenger_claims[0])
            extra.update({
                "id": retained_id,
                "statement": "Human resources specialists administer onboarding and employee lifecycle processes.",
            })
            challenger_claims.append(extra)
            (challenger / "claims.json").write_text(json.dumps(challenger_claims, indent=2) + "\n", encoding="utf-8")
        primary_cost = self._record_attempt(comparison_id)
        challenger_cost = self._record_attempt(comparison_id, stage="challenge")
        current_cost = primary_cost + challenger_cost

        def arm(name: str, directory: Path, claims: list[dict[str, Any]], cost: float) -> dict[str, Any]:
            return {
                "arm": name,
                "slice_path": str(directory.resolve()),
                "artifact_sha256": self._hashes(directory),
                "source_passages_sha256": passages_sha,
                "source_passage_count": len(passages),
                "eligible_for_review": True,
                "policy_gate_reasons": [],
                "claims": len(claims),
                "claim_ids": [str(row["id"]) for row in claims],
                "known_cost_usd": cost,
                "wall_elapsed_ms": 1000,
            }

        comparison = {
            "schema_version": "skills-vector-matched-comparison/1",
            "comparison_id": comparison_id,
            "occupation": slug,
            "created_at": self._stamp(self.now),
            "source_passages": passages,
            "source_passages_sha256": passages_sha,
            "arms": {
                "primary-only": arm("primary-only", primary, primary_claims, primary_cost),
                "primary-plus-challenge": arm("primary-plus-challenge", challenger, challenger_claims, current_cost),
            },
            "challenger": {
                "nominated_source_backed_claim_ids": [retained_id],
                "nominated_expectation_ids": [],
                "unsupported_refinements": [],
                "challenge_findings": [],
            },
        }
        comparison_path = self.evidence / "receipts" / f"{comparison_id}-comparison.json"
        comparison_path.parent.mkdir(parents=True, exist_ok=True)
        comparison_bytes = (canonical_json(comparison) + "\n").encode("utf-8")
        comparison_path.write_bytes(comparison_bytes)
        selected = "primary-plus-challenge" if retain_challenge else "primary-only"
        adjudication = {
            "schema_version": "skills-vector-independent-adjudication/1",
            "comparison_id": comparison_id,
            "comparison_sha256": hashlib.sha256(comparison_bytes).hexdigest(),
            "reviewed_at": self._stamp(self.now),
            "reviewer": {
                "id": f"fresh-reviewer-{slug}",
                "role": "independent comparison reviewer",
                "independent_of_pipeline": True,
                "not_author_of_candidate": True,
            },
            "selected_arm": selected,
            "decision_reason": "Synthetic isolated test evidence selects the arm prescribed by its aggregate fixture metrics.",
            "miss_adjudications": {retained_id: "retain_supported" if retain_challenge else "not_selected"},
            "unsupported_refinement_adjudications": {},
        }
        adjudication_path = self.evidence / "receipts" / f"{comparison_id}-adjudication.json"
        adjudication_bytes = (json.dumps(adjudication, indent=2) + "\n").encode("utf-8")
        adjudication_path.write_bytes(adjudication_bytes)

        def metrics(count: int, cost: float, misses: int) -> dict[str, Any]:
            return {
                "supported_claim_count": count,
                "citation_failure_count": 0,
                "miss_count": misses,
                "unsupported_refinement_count": 0,
                "passage_coverage": {"cited_passages": 3, "total_passages": len(passages)},
                "roles_present": {"occupations": [slug], "denominator": 4},
                "wall_clock_seconds": 1.0,
                "actual_cost_usd": cost,
            }

        return {
            "references": {
                "comparison_path": str(comparison_path),
                "comparison_sha256": hashlib.sha256(comparison_bytes).hexdigest(),
                "adjudication_path": str(adjudication_path),
                "adjudication_sha256": hashlib.sha256(adjudication_bytes).hexdigest(),
            },
            "metrics": {
                "primary_only": metrics(
                    len(primary_claims), primary_cost, 1 if retain_challenge else 0,
                ),
                "primary_plus_challenge": metrics(
                    len(challenger_claims), current_cost, 0,
                ),
            },
        }

    def _write_retention_record(self, *, retain_challenge: bool) -> Path:
        role_receipts: dict[str, Any] = {}
        metrics_by_role: dict[str, Any] = {}
        for slug in FIXED_ROLES:
            row = self._frozen_comparison(slug, retain_challenge=retain_challenge)
            role_receipts[slug] = row["references"]
            metrics_by_role[slug] = row["metrics"]
        record = {
            "schema_version": "skills-vector-four-role-stage-retention/1",
            "selected_stage": "primary-plus-challenge" if retain_challenge else "primary-only",
            "role_receipts": role_receipts,
            "metrics_by_role": metrics_by_role,
            "reviewer": {
                "reviewer_id": "fresh-independent-test-reviewer",
                "provider": "advisor-test-fixture",
                "model": "advisor-test-fixture",
                "reasoning": "Synthetic record validates consumer behavior only.",
                "fresh_context": True,
                "independent_of_implementation": True,
                "product_edits_authorized": False,
            },
            "decision_reason": "Synthetic isolated test fixture for the fixed four-role global retention rule.",
        }
        path = self.evidence / "round-one" / "stage-retention.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return path

    def _producer(
        self,
        calls: list[dict[str, Any]],
        *,
        fail_first: bool = False,
        mismatch_cost_first: bool = False,
    ):
        def run_research(config):  # noqa: ANN001
            slug = config.occupation
            calls.append({"occupation": slug, "challenge_enabled": config.challenge_enabled})
            if fail_first and len(calls) == 1:
                run_id = config.run_id
                request = b"synthetic unknown-cost test"
                attempt = self.budget.reserve_attempt(
                    run_id=run_id,
                    attempt_key=f"{run_id}-unknown",
                    stage="synthesis",
                    attempt_number=1,
                    model_id=self.runtime.model_for("primary"),
                    request_bytes=request,
                    input_token_cap=100,
                    output_token_cap=25,
                    max_model_calls=self.runtime.limits["max_model_calls"],
                )
                self.budget.finish_attempt(
                    attempt["attempt_id"],
                    actual_usd=None,
                    input_tokens=None,
                    output_tokens=None,
                    error_code="test_unknown_usage",
                    receipt={"test_only": True},
                )
                return {
                    "run_id": run_id,
                    "occupation": slug,
                    "status": "candidate_blocked",
                    "run_dir": str(Path(config.evidence_root) / "runs" / run_id),
                    "receipt": str(Path(config.evidence_root) / "receipts" / f"{run_id}.json"),
                    "eligible": False,
                    "eligible_arms": [],
                    "challenge_enabled": config.challenge_enabled,
                    "candidate_path": None,
                    "stats": {"model_calls": 1},
                    "error": "synthetic unknown usage",
                }

            run_id = config.run_id
            primary_cost = self._record_attempt(run_id)
            cost = primary_cost
            if config.challenge_enabled:
                cost += self._record_attempt(run_id, stage="challenge")
            run_dir = Path(config.evidence_root) / "runs" / run_id
            arm_root = run_dir / "comparison" / "arms"
            primary = build_slice(arm_root / "primary-only", run_id=run_id, occupation=slug)
            primary_claims = json.loads((primary / "claims.json").read_text(encoding="utf-8"))
            artifacts: dict[str, dict[str, str]] = {}

            def record_arm(name: str, directory: Path) -> None:
                for relative, digest in self._hashes(directory).items():
                    path = directory / relative
                    artifacts[path.relative_to(run_dir).as_posix()] = {
                        "path": str(path.resolve()),
                        "sha256": digest,
                    }

            record_arm("primary-only", primary)
            candidate = primary
            comparison_path: Path | None = None
            comparison_sha: str | None = None
            status = "candidate_ready"
            eligible_arms = ["primary-only"]
            if config.challenge_enabled:
                challenger = build_slice(
                    arm_root / "primary-plus-challenge",
                    run_id=run_id,
                    occupation=slug,
                )
                claims = json.loads((challenger / "claims.json").read_text(encoding="utf-8"))
                extra = dict(claims[0])
                extra.update({
                    "id": f"clm_current_challenge_{slug.replace('-', '_')}",
                    "statement": "Human resources specialists administer onboarding and employee lifecycle processes.",
                })
                claims.append(extra)
                (challenger / "claims.json").write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
                record_arm("primary-plus-challenge", challenger)
                candidate = challenger
                status = "comparison_ready"
                eligible_arms = ["primary-only", "primary-plus-challenge"]
                passages = [{"source_id": f"source_{slug}", "sha256": "0" * 64, "text": "synthetic matched source"}]
                passages_sha = sha256_text(canonical_json(passages))
                comparison = {
                    "schema_version": "skills-vector-matched-comparison/1",
                    "comparison_id": run_id,
                    "occupation": slug,
                    "source_passages": passages,
                    "source_passages_sha256": passages_sha,
                    "arms": {},
                    "challenger": {
                        "nominated_source_backed_claim_ids": [],
                        "unsupported_refinements": [],
                        "challenge_findings": [{"kind": "challenge", "severity": "warning", "issue": "synthetic unresolved challenge"}],
                    },
                }
                for name, directory, rows in (
                    ("primary-only", primary, primary_claims),
                    ("primary-plus-challenge", challenger, claims),
                ):
                    comparison["arms"][name] = {
                        "arm": name,
                        "slice_path": str(directory.resolve()),
                        "artifact_sha256": self._hashes(directory),
                        "source_passages_sha256": passages_sha,
                        "source_passage_count": len(passages),
                        "eligible_for_review": True,
                        "policy_gate_reasons": [],
                        "claims": len(rows),
                        "known_cost_usd": primary_cost if name == "primary-only" else cost,
                        "wall_elapsed_ms": 1000,
                    }
                comparison_path = Path(config.evidence_root) / "receipts" / f"{run_id}-comparison.json"
                comparison_path.parent.mkdir(parents=True, exist_ok=True)
                comparison_bytes = (canonical_json(comparison) + "\n").encode("utf-8")
                comparison_path.write_bytes(comparison_bytes)
                comparison_sha = hashlib.sha256(comparison_bytes).hexdigest()

            receipt_path = Path(config.evidence_root) / "receipts" / f"{run_id}.json"
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt = {
                "receipt_schema": "market-run-receipt/2",
                "run_id": run_id,
                "occupation": slug,
                "status": status,
                "fixtures_used": False,
                "cost_usd_total": cost + 0.00001 if mismatch_cost_first and len(calls) == 1 else cost,
                "artifacts": artifacts,
            }
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            return {
                "run_id": run_id,
                "occupation": slug,
                "status": status,
                "run_dir": str(run_dir),
                "receipt": str(receipt_path),
                "comparison_receipt": str(comparison_path) if comparison_path else None,
                "comparison_sha256": comparison_sha,
                "eligible_arms": eligible_arms,
                "eligible": True,
                "challenge_enabled": config.challenge_enabled,
                "candidate_path": str(candidate.resolve()) if not config.challenge_enabled else None,
                "stats": {"model_calls": 2 if config.challenge_enabled else 1},
                "publication": "not_performed",
                "test_cost_usd": cost,
            }

        return run_research

    def _refresh(self, retention_path: Path, now: datetime) -> dict[str, Any]:
        return run_refresh_once(
            runtime=self.runtime,
            mission_budget=self.budget,
            evidence_root=self.evidence,
            release_root=self.release_root,
            checkpoint_path=self.checkpoint,
            retention_record=retention_path,
            policy=self.policy,
            now=now,
        )

    def test_retained_challenge_refreshes_all_four_once_and_exposes_unreviewed_status(self) -> None:
        retention = self._write_retention_record(retain_challenge=True)
        calls: list[dict[str, Any]] = []
        with patch("skills_vector.market.pipeline.run_research", self._producer(calls)):
            published = self._refresh(retention, self.now)
            skipped = self._refresh(retention, self.now + timedelta(minutes=30))
        self.assertEqual(published["status"], "published", published)
        self.assertEqual(published["provider_calls"], 8)
        self.assertEqual([row["occupation"] for row in calls], list(FIXED_ROLES))
        self.assertTrue(all(row["challenge_enabled"] is True for row in calls))
        self.assertEqual(skipped["status"], "skipped_not_due")
        self.assertEqual(skipped["provider_calls"], 0)
        self.assertEqual(len(calls), 4)

        release = CatalogStore(self.release_root).release()
        assert release is not None
        self.assertNotEqual(release.release_id, self.initial_current_id)
        self.assertEqual([row["slug"] for row in release.occupations], list(FIXED_ROLES))
        policy = release.manifest["publication_policy"]
        self.assertEqual(policy["selected_stage"], "primary-plus-challenge")
        self.assertIs(policy["human_reviewed"], False)
        self.assertEqual(policy["current_challenge_status"], "unresolved_not_independently_adjudicated")
        self.assertEqual(policy["current_challenge_finding_count"], 4)
        self.assertEqual(
            {key: policy[key] for key in ("policy_sha256", "cadence_hours", "min_postings", "max_postings", "max_boards")},
            {
                "policy_sha256": json.loads(self.checkpoint.read_text(encoding="utf-8"))["policy_sha256"],
                "cadence_hours": self.policy["cadence_hours"],
                "min_postings": self.policy["min_postings"],
                "max_postings": self.policy["max_postings"],
                "max_boards": self.policy["max_boards"],
            },
        )
        skip_rules = {
            row["rule"]: row
            for row in release.manifest["skip_rules"]
            if isinstance(row, dict) and isinstance(row.get("rule"), str)
        }
        minimum_rule = skip_rules["insufficient_admitted_postings"]
        self.assertEqual(minimum_rule["threshold"], self.policy["min_postings"])
        self.assertIn("block the full candidate", minimum_rule["action"])
        self.assertIn("last-good release", minimum_rule["action"])
        self.assertIn("no_matching_stored_claim", skip_rules)
        self.assertIn("no_trend_claims_without_comparable_periods", skip_rules)

    def test_primary_only_retention_never_runs_challenge_stage(self) -> None:
        retention = self._write_retention_record(retain_challenge=False)
        calls: list[dict[str, Any]] = []
        with patch("skills_vector.market.pipeline.run_research", self._producer(calls)):
            result = self._refresh(retention, self.now)
        self.assertEqual(result["status"], "published", result)
        self.assertEqual([row["occupation"] for row in calls], list(FIXED_ROLES))
        self.assertTrue(all(row["challenge_enabled"] is False for row in calls))
        release = CatalogStore(self.release_root).release()
        assert release is not None
        policy = release.manifest["publication_policy"]
        self.assertEqual(policy["selected_stage"], "primary-only")
        self.assertEqual(policy["current_challenge_status"], "not_run_primary_only_retained_stage")

    def test_mismatched_cost_receipt_cannot_publish(self) -> None:
        retention = self._write_retention_record(retain_challenge=True)
        before = CatalogStore(self.release_root).release()
        assert before is not None
        calls: list[dict[str, Any]] = []
        with patch(
            "skills_vector.market.pipeline.run_research",
            self._producer(calls, mismatch_cost_first=True),
        ):
            result = self._refresh(retention, self.now)
        after = CatalogStore(self.release_root).release()
        assert after is not None
        self.assertEqual(result["status"], "failed")
        self.assertIn("run cost does not reconcile", result["error"])
        self.assertEqual(after.release_id, before.release_id)
        self.assertEqual(after.content_hash(), before.content_hash())
        self.assertEqual(len(calls), 1)

    def test_unknown_provider_cost_preserves_complete_last_good_release(self) -> None:
        retention = self._write_retention_record(retain_challenge=True)
        first_calls: list[dict[str, Any]] = []
        with patch("skills_vector.market.pipeline.run_research", self._producer(first_calls)):
            published = self._refresh(retention, self.now)
        self.assertEqual(published["status"], "published", published)
        before = CatalogStore(self.release_root).release()
        assert before is not None
        before_identity = {row["slug"]: row for row in before.occupations}

        failed_calls: list[dict[str, Any]] = []
        with patch("skills_vector.market.pipeline.run_research", self._producer(failed_calls, fail_first=True)):
            failed = self._refresh(retention, self.now + timedelta(hours=2))
        self.assertEqual(failed["status"], "failed")
        after = CatalogStore(self.release_root).release()
        assert after is not None
        self.assertEqual(after.release_id, before.release_id)
        self.assertEqual(after.content_hash(), before.content_hash())
        self.assertEqual({row["slug"]: row for row in after.occupations}, before_identity)
        self.assertEqual(len(failed_calls), 1)

    def test_foundation_cache_reuses_recent_bytes_but_never_caches_job_boards(self) -> None:
        cache = SourceEvidenceCache(self.evidence / "source-cache")
        foundation_url = "https://www.onetonline.org/link/summary/13-1071.00"
        board_url = "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true"
        calls: list[str] = []

        def transport(url: str):
            calls.append(url)
            return 200, {"content-type": "text/plain"}, url.encode("utf-8")

        with patch("skills_vector.market.refresh.urllib_transport", side_effect=transport):
            first = cache(foundation_url)
            reused = cache(foundation_url)
            board_first = cache(board_url)
            board_second = cache(board_url)
        self.assertEqual(first[2], reused[2])
        self.assertEqual(board_first[2], board_second[2])
        self.assertEqual(calls, [foundation_url, board_url, board_url])
        self.assertEqual(cache.reused[foundation_url]["sha256"], sha256_text(foundation_url))
        self.assertNotIn(board_url, cache.reused)


if __name__ == "__main__":
    unittest.main()
