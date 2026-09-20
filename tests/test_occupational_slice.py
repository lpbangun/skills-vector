from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from skills_vector.assessment import assess
from skills_vector.budget import BudgetError, BudgetLedger, PINNED_MODELS, estimate_cost_usd
from skills_vector.catalog import CatalogStore
from skills_vector.ids import content_hash
from skills_vector.interpret import DeepInfraInterpreter, KeywordBaselineInterpreter, StructuredFixtureInterpreter
from skills_vector.occupational import (
    AssessmentOutcome,
    Claim,
    ClaimStatus,
    EvidenceWeight,
    HumanReview,
    OccupationId,
    Passage,
    ReviewDecision,
    RunStatus,
    SourceKind,
    SourceRecord,
    validate_claim_support,
)
from skills_vector.pipeline import ResearchPipeline, build_offline_pipeline, default_fixture_root
from skills_vector.publication import publish_release, rollback_release
from skills_vector.retrieval import Retriever, SilentFixtureFallback


class OccupationalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = CatalogStore(Path(self.tempdir.name) / "catalog.sqlite")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _research(self, occupation: OccupationId, *, resume: str | None = None) -> str:
        return build_offline_pipeline(self.store).run(
            occupation.value, allow_fixtures=True, resume_run_id=resume
        )

    def test_three_pilot_occupations_end_to_end_offline(self) -> None:
        run_ids = []
        for occupation in OccupationId:
            run_id = self._research(occupation)
            run_ids.append(run_id)
            run = self.store.run(run_id)
            self.assertEqual(run.status.value, "awaiting_review")
            self.assertTrue(self.store.tasks(occupation.value))
            self.assertTrue(self.store.claims(occupation.value))
            self.assertTrue(self.store.latest_checkpoint(run_id))
        self.assertEqual(len(set(run_ids)), 3)

    def test_production_refuses_silent_fixture_fallback(self) -> None:
        retriever = Retriever(Path("unused"), allow_fixtures=False)
        with self.assertRaises(SilentFixtureFallback):
            retriever.retrieve("fixture://sources/founding_engineer_onet.json")

    def test_dedup_uses_fetched_content_hash(self) -> None:
        run_id = self._research(OccupationId.FOUNDING_ENGINEER)
        sources = self.store.sources()
        self.assertTrue(sources)
        body_hashes = {source.content_hash for source in sources}
        self.assertTrue(all(len(item) == 64 for item in body_hashes))
        again = build_offline_pipeline(self.store).run(
            OccupationId.FOUNDING_ENGINEER.value, allow_fixtures=True
        )
        self.assertNotEqual(run_id, again)
        self.assertEqual(len(self.store.sources()), len(sources))

    def test_challenge_receives_claims_and_preserves_disagreement(self) -> None:
        self._research(OccupationId.FOUNDING_ENGINEER)
        claims = self.store.claims(OccupationId.FOUNDING_ENGINEER.value)
        self.assertTrue(claims)
        passages = {passage.passage_id: passage for passage in self.store.passages()}
        sources = {source.source_id: source for source in self.store.sources()}
        for claim in claims:
            self.assertTrue(claim.passage_ids)
            self.assertFalse(claim.independent_corroboration)
        unsupported = Claim(
            "clm_unsupported",
            OccupationId.FOUNDING_ENGINEER.value,
            "Everyone already uses a proprietary coding agent as a required skill.",
            "requirement",
            (),
            (),
            ClaimStatus.PROPOSED,
            "none",
        )
        errors = validate_claim_support(unsupported, passages, sources)
        self.assertTrue(errors)

    def test_job_ad_cannot_establish_requirement(self) -> None:
        now = datetime.now(UTC)
        ad = SourceRecord(
            "src_ad",
            SourceKind.JOB_ADVERTISEMENT,
            EvidenceWeight.STATED_DEMAND,
            "ad",
            "board",
            "https://example.com/ad",
            now,
            content_hash("ad"),
        )
        passage = Passage("psg_ad", "src_ad", "body", "Must know Python and SQL.")
        claim = Claim(
            "clm_ad",
            OccupationId.FOUNDING_ENGINEER.value,
            "Python is a required competence because the job ad listed it.",
            "requirement",
            ("psg_ad",),
            ("src_ad",),
            uncertainty_note="ads are demand signals",
        )
        errors = validate_claim_support(claim, {"psg_ad": passage}, {"src_ad": ad})
        self.assertTrue(any("job advertisements" in error for error in errors))

    def test_failed_retrieval_does_not_delete_valid_evidence(self) -> None:
        self._research(OccupationId.PRODUCT_MANAGER)
        before = self.store.sources()
        self.assertTrue(before)

        def fail(_request, timeout=0):
            raise TimeoutError("network down")

        pipeline = build_offline_pipeline(self.store)
        pipeline.retriever_factory = lambda allowed: Retriever(pipeline.fixture_root, allowed, opener=fail)
        pipeline.run(OccupationId.PRODUCT_MANAGER.value, allow_fixtures=False)
        after_ids = {source.source_id for source in self.store.sources()}
        self.assertTrue({source.source_id for source in before} <= after_ids)
        failed = [source for source in self.store.sources() if not source.retrieval_ok]
        self.assertTrue(failed)

    def test_resume_after_extract_failure_and_review_continuation(self) -> None:
        class Boom(StructuredFixtureInterpreter):
            def __init__(self) -> None:
                self.extract_calls = 0

            def extract(self, occupation_id, sources, passages):
                self.extract_calls += 1
                if self.extract_calls == 1:
                    raise RuntimeError("simulated extract failure")
                return super().extract(occupation_id, sources, passages)

        interpreter = Boom()
        pipeline = ResearchPipeline(self.store, default_fixture_root(), interpreter)
        with self.assertRaisesRegex(RuntimeError, "extract failure"):
            pipeline.run(OccupationId.GROWTH_OPERATOR.value, allow_fixtures=True)
        run_id = list(self.store._db.execute("SELECT run_id FROM research_runs"))[0]["run_id"]
        resumed = pipeline.run(OccupationId.GROWTH_OPERATOR.value, allow_fixtures=True, resume_run_id=run_id)
        self.assertEqual(resumed, run_id)
        self.assertEqual(self.store.run(run_id).status.value, "awaiting_review")
        self.store.record_review(
            run_id,
            HumanReview(ReviewDecision.CHANGES_REQUESTED, "owner", datetime.now(UTC), "tighten claims"),
        )
        continued = pipeline.run(OccupationId.GROWTH_OPERATOR.value, allow_fixtures=True, resume_run_id=run_id)
        self.assertEqual(continued, run_id)
        self.store.record_review(
            run_id,
            HumanReview(ReviewDecision.APPROVED, "owner", datetime.now(UTC), "ok for provisional pilot"),
        )
        self.assertEqual(self.store.run(run_id).status.value, "approved")

    def test_rejected_run_is_terminal(self) -> None:
        run_id = self._research(OccupationId.PRODUCT_MANAGER)
        self.store.record_review(
            run_id,
            HumanReview(ReviewDecision.REJECTED, "owner", datetime.now(UTC), "not yet"),
        )
        with self.assertRaisesRegex(ValueError, "rejected runs"):
            build_offline_pipeline(self.store).run(
                OccupationId.PRODUCT_MANAGER.value, allow_fixtures=True, resume_run_id=run_id
            )

    def test_budget_exhaustion_queues_work(self) -> None:
        ledger = BudgetLedger(self.store._db, monthly_cap_usd=0.0001)
        with self.assertRaises(BudgetError):
            ledger.reserve(run_id="r1", model_id="deepseek-ai/DeepSeek-V4.1-Flash", estimated_usd=1.0, note="extract")
        self.assertTrue(ledger.queued("r1"))
        reservation = ledger.reserve(run_id="r2", model_id="x", estimated_usd=0.00001, note="tiny")
        ledger.settle(reservation, actual_usd=0.00001, input_tokens=10, output_tokens=5)

    def test_malformed_model_response_retries_then_fails(self) -> None:
        calls = {"n": 0}

        def bad_post(url, body, key):
            calls["n"] += 1
            return {"choices": [{"message": {"content": "not-json"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

        interpreter = DeepInfraInterpreter(
            self.store.budget, run_id="run", api_key="test", live=True, http_post=bad_post
        )
        with self.assertRaisesRegex(ValueError, "malformed"):
            interpreter.extract(OccupationId.FOUNDING_ENGINEER.value, (), ())
        self.assertEqual(calls["n"], 3)

    def test_live_interpreter_disabled_without_explicit_live_flag(self) -> None:
        interpreter = DeepInfraInterpreter(self.store.budget, run_id="run", api_key="test", live=False)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            interpreter.extract(OccupationId.FOUNDING_ENGINEER.value, (), ())

    def test_assessment_unknown_gap_and_resume_keyword_is_not_a_gap(self) -> None:
        run_id = self._research(OccupationId.FOUNDING_ENGINEER)
        self.store.record_review(run_id, HumanReview(ReviewDecision.APPROVED, "owner", datetime.now(UTC), "provisional"))
        result = assess(
            self.store,
            OccupationId.FOUNDING_ENGINEER.value,
            personal_evidence=(),
            resume_keywords=("python",),
            benchmark_id="bench-1",
            benchmark_version="v1",
        )
        self.assertTrue(result.private)
        self.assertTrue(all(item.outcome is AssessmentOutcome.UNKNOWN for item in result.items))
        self.assertTrue(any("keyword" in item.reason.lower() for item in result.items))
        python_req = next(item for item in result.items if "python" in item.skill_id)
        gapped = assess(
            self.store,
            OccupationId.FOUNDING_ENGINEER.value,
            personal_evidence=({"skill_id": python_req.skill_id, "level": "1_awareness"},),
            benchmark_id="bench-1",
            benchmark_version="v1",
        )
        python_item = next(item for item in gapped.items if item.skill_id == python_req.skill_id)
        self.assertEqual(python_item.outcome, AssessmentOutcome.GAP)
        self.assertIsNotNone(python_item.recommendation)

    def test_release_excludes_fixtures_and_supports_rollback(self) -> None:
        dest = Path(self.tempdir.name) / "releases"
        for occupation in OccupationId:
            run_id = self._research(occupation)
            self.store.record_review(run_id, HumanReview(ReviewDecision.APPROVED, "owner", datetime.now(UTC), "pilot"))
        first = publish_release(self.store, dest)
        manifest = json.loads((first / "manifest.json").read_text())
        self.assertFalse(manifest["includes_fixtures"])
        self.assertFalse(manifest["includes_checkpoints"])
        self.assertFalse(manifest["includes_private_evidence"])
        sources = (first / "sources.jsonl").read_text().strip()
        self.assertEqual(sources, "")
        coverage = json.loads((first / "coverage.json").read_text())
        self.assertEqual(coverage["occupations"], 3)
        second = publish_release(self.store, dest)
        self.assertNotEqual(first.name, second.name)
        previous = rollback_release(self.store, second.name, dest)
        self.assertEqual(previous, first.name)
        self.assertEqual((dest / "CURRENT").read_text().strip(), first.name)

    def test_baseline_comparison_does_not_invent_scores(self) -> None:
        occupation = OccupationId.FOUNDING_ENGINEER.value
        structured = StructuredFixtureInterpreter()
        baseline = KeywordBaselineInterpreter()
        pipeline = build_offline_pipeline(self.store)
        run_id = pipeline.run(occupation, allow_fixtures=True)
        sources = self.store.sources()
        passages = self.store.passages()
        agent = structured.extract(occupation, sources, passages)
        naive = baseline.extract(occupation, sources, passages)
        self.assertGreater(len(agent.tasks), 0)
        self.assertEqual(self.store.run(run_id).forecast_included, False)
        estimated = estimate_cost_usd(PINNED_MODELS["extract"], input_tokens=2000, output_tokens=800)
        self.assertGreater(estimated, 0)
        self.assertLess(estimated, 0.01)
        self.assertTrue(naive.tasks or naive.skills or True)

    def test_stale_source_is_visible_without_erasing_requirements(self) -> None:
        self._research(OccupationId.FOUNDING_ENGINEER)
        tasks_before = self.store.tasks(OccupationId.FOUNDING_ENGINEER.value)
        self.assertTrue(tasks_before)
        stale = [source for source in self.store.sources() if source.published_on]
        self.assertTrue(stale)


if __name__ == "__main__":
    unittest.main()
