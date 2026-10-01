"""Pipeline boundaries: allowlist, retry/budget caps, fail-closed identity, last-good release."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_support import (  # noqa: E402
    FOUNDATION_TEXT,
    ScriptedAgent,
    dedup_for,
    demand_claim_id,
    greenhouse_detail_payload,
    greenhouse_payload,
    requirement_id,
    routed_transport,
    sample_job,
    transport_for,
)

from skills_vector.market.agent import BudgetExceeded, CallBudget  # noqa: E402
from skills_vector.market.core import CatalogStore  # noqa: E402
from skills_vector.market.fetch import fetch_url  # noqa: E402
from skills_vector.market.hosts import url_problem  # noqa: E402
from skills_vector.market.limits import RESEARCH_LIMITS  # noqa: E402
from skills_vector.market.pipeline import (  # noqa: E402
    ResearchConfig,
    admission_prompt_text,
    resolve_priority_links,
    run_research,
)
from skills_vector.market.release import sha256_bytes, validate_release  # noqa: E402
from skills_vector.market.sources import seniority_exclusion_reason, us_location_ok  # noqa: E402

FOUNDATION_URL = "https://www.onetonline.org/link/summary/13-1071.00"
BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true"
JOB_ONE_DESC = "Own onboarding and offboarding workflows; maintain HRIS data and support employee relations."
JOB_TWO_DESC = "Support people operations programs and employee lifecycle administration across the US."
JOB_ONE_URL_TEMPLATE = "https://boards.greenhouse.io/{token}/jobs/1"
JOB_TWO_URL_TEMPLATE = "https://boards.greenhouse.io/{token}/jobs/2"


def scripted_responses(
    *,
    run_id: str = "run_test_pipeline",
    occupation: str = "hr-generalist",
    foundation_quote: str = "administer employee lifecycle processes",
    demand_quote: str = "maintain HRIS data",
    admit: bool = True,
    employer: str = "Testco",
    token: str = "testco",
    discovery_boards: list[dict[str, str]] | None = None,
    priorities: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    jobs = [
        sample_job("1", "HR Generalist", "Austin, TX", JOB_ONE_DESC, token=token),
        sample_job("2", "People Operations Specialist", "Remote - US", JOB_TWO_DESC, token=token),
    ]
    key_one = dedup_for(employer, "1", JOB_ONE_URL_TEMPLATE.format(token=token))
    key_two = dedup_for(employer, "2", JOB_TWO_URL_TEMPLATE.format(token=token))
    discovery = {
        "foundations": [{"url": FOUNDATION_URL, "why": "official summary"}],
        "boards": discovery_boards
        or [{"ats": "greenhouse", "token": token, "employer": employer, "why": "first-party postings"}],
        "search_terms": ["hr generalist", "people operations"],
    }
    admissions = {"admissions": []}
    for key, decision in ((key_one, "admit"), (key_two, "admit" if admit else "exclude")):
        admissions["admissions"].append(
            {
                "posting_id": key,
                "decision": decision,
                "reason": "matches role scope" if decision == "admit" else "outside scope",
                "variant": None,
                "seniority": "mid",
                "work_level": "individual_contributor",
                "work_level_reason": "Personally owns the work; no direct reports in the posting.",
                "people_management_quote": "",
                "skills": ["onboarding"] if key == key_one else ["employee lifecycle"],
                "excerpt": "Own onboarding and offboarding workflows" if key == key_one else "this phrase is not in the posting text",
            }
        )
    default_priorities = [
        {
            "label": "Employee lifecycle operations",
            "learning_outcome": "Run onboarding and offboarding workflows with auditable HRIS records.",
            "rationale": "Admitted postings assign lifecycle workflow ownership.",
            "uncertainty": "Small sample; ordering is evidence-based priority, not measured importance.",
            "confidence": "low",
            "basis": "advertised_demand",
            "topic_label": "Onboarding",
            "search_terms": ["onboarding"],
        }
    ]
    priority_rows = priorities if priorities is not None else default_priorities
    reconciliation = {
        "foundation_claims": [
            {"statement": "HR generalists keep employee lifecycle records and coordinate onboarding.", "quote": foundation_quote, "source_id": "", "confidence": "bounded"}
        ],
        "demand_claims": [
            {
                "topic_label": "Onboarding",
                "signal": "onboarding workflow ownership",
                "detail": "Two admitted postings describe onboarding workflow ownership.",
                "posting_ids": [key_one],
                "quote": demand_quote,
                "source_id": "",
                "confidence": "bounded",
            }
        ],
        "learning_priorities": priority_rows,
    }
    if links is None:
        links = [
            {
                "priority_id": requirement_id(
                    run_id, occupation, index, str(row.get("label") or ""), str(row.get("learning_outcome") or "")
                ),
                "claim_ids": [demand_claim_id(run_id, occupation, "Onboarding", [key_one])],
                "rationale": "Agent-selected recorded demand claim supports this priority.",
            }
            for index, row in enumerate(priority_rows)
        ]
    return {
        "discovery": json.dumps(discovery),
        "admission": json.dumps(admissions),
        "reconciliation": json.dumps(reconciliation),
        "evidence_linking": json.dumps({"links": links}),
        "challenge": json.dumps({"challenges": [], "verdict": "approve"}),
    }


class PipelineBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.overlay = self.tmp / "runtime-overlay.yml"
        self.overlay.write_text("retry:\n  modelFallback: false\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _config(
        self,
        *,
        responses: dict[str, str],
        publish: bool = True,
        min_postings: int = 2,
        fallback: bool = False,
        run_id: str = "run_test_pipeline",
        transport=None,
        occupation: str = "hr-generalist",
        max_postings: int | None = None,
    ):
        jobs = greenhouse_payload(
            [
                sample_job("1", "HR Generalist", "Austin, TX", JOB_ONE_DESC),
                sample_job("2", "People Operations Specialist", "Remote - US", JOB_TWO_DESC),
            ]
        )
        release_root = self.tmp / "release"
        release_root.mkdir(exist_ok=True)
        pointer = release_root / "current.json"
        if not pointer.exists():
            pointer.write_text(json.dumps({"schema_version": 1, "current": None, "releases": []}), encoding="utf-8")
        return ResearchConfig(
            occupation=occupation,
            evidence_root=self.tmp / "evidence",
            release_root=release_root,
            overlay=self.overlay,
            publish=publish,
            min_postings=min_postings,
            max_postings=max_postings if max_postings is not None else 60,
            runner=ScriptedAgent(responses, fallback=fallback),
            transport=transport or transport_for(jobs, FOUNDATION_TEXT.encode("utf-8")),
            run_id=run_id,
        )

    # -- allowlist and transport bounds ---------------------------------

    def test_allowlist_refuses_offlist_and_insecure_urls(self) -> None:
        self.assertIsNotNone(url_problem("http://boards-api.greenhouse.io/v1/boards/testco/jobs"))
        self.assertIsNotNone(url_problem("https://evil.example/v1/boards/testco/jobs"))
        self.assertIsNotNone(url_problem("https://boards-api.greenhouse.io/v1/boards/testco/../../etc"))
        self.assertIsNotNone(url_problem("https://198.51.100.7/v1/boards/testco/jobs"))
        self.assertIsNone(url_problem(BOARD_URL))
        self.assertIsNone(url_problem(FOUNDATION_URL))

    def test_fetch_retries_are_bounded(self) -> None:
        calls = {"count": 0}

        def failing_transport(url: str):
            calls["count"] += 1
            raise OSError("connection refused")

        result = fetch_url(BOARD_URL, transport=failing_transport, retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(RESEARCH_LIMITS["infrastructure_retries"], 2)

    def test_model_call_budget_is_fixed(self) -> None:
        budget = CallBudget(limit=2)
        self.assertEqual(budget.reserve(), 1)
        self.assertEqual(budget.reserve(), 2)
        with self.assertRaises(BudgetExceeded):
            budget.reserve()

    # -- fail-closed pipeline -------------------------------------------

    def test_unverified_excerpts_are_dropped_and_disagreement_recorded(self) -> None:
        config = self._config(responses=scripted_responses())
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        release_root = config.release_root
        release = CatalogStore(release_root).release()
        self.assertIsNotNone(release)
        self.assertEqual(len(release.postings), 2)
        published_extracts = "\n".join(path.read_text(encoding="utf-8") for path in (release.root / "extracts").glob("*.txt"))
        self.assertNotIn("this phrase is not in the posting text", published_extracts)
        disagreements = json.loads((release.root / "disagreements.json").read_text(encoding="utf-8"))
        self.assertTrue(any(row["kind"] == "quote_verification" for row in disagreements))

        def extract_kind(path: Path) -> str:
            for line in path.read_text(encoding="utf-8").splitlines()[:4]:
                if line.startswith("# source_type"):
                    return line.split()[-1]
            return ""

        extracts = list((release.root / "extracts").glob("*.txt"))
        board_extracts = [path for path in extracts if extract_kind(path) == "job-board"]
        foundation_extracts = [path for path in extracts if extract_kind(path) == "foundation"]
        self.assertTrue(any("maintain HRIS data" in path.read_text(encoding="utf-8") for path in board_extracts))
        self.assertFalse(any("maintain HRIS data" in path.read_text(encoding="utf-8") for path in foundation_extracts))
        self.assertEqual(validate_release(release.root), [])
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertFalse(receipt["fixtures_used"])
        self.assertEqual(receipt["execution_context"], "local-runtime")
        self.assertEqual(receipt["provider"], "opencode-go")
        self.assertEqual(receipt["model"], "deepseek-v4.1-flash")
        self.assertFalse(receipt["model_fallback"])
        self.assertEqual(len(receipt["model_calls"]), 5)
        for record in receipt["session_records"]:
            self.assertTrue(Path(record["path"]).is_file())
        answer = CatalogStore(release_root).query("onboarding")
        self.assertEqual(answer["status"], "cited_evidence")

    def test_tampered_quote_blocks_publish_and_keeps_last_good(self) -> None:
        first = run_research(self._config(responses=scripted_responses(run_id="run_test_first"), run_id="run_test_first"))
        self.assertEqual(first["status"], "published")
        pointer_before = (self.tmp / "release" / "current.json").read_bytes()

        second = run_research(
            self._config(
                responses=scripted_responses(
                    run_id="run_test_second",
                    foundation_quote="a sentence that was never retrieved",
                    demand_quote="maintained HRIS",
                ),
                run_id="run_test_second",
            )
        )
        self.assertNotEqual(second["status"], "published")
        self.assertEqual((self.tmp / "release" / "current.json").read_bytes(), pointer_before)
        receipt = json.loads(Path(second["receipt"]).read_text(encoding="utf-8"))
        self.assertIn("foundation claims", receipt["error"])
        store = CatalogStore(self.tmp / "release")
        self.assertEqual(store.current_release_id(), first["release_id"])

    def test_fallback_session_fails_closed(self) -> None:
        result = run_research(self._config(responses=scripted_responses(), fallback=True, run_id="run_test_fallback"))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("model identity verification", result["error"])
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["model_calls"][0]["problems"])
        self.assertFalse((self.tmp / "release" / "releases").exists())

    def test_thin_sample_never_publishes(self) -> None:
        result = run_research(
            self._config(responses=scripted_responses(run_id="run_test_thin", admit=False), run_id="run_test_thin")
        )
        self.assertEqual(result["status"], "validated_not_published")
        self.assertIn("admitted postings", result["error"])
        self.assertFalse((self.tmp / "release" / "releases").exists())

    # -- scope admission (work level) ------------------------------------

    def test_people_manager_scope_is_excluded_despite_ic_like_title_and_quota(self) -> None:
        manager_desc = (
            "Recruit, develop and lead a team of Account Executives while carrying a personal quota "
            "and owning enterprise account relationships."
        )
        ic_desc = "Own a personal quota, prospect and close new business across the United States."
        board = greenhouse_payload(
            [
                sample_job("1", "Account Executive", "Austin, TX", ic_desc, token="testco"),
                sample_job("2", "Account Executive", "Remote - US", manager_desc, token="testco"),
            ]
        )
        key_ic = dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco"))
        key_manager = dedup_for("Testco", "2", JOB_TWO_URL_TEMPLATE.format(token="testco"))
        responses = scripted_responses(run_id="run_test_manager", occupation="account-executive")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": key_ic,
                        "decision": "admit",
                        "reason": "in-scope closing role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally carries quota and closes deals; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["quota"],
                        "excerpt": "Own a personal quota, prospect and close new business",
                    },
                    {
                        "posting_id": key_manager,
                        "decision": "admit",
                        "reason": "quota-carrying sales leadership role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "people_manager",
                        "work_level_reason": "Recruits, develops and leads a team of Account Executives.",
                        "people_management_quote": "Recruit, develop and lead a team of Account Executives",
                        "skills": ["quota"],
                        "excerpt": "Recruit, develop and lead a team of Account Executives",
                    },
                ]
            }
        )
        config = self._config(
            responses=responses,
            transport=routed_transport({"boards/testco/jobs": board}, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_manager",
            occupation="account-executive",
            min_postings=1,
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        release = CatalogStore(config.release_root).release()
        self.assertIsNotNone(release)
        self.assertEqual([posting["dedup_key"] for posting in release.postings], [key_ic])
        posting = release.postings[0]
        self.assertEqual(posting["work_level"], "individual_contributor")
        self.assertEqual(posting["people_management_quote"], "")
        self.assertTrue(posting["work_level_reason"])
        exclusions = json.loads((release.root / "exclusions.json").read_text(encoding="utf-8"))
        manager_rows = [row for row in exclusions if row["dedup_key"] == key_manager]
        self.assertEqual(len(manager_rows), 1)
        self.assertEqual(validate_release(release.root), [])

    def test_missing_or_unknown_work_level_decisions_are_excluded(self) -> None:
        key_one = dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco"))
        key_two = dedup_for("Testco", "2", JOB_TWO_URL_TEMPLATE.format(token="testco"))
        responses = scripted_responses(run_id="run_test_scope_missing")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": key_one,
                        "decision": "admit",
                        "reason": "in-scope HR generalist role",
                        "variant": None,
                        "seniority": "mid",
                        "skills": ["onboarding"],
                        "excerpt": "Own onboarding and offboarding workflows",
                    },
                    {
                        "posting_id": key_two,
                        "decision": "admit",
                        "reason": "in-scope people operations role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "unknown",
                        "work_level_reason": "Posting text does not establish direct-report ownership.",
                        "people_management_quote": "",
                        "skills": ["employee lifecycle"],
                        "excerpt": "employee lifecycle",
                    },
                ]
            }
        )
        result = run_research(
            self._config(responses=responses, run_id="run_test_scope_missing", min_postings=2)
        )
        self.assertEqual(result["status"], "validated_not_published", result)
        self.assertFalse((self.tmp / "release" / "releases").exists())

    def test_individual_contributor_growth_manager_title_is_not_excluded_by_manager(self) -> None:
        board = greenhouse_payload(
            [
                sample_job(
                    "1", "Growth Manager", "Remote - US",
                    "Own onboarding activation experiments and retention loops.", token="testco",
                ),
                sample_job(
                    "2", "Lifecycle Marketing Manager", "Remote - US",
                    "Run lifecycle email campaigns and paid acquisition tests.", token="testco",
                ),
                sample_job("3", "Sales Account Executive", "Austin, TX", "Carry quota and close new business.", token="testco"),
            ]
        )
        key_one = dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco"))
        key_two = dedup_for("Testco", "2", JOB_TWO_URL_TEMPLATE.format(token="testco"))
        key_three = dedup_for("Testco", "3", "https://boards.greenhouse.io/testco/jobs/3")
        responses = scripted_responses(run_id="run_test_growth_title", occupation="growth-manager")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": key_one,
                        "decision": "admit",
                        "reason": "in-scope product growth role",
                        "variant": "product-growth",
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally owns activation experiments; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["onboarding"],
                        "excerpt": "Own onboarding activation experiments",
                    },
                    {
                        "posting_id": key_two,
                        "decision": "admit",
                        "reason": "in-scope growth marketing role",
                        "variant": "growth-marketing",
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally runs lifecycle and paid campaigns; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["lifecycle"],
                        "excerpt": "Run lifecycle email campaigns",
                    },
                    {
                        "posting_id": key_three,
                        "decision": "admit",
                        "reason": "in-scope closing role",
                        "variant": "sales-account-executive",
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally carries quota and closes; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["quota"],
                        "excerpt": "Carry quota and close new business",
                    },
                ]
            }
        )
        config = self._config(
            responses=responses,
            transport=routed_transport({"boards/testco/jobs": board}, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_growth_title",
            occupation="growth-manager",
            min_postings=3,
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        self.assertIsNone(seniority_exclusion_reason("Growth Manager", "growth-manager"))
        release = CatalogStore(config.release_root).release()
        self.assertIsNotNone(release)
        manager_posting = next(posting for posting in release.postings if posting["title"] == "Growth Manager")
        self.assertEqual(manager_posting["dedup_key"], key_one)
        self.assertEqual(manager_posting["work_level"], "individual_contributor")

    def test_advising_managers_is_not_direct_report_ownership(self) -> None:
        advising_desc = "Advise managers on employee relations and coach people leaders through policy questions."
        board = greenhouse_payload(
            [
                sample_job("1", "HR Business Partner", "Austin, TX", advising_desc, token="testco"),
                sample_job("2", "People Operations Specialist", "Remote - US", JOB_TWO_DESC, token="testco"),
            ]
        )
        key_one = dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco"))
        key_two = dedup_for("Testco", "2", JOB_TWO_URL_TEMPLATE.format(token="testco"))
        responses = scripted_responses(run_id="run_test_advising")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": key_one,
                        "decision": "admit",
                        "reason": "in-scope HR business partner role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Advises managers on policy; owns no direct reports.",
                        "people_management_quote": "",
                        "skills": ["employee relations"],
                        "excerpt": "Advise managers on employee relations",
                    },
                    {
                        "posting_id": key_two,
                        "decision": "admit",
                        "reason": "in-scope people operations role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally supports lifecycle administration; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["employee lifecycle"],
                        "excerpt": "employee lifecycle administration across the US",
                    },
                ]
            }
        )
        config = self._config(
            responses=responses,
            transport=routed_transport({"boards/testco/jobs": board}, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_advising",
            min_postings=2,
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        release = CatalogStore(config.release_root).release()
        self.assertIsNotNone(release)
        titles = sorted(posting["title"] for posting in release.postings)
        self.assertEqual(titles, ["HR Business Partner", "People Operations Specialist"])
        self.assertTrue(all(posting["work_level"] == "individual_contributor" for posting in release.postings))

    def test_invalid_scope_admission_preserves_last_good_release(self) -> None:
        first = run_research(
            self._config(responses=scripted_responses(run_id="run_test_scope_first"), run_id="run_test_scope_first")
        )
        self.assertEqual(first["status"], "published")
        pointer_before = (self.tmp / "release" / "current.json").read_bytes()

        responses = scripted_responses(run_id="run_test_scope_second")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco")),
                        "decision": "admit",
                        "reason": "claims to be in scope",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "people_manager",
                        "work_level_reason": "Owns direct reports according to the posting text.",
                        "people_management_quote": "",
                        "skills": ["onboarding"],
                        "excerpt": "Own onboarding and offboarding workflows",
                    },
                    {
                        "posting_id": dedup_for("Testco", "2", JOB_TWO_URL_TEMPLATE.format(token="testco")),
                        "decision": "admit",
                        "reason": "claims to be in scope",
                        "variant": None,
                        "seniority": "mid",
                        "skills": ["employee lifecycle"],
                        "excerpt": "employee lifecycle administration",
                    },
                ]
            }
        )
        second = run_research(self._config(responses=responses, run_id="run_test_scope_second"))
        self.assertNotEqual(second["status"], "published")
        self.assertEqual((self.tmp / "release" / "current.json").read_bytes(), pointer_before)
        store = CatalogStore(self.tmp / "release")
        self.assertEqual(store.current_release_id(), first["release_id"])

    def test_malformed_scope_fields_preserve_last_good(self) -> None:
        first = run_research(
            self._config(responses=scripted_responses(run_id="run_scope_types_first"), run_id="run_scope_types_first")
        )
        self.assertEqual(first["status"], "published")
        pointer_before = (self.tmp / "release" / "current.json").read_bytes()
        for field, value in (("work_level_reason", {"unsupported": True}), ("people_management_quote", None)):
            with self.subTest(field=field):
                run_id = f"run_scope_types_{field}"
                responses = scripted_responses(run_id=run_id)
                admissions = json.loads(responses["admission"])
                for row in admissions["admissions"]:
                    row[field] = value
                responses["admission"] = json.dumps(admissions)
                result = run_research(self._config(responses=responses, run_id=run_id))
                self.assertEqual(result["status"], "validated_not_published", result)
                self.assertEqual(result["stats"]["admitted_postings"], 0)
                self.assertEqual((self.tmp / "release" / "current.json").read_bytes(), pointer_before)

    def test_unavailable_descriptions_cannot_be_admitted_from_titles(self) -> None:
        first = run_research(
            self._config(responses=scripted_responses(run_id="run_description_first"), run_id="run_description_first")
        )
        self.assertEqual(first["status"], "published")
        pointer_before = (self.tmp / "release" / "current.json").read_bytes()
        board = greenhouse_payload([
            sample_job("1", "HR Generalist", "Austin, TX", ""),
            sample_job("2", "People Operations Specialist", "Remote - US", ""),
        ])
        config = self._config(
            responses=scripted_responses(run_id="run_description_failure"),
            run_id="run_description_failure",
            transport=routed_transport({"jobs?content=true": board}, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
        )
        result = run_research(config)
        self.assertEqual(result["status"], "validated_not_published", result)
        self.assertEqual(result["stats"]["admitted_postings"], 0)
        self.assertEqual((self.tmp / "release" / "current.json").read_bytes(), pointer_before)

    def test_admission_prompt_compaction_keeps_responsibility_context(self) -> None:
        boilerplate = "Acme is a mission-driven company offering competitive benefits and remote-first culture. " * 40
        text = boilerplate + "Responsibilities: own onboarding and offboarding workflows and manage HRIS data."
        windowed = admission_prompt_text(text)
        self.assertLessEqual(len(windowed), 1800)
        self.assertIn("Responsibilities: own onboarding and offboarding workflows", windowed)

    # -- measured yield defects from the live HR run ---------------------

    def test_location_and_seniority_matchers_keep_measured_cases(self) -> None:
        self.assertTrue(us_location_ok("San Francisco- Remote"))
        self.assertTrue(
            us_location_ok(
                "Atlanta, GA - Hybrid; Denver, CO - Hybrid; New York, NY - Hybrid; "
                "San Francisco, CA - Hybrid; Toronto, Ontario - Remote"
            )
        )
        self.assertTrue(us_location_ok("United States - Remote"))
        self.assertFalse(us_location_ok("Canada - Remote (ON, AB, BC, or NS Only)"))
        self.assertFalse(us_location_ok("Petaling Jaya, Selangor, my"))
        self.assertFalse(us_location_ok("bengaluru, in"))
        self.assertFalse(us_location_ok("Gerlingen, BW, de"))
        self.assertFalse(us_location_ok("Wetzlar, HE, de"))
        self.assertFalse(us_location_ok(""))
        self.assertTrue(us_location_ok("Boise, ID"))
        self.assertTrue(us_location_ok("Fresno, CA"))
        self.assertIsNone(seniority_exclusion_reason("HR Business Partner", "hr-generalist"))
        self.assertIsNone(seniority_exclusion_reason("People Business Partner", "hr-generalist"))
        self.assertEqual(seniority_exclusion_reason("Director, IT Operations", "hr-generalist"), "title band excluded (director)")
        self.assertEqual(seniority_exclusion_reason("HR Business Partner"), "title band excluded (partner)")

    def test_truncated_board_is_salvaged_and_compact_listing_enables_detail_text(self) -> None:
        filler = "x" * (int(RESEARCH_LIMITS["max_response_bytes"]) + 4096)
        truncated = json.dumps(
            {
                "jobs": [
                    sample_job("1", "HR Business Partner", "United States - Remote", "You will drive people operations."),
                    sample_job("2", "Director of Operations", "New York, NY", "Own the function."),
                    {"id": "3", "title": "cut off", "location": {"name": "Austin, TX"}, "absolute_url": JOB_ONE_URL_TEMPLATE.format(token="testco"), "padding": filler},
                ]
            }
        ).encode("utf-8")
        compact = greenhouse_payload(
            [
                sample_job("1", "HR Business Partner", "United States - Remote", "", token="testco"),
                sample_job("2", "People Operations Specialist", "Remote - US", "", token="testco"),
                sample_job("3", "Director of Operations", "New York, NY", "", token="testco"),
            ]
        )
        routes = {
            "boards/testco/jobs/1": greenhouse_detail_payload(
                "1", "HR Business Partner", "United States - Remote",
                "&lt;p&gt;You will drive people operations for designated small business clients.&lt;/p&gt;",
            ),
            "boards/testco/jobs/2": greenhouse_detail_payload(
                "2", "People Operations Specialist", "Remote - US",
                "Support employee lifecycle administration across the US.",
            ),
            "jobs?content=true": truncated,
            "boards/testco/jobs": compact,
        }
        responses = scripted_responses(run_id="run_test_salvage", employer="Testco", token="testco")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco")),
                        "decision": "admit",
                        "reason": "in-scope HR business partner role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally drives people operations for clients; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["people operations"],
                        "excerpt": "drive people operations for designated small business clients",
                    },
                    {
                        "posting_id": dedup_for("Testco", "2", JOB_TWO_URL_TEMPLATE.format(token="testco")),
                        "decision": "admit",
                        "reason": "in-scope people operations role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally supports lifecycle administration; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["employee lifecycle"],
                        "excerpt": "employee lifecycle administration across the US",
                    },
                ]
            }
        )
        config = self._config(
            responses=responses,
            transport=routed_transport(routes, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_salvage",
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["candidate_selection"]["detail_fetches"], 2)
        self.assertFalse(receipt["candidate_selection"]["feedback_triggered"])

        board_row = next(row for row in receipt["retrieval"] if row.get("source_type") == "job-board")
        attempts = board_row["listing_attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0]["truncated"])
        self.assertIn("compact listing fallback", attempts[1]["note"])
        self.assertEqual(board_row["postings_enumerated"], 3)
        self.assertFalse(board_row["listing_truncated"])
        self.assertNotIn("detail_urls", board_row)

        detail_rows = [row for row in receipt["retrieval"] if row.get("source_type") == "job-posting-detail"]
        self.assertEqual(len(detail_rows), 2)
        for detail_row in detail_rows:
            self.assertEqual(detail_row["retrieval_kind"], "detail")
            self.assertEqual(detail_row["parent_source_id"], board_row["id"])
        self.assertEqual(receipt["sampling"]["denominators"]["boards_attempted"], 1)
        self.assertEqual(receipt["sampling"]["denominators"]["boards_used"], 1)

        run_exclusions = json.loads((Path(result["run_dir"]) / "slice" / "exclusions.json").read_text(encoding="utf-8"))
        self.assertTrue(any(row["reason"] == "title band excluded (director)" for row in run_exclusions))

        release = CatalogStore(config.release_root).release()
        self.assertIsNotNone(release)
        self.assertEqual(len(release.postings), 2)
        published_extracts = "\n".join(path.read_text(encoding="utf-8") for path in (release.root / "extracts").glob("*.txt"))
        self.assertIn("drive people operations for designated small business clients", published_extracts)
        self.assertNotIn("&lt;", published_extracts)
        self.assertNotIn("<p>", published_extracts)

    def test_board_attempt_ceiling_includes_discovery_feedback(self) -> None:
        first = run_research(self._config(responses=scripted_responses(run_id="run_cap_baseline"), run_id="run_cap_baseline"))
        self.assertEqual(first["status"], "published")
        pointer_before = (self.tmp / "release" / "current.json").read_bytes()
        for cap in (1, 2):
            with self.subTest(cap=cap):
                run_id = f"run_board_cap_{cap}"
                responses = scripted_responses(
                    run_id=run_id,
                    discovery_boards=[{"ats": "greenhouse", "token": "deadco", "employer": "Deadco"}],
                )
                responses["discovery_feedback"] = json.dumps({"boards": [
                    {"ats": "greenhouse", "token": "testco", "employer": "Testco"},
                    {"ats": "greenhouse", "token": "overflowco", "employer": "Overflowco"},
                ]})
                transport = routed_transport(
                    {"boards/testco/jobs": greenhouse_payload([
                        sample_job("1", "HR Generalist", "Austin, TX", JOB_ONE_DESC),
                        sample_job("2", "People Operations Specialist", "Remote - US", JOB_TWO_DESC),
                    ])},
                    foundation_body=FOUNDATION_TEXT.encode("utf-8"),
                )
                requested_boards = []

                def bounded_transport(url):
                    if "boards-api.greenhouse.io" in url:
                        requested_boards.append(url)
                    return transport(url)

                config = self._config(responses=responses, transport=bounded_transport, run_id=run_id)
                config.max_boards = cap
                result = run_research(config)
                self.assertEqual(len(requested_boards), cap)
                self.assertFalse(any("overflowco" in url for url in requested_boards))
                if cap == 1:
                    self.assertIsNone(result["publish"])
                    self.assertEqual((self.tmp / "release" / "current.json").read_bytes(), pointer_before)
                else:
                    self.assertEqual(result["status"], "published", result)
                    self.assertEqual({row["employer"] for row in CatalogStore(config.release_root).release().postings}, {"Testco"})

    def test_discovery_feedback_replaces_dead_and_thin_boards(self) -> None:
        responses = scripted_responses(
            run_id="run_test_feedback",
            employer="Thinco",
            token="thinco",
            discovery_boards=[
                {"ats": "greenhouse", "token": "deadco", "employer": "Deadco", "why": "token yields nothing"},
                {"ats": "greenhouse", "token": "thinco", "employer": "Thinco", "why": "one in-scope posting"},
            ],
        )
        responses["discovery_feedback"] = json.dumps(
            {"boards": [{"ats": "greenhouse", "token": "liveco", "employer": "Liveco", "why": "replacement board with in-scope postings"}]}
        )
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": dedup_for("Thinco", "1", JOB_ONE_URL_TEMPLATE.format(token="thinco")),
                        "decision": "admit",
                        "reason": "in-scope HR generalist role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally owns onboarding workflows; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["onboarding"],
                        "excerpt": "Own onboarding and offboarding workflows",
                    },
                    {
                        "posting_id": dedup_for("Liveco", "1", JOB_ONE_URL_TEMPLATE.format(token="liveco")),
                        "decision": "admit",
                        "reason": "in-scope people operations role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally supports people operations programs; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["people operations"],
                        "excerpt": "people operations programs",
                    },
                ]
            }
        )
        routes = {
            "boards/deadco/jobs": b"board not found",
            "boards/thinco/jobs": greenhouse_payload(
                [sample_job("1", "HR Generalist", "Austin, TX", JOB_ONE_DESC, token="thinco")]
            ),
            "boards/liveco/jobs": greenhouse_payload(
                [sample_job("1", "People Operations Specialist", "Remote - US", JOB_TWO_DESC, token="liveco")]
            ),
        }
        config = self._config(
            responses=responses,
            transport=routed_transport(routes, foundation_body=FOUNDATION_TEXT.encode("utf-8"), statuses={"boards/deadco/jobs": 404}),
            run_id="run_test_feedback",
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(len(receipt["model_calls"]), 6)
        self.assertTrue(receipt["candidate_selection"]["feedback_triggered"])
        self.assertEqual(receipt["discovery_feedback"][0]["boards"][0]["token"], "liveco")
        failed = [row for row in receipt["retrieval"] if row.get("source_type") == "job-board" and row.get("inclusion") is False]
        self.assertTrue(any("404" in str(row.get("exclusion_reason")) for row in failed))
        release = CatalogStore(config.release_root).release()
        self.assertEqual({posting["employer"] for posting in release.postings}, {"Thinco", "Liveco"})

    def test_agent_selected_priority_links_are_honored_over_topic_text(self) -> None:
        priorities = [
            {
                "label": "Employee lifecycle operations",
                "learning_outcome": "Run onboarding and offboarding workflows with auditable HRIS records.",
                "rationale": "Admitted postings assign lifecycle workflow ownership.",
                "uncertainty": "Small sample; ordering is evidence-based priority, not measured importance.",
                "confidence": "low",
                "basis": "advertised_demand",
                "topic_label": "workforce lifecycle administration",
                "search_terms": ["onboarding", "employee lifecycle"],
            }
        ]
        key_one = dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco"))
        links = [
            {
                "priority_id": requirement_id(
                    "run_test_link", "hr-generalist", 0, priorities[0]["label"], priorities[0]["learning_outcome"]
                ),
                "claim_ids": [demand_claim_id("run_test_link", "hr-generalist", "Onboarding", [key_one])],
                "rationale": "The recorded onboarding-ownership demand claim carries this lifecycle priority.",
            }
        ]
        config = self._config(
            responses=scripted_responses(run_id="run_test_link", priorities=priorities, links=links),
            run_id="run_test_link",
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)
        release = CatalogStore(config.release_root).release()
        requirement = release.requirements[0]
        self.assertEqual(requirement["evidence_claim_ids"], links[0]["claim_ids"])
        self.assertEqual(requirement["evidence_rationale"], links[0]["rationale"])
        self.assertEqual(requirement["priority"], 1)
        demand_claims = [claim for claim in release.claims if claim["claim_type"] == "advertised_demand"]
        self.assertTrue(demand_claims)
        self.assertIn("recorded in 1 of 2 admitted HR Generalist postings", demand_claims[0]["statement"])
        self.assertIn("onboarding workflow ownership", demand_claims[0]["statement"])

    def test_unknown_priority_link_ids_fail_closed(self) -> None:
        priorities = [
            {
                "label": "Zoning analysis",
                "learning_outcome": "Read municipal zoning maps.",
                "rationale": "Not a stated role requirement.",
                "uncertainty": "No evidence.",
                "confidence": "low",
                "basis": "advertised_demand",
                "topic_label": "zoning",
                "search_terms": ["zoning"],
            }
        ]
        links = [
            {
                "priority_id": requirement_id(
                    "run_test_unlinked", "hr-generalist", 0, priorities[0]["label"], priorities[0]["learning_outcome"]
                ),
                "claim_ids": ["clm_00000000000000000000"],
                "rationale": "References a claim that was never recorded.",
            }
        ]
        result = run_research(
            self._config(responses=scripted_responses(run_id="run_test_unlinked", priorities=priorities, links=links), run_id="run_test_unlinked")
        )
        self.assertEqual(result["status"], "validated_not_published")
        self.assertIn("no learning priorities survived verification", result["error"])
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        kinds = {row["kind"] for row in receipt["challenge"]["disagreements"]}
        self.assertIn("invalid_priority_link", kinds)
        self.assertIn("unlinked_learning_priority", kinds)
        slice_requirements = json.loads((Path(result["run_dir"]) / "slice" / "requirements.json").read_text(encoding="utf-8"))
        self.assertEqual(slice_requirements, [])

    def test_priority_link_resolver_drops_unknown_wrong_role_basis_and_cross_variant(self) -> None:
        claims = [
            {
                "id": "clm_foundation",
                "occupation_slug": "hr-generalist",
                "claim_type": "foundation",
                "variant": None,
                "statement": "HR generalists administer lifecycle processes.",
                "quote": "administer employee lifecycle processes",
            },
            {
                "id": "clm_demand_hr",
                "occupation_slug": "hr-generalist",
                "claim_type": "advertised_demand",
                "variant": None,
                "statement": "Onboarding: workflow ownership.",
                "quote": "Own onboarding and offboarding workflows",
            },
            {
                "id": "clm_pg",
                "occupation_slug": "growth-manager",
                "claim_type": "advertised_demand",
                "variant": "product-growth",
                "statement": "Activation loops: self-serve trials.",
                "quote": "self-serve trial activation",
            },
            {
                "id": "clm_gm",
                "occupation_slug": "growth-manager",
                "claim_type": "advertised_demand",
                "variant": "growth-marketing",
                "statement": "Lifecycle email: retention campaigns.",
                "quote": "lifecycle email campaigns",
            },
        ]
        unknown = resolve_priority_links(
            occupation="hr-generalist",
            claims=claims,
            priority_id="req_a",
            basis="advertised_demand",
            link_row={"claim_ids": ["clm_never_recorded"], "rationale": "x"},
        )
        self.assertEqual(unknown["claim_ids"], [])
        self.assertEqual([row["kind"] for row in unknown["disagreements"]], ["invalid_priority_link", "unlinked_learning_priority"])

        wrong_role = resolve_priority_links(
            occupation="hr-generalist",
            claims=claims,
            priority_id="req_b",
            basis="advertised_demand",
            link_row={"claim_ids": ["clm_pg"], "rationale": "x"},
        )
        self.assertEqual(wrong_role["claim_ids"], [])
        self.assertTrue(any(row["kind"] == "invalid_priority_link" for row in wrong_role["disagreements"]))

        wrong_basis = resolve_priority_links(
            occupation="hr-generalist",
            claims=claims,
            priority_id="req_c",
            basis="foundation",
            link_row={"claim_ids": ["clm_demand_hr"], "rationale": "x"},
        )
        self.assertEqual(wrong_basis["claim_ids"], [])
        self.assertTrue(any("stay distinct" in row["issue"] for row in wrong_basis["disagreements"]))

        cross_variant = resolve_priority_links(
            occupation="growth-manager",
            claims=claims,
            priority_id="req_d",
            basis="advertised_demand",
            link_row={"claim_ids": ["clm_pg", "clm_gm"], "rationale": "x"},
        )
        self.assertEqual(cross_variant["claim_ids"], [])
        self.assertEqual([row["kind"] for row in cross_variant["disagreements"]], ["cross_variant_priority_link"])

        accepted = resolve_priority_links(
            occupation="growth-manager",
            claims=claims,
            priority_id="req_e",
            basis="advertised_demand",
            link_row={"claim_ids": ["clm_pg"], "rationale": "Activation evidence."},
        )
        self.assertEqual(accepted["claim_ids"], ["clm_pg"])
        self.assertEqual(accepted["variant"], "product-growth")
        self.assertEqual(accepted["disagreements"], [])

    def test_quote_origin_is_retrieved_detail_response_not_listing(self) -> None:
        detail_text = (
            "Facilitate internal transfers, oversee and facilitate promotion opportunities, "
            "conduct exit interviews for the United States workforce."
        )
        detail_body = greenhouse_detail_payload("1", "HR Generalist", "Austin, TX", detail_text)
        listing = greenhouse_payload([sample_job("1", "HR Generalist", "Austin, TX", "", token="testco")])
        routes = {
            "boards/testco/jobs/1": detail_body,
            "jobs?content=true": listing,
        }
        responses = scripted_responses(run_id="run_test_detail", demand_quote="Facilitate internal transfers, oversee and facilitate promotion opportunities")
        responses["admission"] = json.dumps(
            {
                "admissions": [
                    {
                        "posting_id": dedup_for("Testco", "1", JOB_ONE_URL_TEMPLATE.format(token="testco")),
                        "decision": "admit",
                        "reason": "in-scope HR generalist role",
                        "variant": None,
                        "seniority": "mid",
                        "work_level": "individual_contributor",
                        "work_level_reason": "Personally runs transfers and exit interviews; no direct reports.",
                        "people_management_quote": "",
                        "skills": ["internal transfers"],
                        "excerpt": "Facilitate internal transfers, oversee and facilitate promotion opportunities",
                    }
                ]
            }
        )
        config = self._config(
            responses=responses,
            transport=routed_transport(routes, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_detail",
            min_postings=1,
        )
        result = run_research(config)
        self.assertEqual(result["status"], "published", result)

        release = CatalogStore(config.release_root).release()
        self.assertIsNotNone(release)
        sources_by_id = release.source_by_id()
        detail_row = next(row for row in release.sources if row.get("source_type") == "job-posting-detail")
        board_row = next(row for row in release.sources if row.get("source_type") == "job-board")
        self.assertEqual(detail_row["url"], "https://boards-api.greenhouse.io/v1/boards/testco/jobs/1")
        self.assertEqual(detail_row["sha256"], sha256_bytes(detail_body))
        self.assertEqual(detail_row["bytes"], len(detail_body))
        self.assertEqual(detail_row["retrieval_kind"], "detail")
        self.assertEqual(detail_row["parent_source_id"], board_row["id"])
        self.assertNotIn("content", board_row["url"].split("?")[0])
        self.assertNotIn("detail_urls", board_row)

        posting = release.postings[0]
        self.assertEqual(posting["source_id"], detail_row["id"])
        claim = next(row for row in release.claims if row.get("claim_type") == "advertised_demand")
        self.assertEqual(claim["source_ids"], [detail_row["id"]])
        self.assertEqual(claim["evidence"]["quote_source"], detail_row["id"])
        cited = sources_by_id[str(claim["source_ids"][0])]
        self.assertEqual(cited["url"], "https://boards-api.greenhouse.io/v1/boards/testco/jobs/1")
        self.assertEqual(cited["sha256"], sha256_bytes(detail_body))

        detail_extract = release.extracts[str(detail_row["id"])]
        self.assertIn("Facilitate internal transfers, oversee and facilitate promotion opportunities", detail_extract)
        self.assertIn("# retrieval_kind detail", detail_extract)
        self.assertIn(f"# parent_source_id {board_row['id']}", detail_extract)
        self.assertNotIn("Facilitate internal transfers", release.extracts.get(str(board_row["id"]), ""))

        stats = release.occupations[0]["stats"]
        self.assertEqual(stats["boards_attempted"], 1)
        self.assertEqual(stats["boards_used"], 1)
        self.assertEqual(stats["detail_sources_used"], 1)
        self.assertEqual(validate_release(release.root), [])

    def test_cap_allocation_is_fair_across_employers_and_variants(self) -> None:
        big_board = greenhouse_payload(
            [
                sample_job(f"{index:02d}", "Sales Account Executive", "Austin, TX", "Quota-carrying closing role.", token="aaaplc")
                for index in range(30)
            ]
        )
        small_board = greenhouse_payload(
            [
                sample_job("01", "Product-Led Growth Manager", "Remote - US", "Own activation outcomes.", token="zzzco"),
                sample_job("02", "Lifecycle Marketing Manager", "Remote - US", "Own lifecycle campaigns.", token="zzzco"),
            ]
        )
        routes = {
            "boards/aaaplc/jobs": big_board,
            "boards/zzzco/jobs": small_board,
        }
        responses = scripted_responses(
            run_id="run_test_cap",
            employer="Aaa Plc",
            token="aaaplc",
            discovery_boards=[
                {"ats": "greenhouse", "token": "aaaplc", "employer": "Aaa Plc", "why": "large sales board"},
                {"ats": "greenhouse", "token": "zzzco", "employer": "Zzz Co", "why": "growth variant board"},
            ],
        )
        config = self._config(
            responses=responses,
            transport=routed_transport(routes, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_cap",
            occupation="growth-manager",
            min_postings=3,
            max_postings=6,
        )
        result = run_research(config)
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        allocation = receipt["candidate_selection"]["cap_allocation"]
        self.assertEqual(allocation["cap"], 6)
        self.assertEqual(allocation["selected_candidates"], 6)
        self.assertGreaterEqual(allocation["employer_outcomes"]["Zzz Co"]["selected"], 2)
        self.assertGreaterEqual(allocation["bucket_outcomes"]["product-growth"]["selected"], 1)
        self.assertGreaterEqual(allocation["bucket_outcomes"]["growth-marketing"]["selected"], 1)
        self.assertGreater(allocation["employer_outcomes"]["Aaa Plc"]["excluded_by_cap"], 0)
        self.assertNotIn("product-growth", receipt["candidate_selection"]["missing_required_buckets"])
        cap_exclusions = [
            row for row in json.loads((Path(result["run_dir"]) / "slice" / "exclusions.json").read_text(encoding="utf-8"))
            if "posting cap" in row["reason"]
        ]
        self.assertTrue(cap_exclusions)
        self.assertTrue(all(row["employer"] == "Aaa Plc" for row in cap_exclusions))

    def test_missing_growth_variant_bucket_triggers_feedback(self) -> None:
        sales_board = greenhouse_payload(
            [
                sample_job(f"{index:02d}", "Sales Account Executive", "Austin, TX", "Quota-carrying closing role.", token="aaaplc")
                for index in range(3)
            ]
        )
        variant_board = greenhouse_payload(
            [
                sample_job("01", "Product-Led Growth Manager", "Remote - US", "Own activation outcomes.", token="bboo"),
                sample_job("02", "Lifecycle Marketing Manager", "Remote - US", "Own lifecycle campaigns.", token="bboo"),
            ]
        )
        routes = {
            "boards/aaaplc/jobs": sales_board,
            "boards/bboo/jobs": variant_board,
        }
        responses = scripted_responses(
            run_id="run_test_bucket",
            employer="Aaa Plc",
            token="aaaplc",
            discovery_boards=[{"ats": "greenhouse", "token": "aaaplc", "employer": "Aaa Plc", "why": "sales-only board"}],
        )
        responses["discovery_feedback"] = json.dumps(
            {"boards": [{"ats": "greenhouse", "token": "bboo", "employer": "Bboo Inc", "why": "product-growth and lifecycle postings"}]}
        )
        config = self._config(
            responses=responses,
            transport=routed_transport(routes, foundation_body=FOUNDATION_TEXT.encode("utf-8")),
            run_id="run_test_bucket",
            occupation="growth-manager",
            min_postings=3,
            max_postings=3,
        )
        result = run_research(config)
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["candidate_selection"]["feedback_triggered"])
        self.assertEqual(receipt["discovery_feedback"][0]["missing_variant_buckets"], ["product-growth", "growth-marketing"])
        self.assertEqual(receipt["discovery_feedback"][0]["boards"][0]["token"], "bboo")
        allocation = receipt["candidate_selection"]["cap_allocation"]
        self.assertGreaterEqual(allocation["bucket_outcomes"]["product-growth"]["selected"], 1)
        self.assertGreaterEqual(allocation["bucket_outcomes"]["growth-marketing"]["selected"], 1)
        self.assertGreaterEqual(allocation["employer_outcomes"]["Bboo Inc"]["selected"], 2)

    def test_published_role_lineage_identifies_its_generating_run(self) -> None:
        config = self._config(responses=scripted_responses(run_id="run_test_lineage"), run_id="run_test_lineage")
        result = run_research(config)
        self.assertEqual(result["status"], "published")
        role = CatalogStore(config.release_root).occupation("hr-generalist")
        self.assertEqual([row["run_id"] for row in role["lineage"]], ["run_test_lineage"])

        updated = self._config(responses=scripted_responses(run_id="run_test_lineage_update"), run_id="run_test_lineage_update")
        self.assertEqual(run_research(updated)["status"], "published")
        role = CatalogStore(updated.release_root).occupation("hr-generalist")
        self.assertEqual([row["run_id"] for row in role["lineage"]], ["run_test_lineage_update"])


if __name__ == "__main__":
    unittest.main()
