"""Active market producer boundaries: source evidence, DeepInfra, budget, and candidates."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_support import (  # noqa: E402
    FOUNDATION_TEXT,
    active_runtime_bundle,
    greenhouse_payload,
    make_candidate,
    sample_job,
    transport_for,
)
from skills_vector.budget import BudgetError  # noqa: E402
from skills_vector.market.agent import DeepInfraRunner, ResearchError  # noqa: E402
from skills_vector.market.core import CatalogStore  # noqa: E402
from skills_vector.market.fetch import fetch_url  # noqa: E402
from skills_vector.market.hosts import url_problem  # noqa: E402
from skills_vector.market.limits import RESEARCH_LIMITS  # noqa: E402
from skills_vector.market.pipeline import (  # noqa: E402
    ResearchConfig,
    ResearchRun,
    resolve_priority_links,
    run_research,
)
from skills_vector.market.release import validate_release  # noqa: E402
from skills_vector.market.sources import seniority_exclusion_reason, us_location_ok  # noqa: E402

FOUNDATION_URL = "https://www.onetonline.org/link/summary/13-1071.00"
BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true"
IC_TEXT = (
    "This is an individual contributor role with no direct reports. Independently own end-to-end onboarding workflows. "
    "Required: maintain accurate HRIS records for onboarding operations. Proficient in HRIS systems. "
    "Our employer operates in manufacturing. Clients include healthcare organizations. "
    "Hybrid work in Austin, TX. At least 3 years of experience."
)
MANAGER_TEXT = (
    "This people manager has 4 direct reports. Supervise a team of HR coordinators. "
    "Required: maintain accurate HRIS records for onboarding operations."
)
UNKNOWN_TEXT = (
    "Support people operations programs and maintain accurate HRIS records. "
    "Collaborate with the employee relations team. Reporting structure is not stated."
)
RECRUITING_MANAGER_TEXT = (
    "Talent Acquisition Manager role. Hire employees for open roles. 5 years managing a team. "
    "Required: maintain accurate HRIS records."
)
UNRELATED_TEXT = "Coordinate software delivery plans and stakeholder meetings for engineering programs."
JOBS = [
    sample_job("1", "HR Generalist", "Austin, TX", IC_TEXT),
    sample_job("2", "People Operations Manager", "Remote - US", MANAGER_TEXT),
    sample_job("3", "People Operations Specialist", "Remote - US", UNKNOWN_TEXT),
    sample_job("4", "Talent Acquisition Manager", "Remote - US", RECRUITING_MANAGER_TEXT),
    sample_job("5", "Technical Program Manager", "New York, NY", UNRELATED_TEXT),
]


class _HttpResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "_HttpResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, max_bytes: int = -1) -> bytes:
        return self.payload if max_bytes < 0 else self.payload[:max_bytes]


class MockDeepInfraHTTP:
    """HTTP-level fake: checks the active request body and returns provider receipts."""

    def __init__(self, *, mode: str = "valid") -> None:
        self.mode = mode
        self.requests: list[dict[str, Any]] = []

    def open(self, request, timeout: float) -> _HttpResponse:  # noqa: ANN001
        self.requests.append({"url": request.full_url, "headers": dict(request.header_items()), "timeout": timeout})
        if self.mode == "provider_failure":
            raise urllib.error.URLError("test provider unavailable")
        request_doc = json.loads(request.data)
        self.requests[-1]["model"] = request_doc["model"]
        self.requests[-1]["request_body"] = request_doc
        prompt = request_doc["messages"][1]["content"]
        output = self._response_for(prompt)
        response: dict[str, Any] = {
            "model": request_doc["model"],
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(output)},
            }],
        }
        if self.mode != "unknown_cost":
            response["usage"] = {"prompt_tokens": 400, "completion_tokens": 120, "total_tokens": 520}
        return _HttpResponse(json.dumps(response).encode("utf-8"))

    def _response_for(self, prompt: str) -> dict[str, Any]:
        if prompt.startswith("You are the discovery pass"):
            return {
                "foundations": [{"url": FOUNDATION_URL, "why": "official occupation foundation"}],
                "boards": [{"ats": "greenhouse", "token": "testco", "employer": "Testco", "why": "public employer listings"}],
                "search_terms": ["HR Generalist", "People Operations"],
            }
        if prompt.startswith("Admit only postings"):
            return self._admissions(prompt)
        if prompt.startswith("You are the evidence reconciliation pass"):
            return self._synthesis(prompt)
        if prompt.startswith("You are the learning-priority evidence-linking pass"):
            claims_text = prompt.split("Recorded claims from this run (the only valid claim_id values):\n", 1)[1]
            claims_text = claims_text.split("\n\nDraft learning priorities", 1)[0]
            priorities_text = prompt.split("Draft learning priorities already authored by the reconciliation pass (labels, outcomes, rationale and order are\nfixed; do not rewrite them):\n", 1)[1]
            priorities_text = priorities_text.split("\n\nFor every priority", 1)[0]
            claims = json.loads(claims_text)
            priorities = json.loads(priorities_text)
            demand_ids = [str(row["claim_id"]) for row in claims if row.get("claim_type") == "advertised_demand"]
            return {"links": [
                {"priority_id": row["priority_id"], "claim_ids": demand_ids[:1], "rationale": "This recorded demand claim supports the bounded learning priority."}
                for row in priorities
            ]}
        raise AssertionError(f"unexpected provider stage prompt: {prompt[:100]!r}")

    @staticmethod
    def _admissions(prompt: str) -> dict[str, Any]:
        rows = json.loads(prompt.split("POSTINGS:\n", 1)[1])
        admissions: list[dict[str, Any]] = []
        for row in rows:
            title = str(row["title"])
            text = str(row["text"])
            if title == "Technical Program Manager":
                admissions.append({"posting_id": row["posting_id"], "decision": "exclude", "reason": "No HR duties are stated."})
                continue
            common = {
                "posting_id": row["posting_id"],
                "decision": "admit",
                "reason": "The posting contains role-specific people operations duties.",
                "variant": None,
                "advertised_experience": (
                    ["At least 3 years of experience"] if "At least 3 years of experience" in text
                    else ["5 years managing a team"] if "5 years managing a team" in text
                    else []
                ),
                "context_dimensions": {
                    "employer_industry": {"value": "manufacturing", "quote": "Our employer operates in manufacturing"} if "Our employer operates in manufacturing" in text else {"value": None, "quote": ""},
                    "customer_industry": {"value": "healthcare", "quote": "Clients include healthcare organizations"} if "Clients include healthcare organizations" in text else {"value": None, "quote": ""},
                    "sales_segment": {"value": None, "quote": ""},
                    "work_context": {"value": "hybrid", "quote": "Hybrid work in Austin, TX"} if "Hybrid work in Austin, TX" in text else {"value": None, "quote": ""},
                    "employer_size": {"value": None, "quote": ""},
                    "employer_stage": {"value": None, "quote": ""},
                },
                "expectations": [
                    {
                        "source_wording": "maintain accurate HRIS records",
                        "dimension": "task",
                        "basis": "employer_requirement",
                        "proficiency": "not_stated",
                        "proficiency_quote": "",
                    }
                ],
                "excerpt": "maintain accurate HRIS records" if "maintain accurate HRIS records" in text else "",
            }
            if title == "HR Generalist":
                common.update({
                    "work_level": "individual_contributor",
                    "work_level_reason": "The posting explicitly describes an individual contributor with no direct reports.",
                    "work_level_quote": "individual contributor role with no direct reports",
                    "people_management_quote": "",
                    "responsibility_band": "independent_ic",
                    "responsibility_reason": "The role explicitly owns end-to-end workflow work independently.",
                    "responsibility_quote": "Independently own end-to-end onboarding workflows",
                    "expectations": [
                        *common["expectations"],
                        {
                            "source_wording": "HRIS systems",
                            "dimension": "tool",
                            "basis": "employer_requirement",
                            "proficiency": "explicitly_stated",
                            "proficiency_quote": "Proficient in HRIS systems",
                        },
                        {
                            "source_wording": "At least 3 years of experience",
                            "dimension": "knowledge",
                            "basis": "employer_requirement",
                            "proficiency": "not_stated",
                            "proficiency_quote": "",
                        },
                        {
                            "source_wording": "invented leadership capability phrase",
                            "dimension": "capability",
                            "basis": "employer_requirement",
                            "proficiency": "not_stated",
                            "proficiency_quote": "",
                        },
                    ],
                })
            elif title == "People Operations Manager":
                common.update({
                    "work_level": "people_manager",
                    "work_level_reason": "The source identifies direct reports and team supervisory duties.",
                    "work_level_quote": "direct reports",
                    "people_management_quote": "direct reports",
                    "responsibility_band": "people_management",
                    "responsibility_reason": "The role explicitly supervises HR coordinators.",
                    "responsibility_quote": "Supervise a team of HR coordinators",
                })
            elif title == "Talent Acquisition Manager":
                common.update({
                    "work_level": "people_manager",
                    "work_level_reason": "Recruiting and years establish team leadership.",
                    "work_level_quote": "5 years managing a team",
                    "people_management_quote": "Hire employees for open roles",
                    "responsibility_band": "people_management",
                    "responsibility_reason": "The years token describes managerial responsibility.",
                    "responsibility_quote": "5 years managing a team",
                })
            else:
                common.update({
                    "work_level": "unknown",
                    "work_level_reason": "The source does not establish reporting structure.",
                    "work_level_quote": "",
                    "people_management_quote": "",
                    "responsibility_band": "unknown",
                    "responsibility_reason": "The source does not establish a responsibility level.",
                    "responsibility_quote": "",
                })
            admissions.append(common)
        return {"admissions": admissions}

    def _synthesis(self, prompt: str) -> dict[str, Any]:
        foundation_text = prompt.split("OFFICIAL FOUNDATION PASSAGE:\n", 1)[1]
        foundation_rows = json.loads(foundation_text.split("\n\nADMITTED POSTINGS AND DISTINCT EXPECTATION ROWS:\n", 1)[0])
        posting_rows = json.loads(prompt.split("ADMITTED POSTINGS AND DISTINCT EXPECTATION ROWS:\n", 1)[1])
        source_id = foundation_rows[0]["source_id"]
        if not posting_rows:
            return {"foundation_claims": [], "demand_claims": [], "learning_priorities": []}
        task_expectations = [
            (posting, expected)
            for posting in posting_rows
            for expected in posting.get("expectations", [])
            if expected.get("dimension") == "task" and expected.get("basis") == "employer_requirement"
        ]
        topic_source = task_expectations[0][0]["source_id"]
        topic_quote = task_expectations[0][1]["source_wording"]
        demand_quote = "the posting states that maintain accurate HRIS records" if self.mode == "tampered_quote" else topic_quote
        return {
            "foundation_claims": [{
                "statement": "Human resources specialists administer employee lifecycle processes.",
                "quote": "administer employee lifecycle processes",
                "source_id": source_id,
                "dimension": "task",
                "confidence": "bounded",
            }],
            "demand_claims": [{
                "topic_label": "HRIS operations",
                "signal": "maintaining accurate HRIS records",
                "detail": "The admitted source states this task in its own wording.",
                "posting_ids": [row["posting_id"] for row in posting_rows],
                "expectation_ids": [expected["expectation_id"] for _, expected in task_expectations],
                "dimension": "task",
                "basis": "employer_requirement",
                "quote": demand_quote,
                "source_id": topic_source,
                "confidence": "bounded",
            }],
            "learning_priorities": [{
                "label": "HRIS operations",
                "learning_outcome": "Maintain accurate HRIS records in source-backed people operations workflows.",
                "rationale": "The recorded task evidence supports this bounded practice priority.",
                "uncertainty": (
                    "The sample is small and does not establish prevalence."
                    if self.mode == "honest_disclaimer"
                    else "Most employers require HRIS proficiency; demand is trending upward."
                    if self.mode == "unsupported_market_assertion"
                    else "A small admitted sample does not measure proficiency."
                ),
                "confidence": "low",
                "basis": "advertised_demand",
                "topic_label": "HRIS operations",
                "search_terms": ["maintain accurate HRIS records"],
            }],
        }


class PipelineBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *, mode: str = "valid", run_id: str = "run_active_pipeline"):
        runtime, mission_budget = active_runtime_bundle(self.tmp / f"operator-{run_id}")
        provider = MockDeepInfraHTTP(mode=mode)
        release_root = self.tmp / f"release-{run_id}"
        config = ResearchConfig(
            occupation="hr-generalist",
            evidence_root=self.tmp / f"evidence-{run_id}",
            release_root=release_root,
            runtime=runtime,
            mission_budget=mission_budget,
            min_postings=3,
            max_postings=5,
            max_boards=1,
            transport=transport_for(greenhouse_payload(JOBS), FOUNDATION_TEXT.encode("utf-8")),
            run_id=run_id,
            challenge_enabled=False,
        )
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "unit-test-only-not-a-credential"}):
            with patch("skills_vector.market.agent.urllib.request.build_opener", return_value=provider):
                result = run_research(config)
        return config, mission_budget, provider, result

    def test_url_allowlist_and_fetch_retry_bound_fail_closed(self) -> None:
        self.assertIsNotNone(url_problem("http://boards-api.greenhouse.io/v1/boards/testco/jobs"))
        self.assertIsNotNone(url_problem("https://evil.example/v1/boards/testco/jobs"))
        self.assertIsNotNone(url_problem("https://boards-api.greenhouse.io/v1/boards/testco/../../etc"))
        self.assertIsNone(url_problem(BOARD_URL))
        calls = 0

        def failing_transport(_url: str):
            nonlocal calls
            calls += 1
            raise OSError("connection refused")

        refused = fetch_url("https://evil.example/v1/boards/testco/jobs", transport=failing_transport)
        self.assertFalse(refused.ok)
        self.assertEqual(calls, 0)
        retried = fetch_url(BOARD_URL, transport=failing_transport, retries=1)
        self.assertFalse(retried.ok)
        self.assertEqual(retried.attempts, 2)
        self.assertEqual(calls, 2)

    def test_role_title_priority_precedes_newer_unrelated_candidates_and_falls_back_fairly(self) -> None:
        runtime, mission_budget = active_runtime_bundle(self.tmp / "operator-allocation")
        config = ResearchConfig(
            occupation="hr-generalist",
            evidence_root=self.tmp / "allocation-evidence",
            release_root=self.tmp / "allocation-release",
            runtime=runtime,
            mission_budget=mission_budget,
            min_postings=1,
            max_postings=1,
        )
        run = ResearchRun(config)
        old_role = {
            "key": "old-role-hint", "employer": "Roleco", "bucket": "role",
            "title": "People Operations Specialist", "posted_at": "2024-01-01T00:00:00Z",
            "canonical_title_match": False, "role_title_hint_match": True,
        }
        new_unrelated = {
            "key": "new-program-manager", "employer": "Otherco", "bucket": "role",
            "title": "Technical Program Manager", "posted_at": "2026-10-01T00:00:00Z",
            "canonical_title_match": False, "role_title_hint_match": False,
        }
        selected, _ = run._allocate_candidates([new_unrelated, old_role])
        self.assertEqual([row["key"] for row in selected], ["old-role-hint"])

        run.config.max_postings = 2
        same_large_employer = {
            "key": "another-roleco", "employer": "Roleco", "bucket": "role",
            "title": "People Operations Specialist", "posted_at": "2026-09-01T00:00:00Z",
            "canonical_title_match": False, "role_title_hint_match": True,
        }
        another_employer = {
            "key": "other-roleco", "employer": "Otherco", "bucket": "role",
            "title": "HRIS Analyst", "posted_at": "2025-01-01T00:00:00Z",
            "canonical_title_match": False, "role_title_hint_match": True,
        }
        selected, _ = run._allocate_candidates([old_role, same_large_employer, another_employer])
        self.assertEqual({row["employer"] for row in selected}, {"Roleco", "Otherco"})
        run.config.max_postings = 1
        selected, _ = run._allocate_candidates(
            [new_unrelated, {**same_large_employer, "role_title_hint_match": False}]
        )
        self.assertEqual([row["key"] for row in selected], ["new-program-manager"])
        mission_budget.close()

    def test_canonical_title_matches_form_global_tier_before_other_hints(self) -> None:
        runtime, mission_budget = active_runtime_bundle(self.tmp / "operator-canonical-allocation")
        config = ResearchConfig(
            occupation="hr-generalist",
            evidence_root=self.tmp / "canonical-allocation-evidence",
            release_root=self.tmp / "canonical-allocation-release",
            runtime=runtime,
            mission_budget=mission_budget,
            min_postings=1,
            max_postings=5,
        )
        run = ResearchRun(config)
        candidates = [
            {
                "key": "spacex-canonical-new", "employer": "SpaceX", "bucket": "role",
                "title": "HR Generalist", "posted_at": "2026-09-30T00:00:00Z",
                "canonical_title_match": True, "role_title_hint_match": True,
            },
            {
                "key": "spacex-canonical-old", "employer": "SpaceX", "bucket": "role",
                "title": "HR Generalist II", "posted_at": "2024-01-01T00:00:00Z",
                "canonical_title_match": True, "role_title_hint_match": True,
            },
            {
                "key": "other-hris", "employer": "Otherco", "bucket": "role",
                "title": "HRIS Analyst", "posted_at": "2026-10-01T00:00:00Z",
                "canonical_title_match": False, "role_title_hint_match": True,
            },
            {
                "key": "intern-hr-tech", "employer": "Thirdco", "bucket": "role",
                "title": "Product Manager (HR Technology) Intern", "posted_at": "2026-10-01T00:00:00Z",
                "canonical_title_match": False, "role_title_hint_match": True,
            },
            {
                "key": "other-people-ops", "employer": "Fourthco", "bucket": "role",
                "title": "People Operations Specialist", "posted_at": "2026-10-01T00:00:00Z",
                "canonical_title_match": False, "role_title_hint_match": True,
            },
            {
                "key": "other-hrbp", "employer": "Fifthco", "bucket": "role",
                "title": "HR Business Partner", "posted_at": "2026-10-01T00:00:00Z",
                "canonical_title_match": False, "role_title_hint_match": True,
            },
        ]
        selected, _ = run._allocate_candidates(candidates)
        self.assertEqual(
            [row["key"] for row in selected[:2]],
            ["spacex-canonical-new", "spacex-canonical-old"],
        )
        self.assertEqual(len(selected), 5)
        mission_budget.close()

    def test_growth_allocation_preserves_variant_bucket_coverage(self) -> None:
        runtime, mission_budget = active_runtime_bundle(self.tmp / "operator-growth-allocation")
        config = ResearchConfig(
            occupation="growth-manager",
            evidence_root=self.tmp / "growth-allocation-evidence",
            release_root=self.tmp / "growth-allocation-release",
            runtime=runtime,
            mission_budget=mission_budget,
            min_postings=1,
            max_postings=3,
        )
        run = ResearchRun(config)
        candidates = [
            {
                "key": "product-growth-new", "employer": "Growthco", "bucket": "product-growth",
                "title": "Growth Manager", "posted_at": "2026-09-30T00:00:00Z",
                "canonical_title_match": True, "role_title_hint_match": True,
            },
            {
                "key": "product-growth-old", "employer": "Growthco", "bucket": "product-growth",
                "title": "Growth Manager", "posted_at": "2024-01-01T00:00:00Z",
                "canonical_title_match": True, "role_title_hint_match": True,
            },
            {
                "key": "growth-marketing", "employer": "Growthco", "bucket": "growth-marketing",
                "title": "Growth Manager", "posted_at": "2025-01-01T00:00:00Z",
                "canonical_title_match": True, "role_title_hint_match": True,
            },
            {
                "key": "sales-account-executive", "employer": "Growthco",
                "bucket": "sales-account-executive", "title": "Growth Manager",
                "posted_at": "2025-02-01T00:00:00Z",
                "canonical_title_match": True, "role_title_hint_match": True,
            },
        ]
        selected, _ = run._allocate_candidates(candidates)
        self.assertEqual(
            {row["bucket"] for row in selected},
            {"product-growth", "growth-marketing", "sales-account-executive"},
        )
        mission_budget.close()

    def test_location_seniority_and_priority_link_boundaries_remain_explicit(self) -> None:
        self.assertTrue(us_location_ok("San Francisco - Remote"))
        self.assertFalse(us_location_ok("London, United Kingdom"))
        self.assertIsNotNone(seniority_exclusion_reason("Principal HR Generalist", "hr-generalist"))
        self.assertIsNone(seniority_exclusion_reason("HR Generalist", "hr-generalist"))
        claims = [
            {"id": "demand", "occupation_slug": "hr-generalist", "claim_type": "advertised_demand", "variant": None},
            {"id": "foundation", "occupation_slug": "hr-generalist", "claim_type": "foundation", "variant": None},
        ]
        valid = resolve_priority_links(
            occupation="hr-generalist", claims=claims, priority_id="priority",
            basis="advertised_demand", link_row={"priority_id": "priority", "claim_ids": ["demand", "unknown", "foundation"], "rationale": "bounded"},
        )
        self.assertEqual(valid["claim_ids"], ["demand"])
        self.assertTrue(any(row["kind"] == "invalid_priority_link" for row in valid["disagreements"]))

    def test_active_pipeline_keeps_verified_manager_and_unknown_rows_and_freezes_candidate_only(self) -> None:
        config, budget, provider, result = self._run()
        try:
            self.assertEqual(result["status"], "candidate_ready", result)
            self.assertTrue(result["eligible"])
            self.assertIn("primary-only", result["eligible_arms"])
            candidate = Path(result["candidate_path"])
            postings = json.loads((candidate / "postings.json").read_text(encoding="utf-8"))
            by_title = {row["title"]: row for row in postings}
            self.assertEqual(len(postings), 4)
            self.assertEqual(by_title["HR Generalist"]["work_level"], "individual_contributor")
            manager = by_title["People Operations Manager"]
            self.assertEqual(manager["work_level"], "people_manager")
            self.assertEqual(manager["responsibility_band"], "people_management")
            self.assertIn("Supervise a team of HR coordinators", manager["responsibility_evidence"]["quote"])
            unknown = by_title["People Operations Specialist"]
            self.assertEqual(unknown["work_level"], "unknown")
            self.assertEqual(unknown["responsibility_band"], "unknown")
            self.assertTrue(unknown["work_level_reason"])
            recruiting_manager = by_title["Talent Acquisition Manager"]
            self.assertEqual(recruiting_manager["work_level"], "unknown")
            self.assertEqual(recruiting_manager["responsibility_band"], "unknown")
            self.assertEqual(recruiting_manager["advertised_experience"]["value"], ["5 years managing a team"])
            self.assertEqual(validate_release(make_candidate(candidate, self.tmp / "validation-work")), [])
            hr_expectations = by_title["HR Generalist"]["expectations"]
            self.assertTrue(any(row["proficiency"] == "explicitly_stated" and row["proficiency_quote"] == "Proficient in HRIS systems" for row in hr_expectations))
            years = next(
                row for row in hr_expectations
                if row["source_wording"] == "At least 3 years of experience"
            )
            self.assertEqual(years["dimension"], "experience")
            self.assertEqual(years["proficiency"], "not_stated")
            self.assertEqual(by_title["HR Generalist"]["responsibility_band"], "independent_ic")
            self.assertFalse(any("invented leadership" in row["source_wording"] for row in hr_expectations))
            disagreements = json.loads((candidate / "disagreements.json").read_text(encoding="utf-8"))
            self.assertTrue(any("not byte-verbatim" in str(row.get("issue") or "") for row in disagreements))
            excluded = json.loads((candidate / "exclusions.json").read_text(encoding="utf-8"))
            self.assertTrue(any("Technical Program Manager" in str(row.get("title") or "") for row in excluded))
            self.assertEqual(provider.requests[0]["url"], "https://api.deepinfra.com/v1/openai/chat/completions")
            self.assertEqual(provider.requests[0]["model"], config.runtime.model_for("primary"))
            self.assertTrue(all(row["url"] == "https://api.deepinfra.com/v1/openai/chat/completions" for row in provider.requests))
            self.assertFalse((config.release_root / "current.json").exists())
            self.assertEqual(result["publication"], "not_performed")
        finally:
            budget.close()

    def test_unverified_demand_quote_blocks_candidate_without_publishing(self) -> None:
        config, budget, _provider, result = self._run(mode="tampered_quote", run_id="run_bad_quote")
        try:
            self.assertEqual(result["status"], "candidate_blocked", result)
            self.assertFalse(result["eligible"])
            self.assertIsNone(CatalogStore(config.release_root).current_release_id())
            receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
            self.assertIn("learning priorities", str(receipt.get("error") or ""))
        finally:
            budget.close()

    def test_honest_sample_disclaimer_keeps_grounded_learning_priority(self) -> None:
        _config, budget, _provider, result = self._run(
            mode="honest_disclaimer", run_id="run_honest_disclaimer"
        )
        try:
            self.assertEqual(result["status"], "candidate_ready", result)
        finally:
            budget.close()

    def test_unsupported_market_assertion_blocks_learning_priority(self) -> None:
        config, budget, _provider, result = self._run(
            mode="unsupported_market_assertion", run_id="run_market_assertion"
        )
        try:
            self.assertEqual(result["status"], "candidate_blocked", result)
            self.assertFalse(result["eligible"])
            self.assertIsNone(CatalogStore(config.release_root).current_release_id())
        finally:
            budget.close()

    def _direct_runner(self, *, mode: str, allocation_usd: float = 1.0):
        runtime, budget = active_runtime_bundle(self.tmp / f"operator-{mode}", allocation_usd=allocation_usd)
        provider = MockDeepInfraHTTP(mode=mode)
        with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "unit-test-only-not-a-credential"}):
            runner = DeepInfraRunner(
                run_id=f"run_{mode}",
                run_dir=self.tmp / f"runner-{mode}",
                config=runtime,
                budget=budget,
            )
        return runtime, budget, provider, runner

    def test_unknown_provider_failure_stops_retries_and_preserves_hold(self) -> None:
        _runtime, budget, provider, runner = self._direct_runner(mode="provider_failure")
        try:
            with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "unit-test-only-not-a-credential"}):
                with patch("skills_vector.market.agent.urllib.request.build_opener", return_value=provider):
                    result = runner.call("probe", "Return one JSON object.")
            self.assertFalse(result.ok)
            attempts = budget.attempts_for_run("run_provider_failure")
            self.assertEqual(len(attempts), 1)
            self.assertTrue(all(row["status"] == "unknown" and row["actual_usd"] is None for row in attempts))
            self.assertIsNone(runner.cost_usd)
        finally:
            budget.close()

    def test_unknown_provider_usage_is_not_zero_and_prevents_mission_settlement(self) -> None:
        _runtime, budget, provider, runner = self._direct_runner(mode="unknown_cost")
        try:
            with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "unit-test-only-not-a-credential"}):
                with patch("skills_vector.market.agent.urllib.request.build_opener", return_value=provider):
                    result = runner.call("probe", "Return one JSON object.")
            self.assertFalse(result.ok)
            attempt = budget.attempts_for_run("run_unknown_cost")[0]
            self.assertEqual(attempt["status"], "unknown")
            self.assertIsNone(attempt["actual_usd"])
            self.assertIsNone(runner.cost_usd)
            with self.assertRaises(BudgetError):
                budget.settle_mission()
        finally:
            budget.close()

    def test_exhausted_mission_allocation_refuses_before_http(self) -> None:
        _runtime, budget, provider, runner = self._direct_runner(mode="valid", allocation_usd=0.000001)
        try:
            with patch.dict(os.environ, {"DEEPINFRA_API_KEY": "unit-test-only-not-a-credential"}):
                with patch("skills_vector.market.agent.urllib.request.build_opener", return_value=provider):
                    with self.assertRaises(ResearchError):
                        runner.call("probe", "Return one JSON object.")
            self.assertEqual(provider.requests, [])
            attempt = budget.attempts_for_run("run_valid")[0]
            self.assertEqual(attempt["status"], "refused")
            self.assertEqual(attempt["error_code"], "mission_allocation")
        finally:
            budget.close()


if __name__ == "__main__":
    unittest.main()
