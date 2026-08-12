from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from skills_vector.domain import EvidenceCategory, EvidenceSource, Role, RoleBriefRequest, validate_brief
from skills_vector.operating_loop import (
    InvestigationHandlers,
    ScoutCandidate,
    WeeklyDeltaScout,
    run_recruiter_investigation,
)
from skills_vector.persistence import BacklogState, InvestigationStore
from skills_vector.planning import AdaptivePlanner, InvestigationPlan, ResearchDepth
from skills_vector.runtime import AgentResult, CallableRuntime, DeterministicStubRuntime, select_runtime


class OperatingLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = InvestigationStore(Path(self.tempdir.name) / "investigations.sqlite")
        self.as_of = date(2026, 8, 11)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def source(
        self,
        source_id: str,
        category: EvidenceCategory,
        *,
        published_on: date | None = None,
    ) -> EvidenceSource:
        return EvidenceSource(
            source_id,
            category,
            f"Source {source_id}",
            f"Publisher {source_id}",
            f"https://example.com/{source_id}",
            published_on or self.as_of,
            datetime(2026, 8, 11, tzinfo=UTC),
        )

    def incorporate(self, source: EvidenceSource) -> None:
        item, created = self.store.enqueue_candidate(Role.RECRUITER, source.category, source, f"hash-{source.source_id}")
        self.assertTrue(created)
        self.store.transition_backlog(item.candidate_id, BacklogState.RESEARCHING)
        self.store.transition_backlog(item.candidate_id, BacklogState.INCORPORATED)

    def test_recruiter_run_persists_trail_and_pauses_with_private_draft(self) -> None:
        runtime = DeterministicStubRuntime()
        result = run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)

        self.assertEqual(result.paused_before, "human_review")
        self.assertTrue(result.draft.private)
        self.assertIsNone(result.draft.review)
        self.assertEqual(validate_brief(result.draft), ())
        self.assertEqual(self.store.run_record(result.run_id).status, "awaiting_human_review")
        self.assertEqual([event.sequence for event in result.events], list(range(1, len(result.events) + 1)))
        artifact_ids = {artifact.artifact_id for artifact in result.artifacts}
        self.assertTrue(artifact_ids)
        self.assertTrue(
            all(reference in artifact_ids for event in result.events for reference in event.artifact_ids)
        )
        self.assertTrue({"evidence", "claims", "role_brief"}.issubset({artifact.kind for artifact in result.artifacts}))
        called_nodes = {call.node for call in runtime.calls}
        self.assertTrue({step.node for step in result.plan.research}.issubset(called_nodes))
        self.assertTrue(set(result.plan.analysis_nodes).issubset(called_nodes))

    def test_second_run_reuses_backlog_without_identical_deep_retrieval(self) -> None:
        runtime = DeterministicStubRuntime()
        first = run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)
        deep_after_first = tuple(
            call.node for call in runtime.calls if call.node.startswith("research_") and call.depth == "deep"
        )
        second = run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)
        deep_after_second = tuple(
            call.node for call in runtime.calls if call.node.startswith("research_") and call.depth == "deep"
        )

        self.assertEqual(len(first.plan.research), 2)
        self.assertEqual(second.plan.research, ())
        self.assertEqual(deep_after_second, deep_after_first)
        self.assertEqual(
            {source.source_id for source in second.draft.sources},
            {source.source_id for source in first.draft.sources},
        )

    def test_planner_uses_deep_subset_for_stale_and_skips_fresh_backlog(self) -> None:
        stale = self.source("stale", EvidenceCategory.PUBLIC_LABOR_DATA, published_on=date(2025, 1, 1))
        self.incorporate(stale)
        planner = AdaptivePlanner()
        request = RoleBriefRequest(Role.RECRUITER, self.as_of)

        stale_plan = planner.plan(request, self.store.list_backlog(Role.RECRUITER), has_prior_brief=True)
        self.assertEqual(len(stale_plan.research), 2)
        self.assertTrue(all(step.depth is ResearchDepth.DEEP for step in stale_plan.research))
        self.assertLess(len(stale_plan.research), len(EvidenceCategory))

        fresh_store = InvestigationStore()
        try:
            source = self.source("fresh", EvidenceCategory.PUBLIC_LABOR_DATA)
            item, _ = fresh_store.enqueue_candidate(Role.RECRUITER, source.category, source, "fresh-hash")
            fresh_store.transition_backlog(item.candidate_id, BacklogState.RESEARCHING)
            fresh_store.transition_backlog(item.candidate_id, BacklogState.INCORPORATED)
            fresh_plan = planner.plan(request, fresh_store.list_backlog(Role.RECRUITER), has_prior_brief=True)
            self.assertEqual(fresh_plan.research, ())
        finally:
            fresh_store.close()

    def test_weekly_scout_only_enqueues_and_planner_can_select_candidates(self) -> None:
        runtime = DeterministicStubRuntime()
        existing = run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)
        events_before = self.store.events(existing.run_id)
        artifacts_before = self.store.artifacts(existing.run_id)
        source = self.source("weekly-delta", EvidenceCategory.CREDIBLE_REPORT)
        request = RoleBriefRequest(Role.RECRUITER, self.as_of)

        candidate_ids = WeeklyDeltaScout(self.store).enqueue(request, (ScoutCandidate(source),))
        plan = AdaptivePlanner().plan(request, self.store.list_backlog(Role.RECRUITER), has_prior_brief=True)

        self.assertEqual(len(candidate_ids), 1)
        self.assertEqual(self.store.backlog_item(candidate_ids[0]).state, BacklogState.QUEUED)
        self.assertEqual(self.store.events(existing.run_id), events_before)
        self.assertEqual(self.store.artifacts(existing.run_id), artifacts_before)
        self.assertTrue(existing.draft.private)
        selected_ids = {candidate_id for step in plan.research for candidate_id in step.candidate_ids}
        self.assertEqual(selected_ids, set(candidate_ids))
        self.assertTrue(all(step.depth is ResearchDepth.SHALLOW for step in plan.research))

    def test_event_artifacts_must_exist_and_belong_to_same_run(self) -> None:
        first = self.store.start_run(Role.RECRUITER)
        second = self.store.start_run(Role.RECRUITER)
        artifact_id = self.store.write_artifact(first.run_id, "test", "evidence", {"ok": True})

        with self.assertRaisesRegex(ValueError, "missing artifact"):
            self.store.append_event(first.run_id, "test", "completed", artifact_ids=("missing",))
        with self.assertRaisesRegex(ValueError, "another run"):
            self.store.append_event(second.run_id, "test", "completed", artifact_ids=(artifact_id,))
        self.assertEqual(self.store.events(first.run_id), ())
        self.assertEqual(self.store.events(second.run_id), ())

    def test_analysis_rejects_capabilities_outside_allowlist(self) -> None:
        runtime = DeterministicStubRuntime()
        record = self.store.start_run(Role.RECRUITER)
        request = RoleBriefRequest(Role.RECRUITER, self.as_of)
        source = self.source("analysis", EvidenceCategory.PUBLIC_LABOR_DATA)
        handlers = InvestigationHandlers(self.store, runtime, AdaptivePlanner())
        state = {
            "run_id": record.run_id,
            "request": request,
            "plan": InvestigationPlan((), ("invented_analysis",), "malicious plan"),
            "evidence": [source],
        }

        with self.assertRaisesRegex(ValueError, "unsupported analysis"):
            handlers.analyze(state)
        self.assertEqual(runtime.calls, [])

    def test_rejected_fingerprint_cannot_reenter_draft(self) -> None:
        rejected = self.source("rejected", EvidenceCategory.PUBLIC_LABOR_DATA)
        content_value = "\x1f".join(
            (rejected.url, rejected.publisher.casefold(), rejected.published_on.isoformat(), rejected.title)
        )
        item, _ = self.store.enqueue_candidate(
            Role.RECRUITER,
            rejected.category,
            rejected,
            hashlib.sha256(content_value.encode("utf-8")).hexdigest(),
        )
        self.store.transition_backlog(item.candidate_id, BacklogState.REJECTED)
        stub = DeterministicStubRuntime()

        def replay(request):
            if request.node == "research_public_labor_data":
                return AgentResult(evidence=(rejected,))
            return stub.run(request)

        result = run_recruiter_investigation(
            self.store,
            as_of=self.as_of,
            runtime=CallableRuntime(replay),
        )

        self.assertNotIn(rejected.source_id, {source.source_id for source in result.draft.sources})
        self.assertEqual(self.store.backlog_item(item.candidate_id).state, BacklogState.REJECTED)

    def test_scout_candidate_is_incorporated_without_duplicate_on_research(self) -> None:
        runtime = DeterministicStubRuntime()
        run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)
        source = self.source("weekly-delta", EvidenceCategory.CREDIBLE_REPORT)
        request = RoleBriefRequest(Role.RECRUITER, self.as_of)
        candidate_ids = WeeklyDeltaScout(self.store).enqueue(request, (ScoutCandidate(source),))
        self.assertEqual(len(candidate_ids), 1)

        result = run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)

        item = self.store.backlog_item(candidate_ids[0])
        self.assertEqual(item.state, BacklogState.INCORPORATED)
        credible_items = [
            i for i in self.store.list_backlog(Role.RECRUITER) if i.lens is EvidenceCategory.CREDIBLE_REPORT
        ]
        self.assertEqual(len(credible_items), 1)
        self.assertIn(source.source_id, {s.source_id for s in result.draft.sources})

    def test_research_reverts_researching_items_on_runtime_failure(self) -> None:
        runtime = DeterministicStubRuntime()
        run_recruiter_investigation(self.store, as_of=self.as_of, runtime=runtime)
        source = self.source("weekly-delta", EvidenceCategory.CREDIBLE_REPORT)
        request = RoleBriefRequest(Role.RECRUITER, self.as_of)
        candidate_ids = WeeklyDeltaScout(self.store).enqueue(request, (ScoutCandidate(source),))

        def fail_on_research(req):
            if req.node == "research_credible_reports":
                raise RuntimeError("simulated agent failure")
            return runtime.run(req)

        with self.assertRaisesRegex(RuntimeError, "simulated agent failure"):
            run_recruiter_investigation(
                self.store, as_of=self.as_of, runtime=CallableRuntime(fail_on_research)
            )

        item = self.store.backlog_item(candidate_ids[0])
        self.assertEqual(item.state, BacklogState.QUEUED)

    def test_runtime_selection_is_offline_by_default_and_omp_is_swappable(self) -> None:
        with patch.dict("os.environ", {}, clear=True), patch("importlib.util.find_spec", return_value=None):
            self.assertIsInstance(select_runtime(), DeterministicStubRuntime)
        stub = DeterministicStubRuntime()
        omp = CallableRuntime(stub.run, name="omp")
        self.assertIs(select_runtime("omp", omp_runtime=omp), omp)


if __name__ == "__main__":
    unittest.main()
