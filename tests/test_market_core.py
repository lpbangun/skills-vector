"""Deterministic behavior tests: query semantics, citations, publish safety, API bounds."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_support import build_slice, default_claim_id, make_candidate, publish_test_release  # noqa: E402

from skills_vector.market.api import handle_request  # noqa: E402
from skills_vector.market.core import CatalogStore  # noqa: E402
from skills_vector.market.publish import PublishError, publish_release  # noqa: E402
from skills_vector.market.release import expectation_relationship_id, sha256_text, source_literal_identity, validate_release  # noqa: E402


class MarketCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "release"
        self.root.mkdir(parents=True)
        (self.root / "current.json").write_text(
            json.dumps({"schema_version": 1, "current": None, "releases": []}) + "\n", encoding="utf-8"
        )
        self.slice_dir = build_slice(Path(self._tmp.name) / "slice-a")
        self.publish_result = publish_test_release(self.root, self.slice_dir)
        self.store = CatalogStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- catalog/query ---------------------------------------------------

    def test_empty_catalog_is_honest(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        (empty / "current.json").write_text(json.dumps({"schema_version": 1, "current": None, "releases": []}), encoding="utf-8")
        store = CatalogStore(empty)
        self.assertEqual(store.release_info()["status"], "no_release")
        occupations = store.list_occupations()["occupations"]
        self.assertEqual([row["slug"] for row in occupations], [
            "hr-generalist", "growth-manager", "account-executive", "forward-deployed-engineer"
        ])
        self.assertEqual(occupations[-1]["status"], "pilot_only")
        self.assertEqual(occupations[-1]["counts"]["claims"], 0)
        answer = store.query("onboarding")
        self.assertEqual(answer["status"], "insufficient_evidence")
        self.assertIsNone(answer["answer"])
        self.assertTrue(answer["handoff"]["local_only"])
        self.assertFalse(answer["handoff"]["mutates_public_state"])

    def test_release_info_pins_publication_policy_and_preserves_legacy_unknown(self) -> None:
        historical = self.store.release()
        self.assertIsNotNone(historical)
        historical_id = historical.release_id

        next_slice = build_slice(
            Path(self._tmp.name) / "slice-b",
            run_id="run_test_0002",
            occupation="growth-manager",
        )
        publish_test_release(self.root, next_slice)
        current_id = self.store.current_release_id()
        self.assertNotEqual(current_id, historical_id)

        policy = {"mode": "automatic", "human_reviewed": False}
        manifest_path = self.root / "releases" / str(current_id) / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["publication_policy"] = policy
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        store = CatalogStore(self.root)
        self.assertEqual(store.release_info(str(current_id))["publication_policy"], policy)
        self.assertIsNone(store.release_info(historical_id)["publication_policy"])

    def test_query_supported_insufficient_unsupported(self) -> None:
        cited = self.store.query("onboarding")
        self.assertEqual(cited["status"], "cited_evidence")
        self.assertEqual(cited["claims"][0]["claim_id"], default_claim_id())
        self.assertEqual(cited["confidence"]["level"], "bounded")
        self.assertNotIn("handoff", cited)

        thin = self.store.query("onboarding", occupation="growth-manager")
        self.assertEqual(thin["status"], "insufficient_evidence")
        self.assertEqual(thin["known_occupations"], ["hr-generalist"])
        self.assertIn("handoff", thin)

        unsupported = self.store.query("quantum welding certification")
        self.assertEqual(unsupported["status"], "unsupported_question")
        self.assertIn("handoff", unsupported)
        self.assertFalse(unsupported["handoff"]["mutates_public_state"])

        role_bundle = self.store.occupation("hr-generalist")
        self.assertEqual(role_bundle["status"], "ok")
        self.assertEqual(role_bundle["demand"]["employers"][0]["employer"], "Testco")

    def test_registered_roles_and_comparison_never_promote_unpublished_fde(self) -> None:
        occupations = self.store.list_occupations()["occupations"]
        self.assertEqual(
            {row["slug"] for row in occupations},
            {"hr-generalist", "growth-manager", "account-executive", "forward-deployed-engineer"},
        )
        fde = self.store.occupation("forward-deployed-engineer")
        self.assertEqual(fde["status"], "pilot_only")
        self.assertEqual(fde["occupation"]["official_anchor"]["status"], "partial_taxonomy_anchor_only")

        comparison = self.store.compare(["hr-generalist", "forward-deployed-engineer"])
        self.assertEqual(comparison["status"], "ok")
        compared_fde = next(row for row in comparison["roles"] if row["slug"] == "forward-deployed-engineer")
        self.assertEqual(compared_fde["status"], "pilot_only")
        self.assertIsNone(compared_fde["postings_admitted"])
        self.assertEqual(compared_fde["facets"], {})
        self.assertEqual(compared_fde["published_findings"], 0)
        self.assertNotIn("coverage_percent", comparison)

        search = self.store.search("onboarding")
        self.assertEqual(search["status"], "ok")
        self.assertIn("claim", {row["kind"] for row in search["results"]})

    def test_refine_keeps_source_literal_identity_separate_from_edges_and_basis(self) -> None:
        release = self.store.release()
        self.assertIsNotNone(release)
        posting = release.postings[0]
        source_id = str(posting["source_id"])
        source_sha256 = release.source_by_id()[source_id]["sha256"]
        shared_wording = "Independently own end-to-end onboarding workflows"
        distinct_wording = "experience with HRIS records"

        def expectation(dimension: str, basis: str, wording: str) -> dict[str, str | None]:
            normalized = " ".join(wording.split()).casefold()
            expectation_id = "exp_" + sha256_text(
                f"expectation/2|{dimension}|{basis}|{normalized}"
            )[:20]
            return {
                "expectation_id": expectation_id,
                "identity_id": source_literal_identity(dimension, wording),
                "identity_method": "source-literal-identity/1",
                "kind": dimension,
                "relationship_id": expectation_relationship_id(
                    "hr-generalist", str(posting["dedup_key"]), source_id, expectation_id,
                ),
                "relationship_method": "posting-source-expectation/1",
                "source_wording": wording,
                "normalized_label": normalized,
                "dimension": dimension,
                "basis": basis,
                "proficiency": "not_stated",
                "proficiency_quote": None,
                "mapping_method": "exact-normalized-label/2",
                "source_id": source_id,
                "source_sha256": source_sha256,
            }

        required = expectation("task", "employer_requirement", shared_wording)
        preferred = expectation("task", "employer_preference", shared_wording)
        different = expectation("knowledge", "employer_requirement", distinct_wording)
        posting["expectations"] = [required, preferred, different]

        refined = self.store.refine(
            occupation="hr-generalist",
            filters={"expectation_identity": required["identity_id"]},
        )
        self.assertEqual(refined["count"], 1)
        self.assertEqual(refined["facets"]["expectation_identity"][required["identity_id"]], 1)
        identities = {row["identity_id"]: row for row in refined["expectation_identities"]}
        shared = identities[required["identity_id"]]
        self.assertEqual(shared["postings_in_sample"], 1)
        self.assertEqual(shared["basis_counts"], {"employer_preference": 1, "employer_requirement": 1})
        self.assertEqual(set(shared["expectation_ids"]), {required["expectation_id"], preferred["expectation_id"]})
        self.assertEqual(set(shared["relationship_ids"]), {required["relationship_id"], preferred["relationship_id"]})
        self.assertEqual(
            {row["basis"] for row in shared["observations"]},
            {"employer_preference", "employer_requirement"},
        )
        self.assertTrue(
            all(row["context_dimensions"]["employer_industry"]["value"] == "manufacturing" for row in shared["observations"])
        )
        self.assertEqual(
            identities[different["identity_id"]]["source_wordings"],
            [distinct_wording],
        )
        self.assertEqual(
            {row["relationship_id"] for row in refined["rows"][0]["expectations"]},
            {required["relationship_id"], preferred["relationship_id"], different["relationship_id"]},
        )
        comparison = self.store.compare(["hr-generalist", "forward-deployed-engineer"])
        hr = next(row for row in comparison["roles"] if row["slug"] == "hr-generalist")
        fde = next(row for row in comparison["roles"] if row["slug"] == "forward-deployed-engineer")
        self.assertEqual(
            {row["identity_id"] for row in hr["expectation_identities"]},
            {required["identity_id"], different["identity_id"]},
        )
        self.assertEqual(fde["expectation_identities"], [])

    def test_management_band_filter_is_recognized_without_inferring_management(self) -> None:
        management = handle_request(
            "GET",
            "/api/refine?occupation=hr-generalist&work_level=individual_contributor&responsibility_band=people_management",
            store=self.store,
        )
        self.assertEqual(management.status, 200)
        management_payload = json.loads(management.body)
        self.assertEqual(management_payload["filters"]["responsibility_band"], "people_management")
        self.assertEqual(management_payload["count"], 0)

        independent = handle_request(
            "GET",
            "/api/refine?occupation=hr-generalist&work_level=individual_contributor&responsibility_band=independent_ic",
            store=self.store,
        )
        self.assertEqual(independent.status, 200)
        self.assertEqual(json.loads(independent.body)["count"], 1)

    def test_claim_and_citation_resolution(self) -> None:
        claim_id = default_claim_id()
        claim = self.store.claim(claim_id)
        self.assertEqual(claim["status"], "ok")
        self.assertTrue(claim["quote_verified"])
        citation = self.store.citation(claim_id)
        self.assertEqual(citation["status"], "ok")
        self.assertEqual(citation["claim_id"], claim_id)
        self.assertEqual(citation["statement"], claim["statement"])
        self.assertEqual(citation["citation_path"], f"/release/citations/{claim_id}.json")

    def test_component_counts_unknowns_and_history_remain_reconstructable(self) -> None:
        first_release = self.publish_result["release_id"]
        ref = next(
            row for row in self.store.occupation("hr-generalist")["component_refs"]
            if row["measure"] == "responsibility_band_distribution"
        )
        before = self.store.get_component(ref["component_id"], first_release)
        self.assertEqual(before["denominator"], 1)
        self.assertEqual(before["rows"][0]["value"], "independent_ic")
        self.assertEqual(before["rows"][0]["numerator"], 1)
        self.assertEqual(before["observations"][0]["source"]["url"], "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true#1")

        slice_b = build_slice(Path(self._tmp.name) / "component-update", run_id="run_component_2")
        second = publish_test_release(self.root, slice_b)
        self.assertEqual(self.store.get_component(ref["component_id"], first_release), before)
        release = self.store.release(second["release_id"])
        release.postings[0]["responsibility_band"] = "unknown"
        after = self.store.get_component(ref["component_id"])
        self.assertEqual(after["release_id"], second["release_id"])
        self.assertEqual(after["rows"][0]["value"], "unknown")
        self.assertEqual(after["rows"][0]["status"], "unknown")
        self.assertEqual(sum(row["numerator"] for row in after["rows"]), after["denominator"])
        self.assertEqual(after["denominator"], len(after["observations"]))
        self.assertEqual(self.store.get_component(ref["component_id"], first_release), before)
        self.assertEqual(self.store.get_component("cmp_" + "0" * 20)["status"], "unknown_component")

    # -- citations/immutability -----------------------------------------

    def test_citation_survives_second_release(self) -> None:
        claim_id = default_claim_id()
        citation_path = self.root / "citations" / f"{claim_id}.json"
        first_bytes = citation_path.read_bytes()
        first_release = self.publish_result["release_id"]

        slice_b = build_slice(
            Path(self._tmp.name) / "slice-b",
            run_id="run_test_0002",
            claim_statement="A second update adds a distinct foundation claim for lifecycle support.",
            claim_quote="coordinate onboarding and offboarding",
        )
        second = publish_test_release(self.root, slice_b)
        self.assertNotEqual(second["release_id"], first_release)
        self.assertEqual(citation_path.read_bytes(), first_bytes)
        self.assertEqual(self.store.current_release_id(), second["release_id"])
        preserved = self.store.release(first_release)
        self.assertIsNotNone(preserved)
        self.assertEqual(preserved.release_id, first_release)
        citation_after = self.store.citation(claim_id)
        self.assertEqual(citation_after["claim_id"], claim_id)
        self.assertEqual(json.dumps(citation_after["sources"], sort_keys=True), json.dumps(json.loads(first_bytes)["sources"], sort_keys=True))

    def test_changes_artifact_tracks_pinned_publication_delta_and_idempotence(self) -> None:
        first_id = self.publish_result["release_id"]
        first_path = self.root / "releases" / first_id / "changes.json"
        first_bytes = first_path.read_bytes()
        first = json.loads(first_bytes)
        self.assertEqual(first["schema_version"], "market-release-changes/1")
        self.assertEqual(first["release_id"], first_id)
        self.assertIsNone(first["previous_release_id"])
        self.assertEqual(first["changes"]["claims"]["added"], [default_claim_id()])

        second_slice = build_slice(
            Path(self._tmp.name) / "slice-b",
            run_id="run_test_0002",
            claim_statement="A second release adds a distinct foundation claim for lifecycle support.",
            claim_quote="coordinate onboarding and offboarding",
        )
        second = publish_test_release(self.root, second_slice)
        second_id = second["release_id"]
        second_path = self.root / "releases" / second_id / "changes.json"
        second = json.loads(second_path.read_text(encoding="utf-8"))
        next_claim_id = json.loads((Path(self._tmp.name) / "slice-b" / "claims.json").read_text(encoding="utf-8"))[0]["id"]
        self.assertEqual(second["previous_release_id"], first_id)
        self.assertEqual(second["changes"]["claims"]["added"], [next_claim_id])
        self.assertEqual(second["changes"]["claims"]["removed"], [default_claim_id()])
        self.assertEqual(second["changes"]["claims"]["counts"], {"added": 1, "removed": 1, "updated": 0})
        self.assertEqual(second["changes"]["postings"]["counts"], {"added": 0, "removed": 0, "updated": 0})
        self.assertEqual(second["changes"]["sources"]["counts"], {"added": 0, "removed": 0, "updated": 0})
        self.assertEqual(first_path.read_bytes(), first_bytes)

        second_bytes = second_path.read_bytes()
        repeated = publish_test_release(self.root, second_slice)
        self.assertEqual(repeated["release_id"], second_id)
        self.assertEqual(second_path.read_bytes(), second_bytes)

    def test_republishing_identical_content_preserves_release_and_citation(self) -> None:
        citation_path = self.root / "citations" / f"{default_claim_id()}.json"
        before = citation_path.read_bytes()
        repeated = publish_test_release(self.root, self.slice_dir)
        self.assertEqual(repeated["release_id"], self.publish_result["release_id"])
        self.assertEqual(citation_path.read_bytes(), before)

    def test_supporting_lineage_changes_receive_a_distinct_immutable_release(self) -> None:
        citation_path = self.root / "citations" / f"{default_claim_id()}.json"
        before = citation_path.read_bytes()
        (self.slice_dir / "lineage.json").write_text(
            json.dumps([{"occupation_slug": "hr-generalist", "run_id": "run_test_0001", "publication": {"status": "validated"}}]),
            encoding="utf-8",
        )
        updated = publish_test_release(self.root, self.slice_dir)
        self.assertNotEqual(updated["release_id"], self.publish_result["release_id"])
        self.assertEqual(citation_path.read_bytes(), before)
        self.assertEqual(
            [row["run_id"] for row in self.store.occupation("hr-generalist")["lineage"]],
            ["run_test_0001"],
        )

    def test_replaced_slice_does_not_carry_stale_extracts_into_next_release(self) -> None:
        """A published release must not keep extract bytes whose source row was replaced."""

        first = self.store.release()
        self.assertIsNotNone(first)
        stale_source_id = "src_test_stale"
        stale_name = f"{stale_source_id}.txt"
        stale_text = "# source src_test_stale\nstale extract bytes that must not survive an update\n"
        (first.root / "extracts" / stale_name).write_text(stale_text, encoding="utf-8")
        sources_path = first.root / "sources.json"
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        sources.append(
            {
                "id": stale_source_id,
                "url": "https://www.bls.gov/ooh/business-and-financial/human-resources-specialists.htm",
                "publisher": "U.S. Bureau of Labor Statistics",
                "source_type": "foundation",
                "occupation_slug": "hr-generalist",
                "retrieved_at": "2026-09-30T00:00:00Z",
                "sha256": sha256_text("stale raw bytes"),
                "bytes": 15,
                "rights": "U.S. Bureau of Labor Statistics public domain; short excerpts published with attribution",
                "inclusion": True,
                "extract_path": f"extracts/{stale_name}",
                "extract_sha256": sha256_text(stale_text),
                "role_scope": {"geography": "United States", "seniority": "mid-level individual contributor"},
            }
        )
        sources_path.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")

        slice_b = build_slice(
            Path(self._tmp.name) / "slice-c",
            run_id="run_test_0003",
            claim_statement="A replacement update cites a new foundation claim.",
            claim_quote="coordinate onboarding and offboarding",
        )
        second = publish_test_release(self.root, slice_b)
        second_release = self.store.release(second["release_id"])
        self.assertIsNotNone(second_release)
        self.assertFalse((second_release.root / "extracts" / stale_name).exists())
        self.assertFalse(
            any(str(row.get("id")) == stale_source_id for row in second_release.sources)
        )
        self.assertTrue((first.root / "extracts" / stale_name).is_file())

    def test_failed_publish_keeps_last_good(self) -> None:
        pointer_before = (self.root / "current.json").read_bytes()
        bad_slice = build_slice(
            Path(self._tmp.name) / "slice-bad",
            run_id="run_test_bad",
            claim_statement="A tampered claim whose quote does not exist in any extract.",
            claim_quote="this phrase was never retrieved from any source",
        )
        from skills_vector.market.publish import merge_slice

        candidate = merge_slice(self.root, bad_slice, generated_at="2026-09-30T00:00:00Z")
        with self.assertRaises(PublishError):
            publish_release(self.root, candidate)
        self.assertEqual((self.root / "current.json").read_bytes(), pointer_before)
        self.assertEqual(self.store.current_release_id(), self.publish_result["release_id"])

    def test_validation_rejects_dedup_mismatch_prevalence_and_fixture(self) -> None:
        mismatch = build_slice(Path(self._tmp.name) / "slice-dedup", stats_override={"postings_dedup": 99})
        problems = validate_release(make_candidate(mismatch, Path(self._tmp.name) / "work-dedup"))
        self.assertTrue(any("postings_dedup" in problem for problem in problems), problems)

        prevalence = build_slice(
            Path(self._tmp.name) / "slice-prev",
            claim_statement="Prevalence of onboarding exceeds 60% of the market.",
        )
        problems = validate_release(make_candidate(prevalence, Path(self._tmp.name) / "work-prev"))
        self.assertTrue(any("prevalence" in problem for problem in problems), problems)

        leaked = build_slice(Path(self._tmp.name) / "slice-fixture")
        claims_path = leaked / "claims.json"
        rows = json.loads(claims_path.read_text(encoding="utf-8"))
        rows[0]["statement"] = "fixture provenance leaked into a product row"
        claims_path.write_text(json.dumps(rows), encoding="utf-8")
        problems = validate_release(make_candidate(leaked, Path(self._tmp.name) / "work-fixture"))
        self.assertTrue(any("fixture" in problem for problem in problems), problems)

        unsafe = build_slice(Path(self._tmp.name) / "slice-unsafe")
        postings_path = unsafe / "postings.json"
        postings = json.loads(postings_path.read_text(encoding="utf-8"))
        postings[0]["url"] = "javascript:alert(1)"
        postings_path.write_text(json.dumps(postings), encoding="utf-8")
        problems = validate_release(make_candidate(unsafe, Path(self._tmp.name) / "work-unsafe"))
        self.assertTrue(any("plain https" in problem for problem in problems), problems)

    def test_validation_rejects_admitted_postings_without_ic_work_level(self) -> None:
        pointer_before = (self.root / "current.json").read_bytes()
        cases = {
            "pm": lambda row: row.update({"work_level": "people_manager"}),
            "unknown": lambda row: row.update({"work_level": "unknown"}),
            "missing": lambda row: row.pop("work_level"),
            "no-reason": lambda row: row.update({"work_level_reason": ""}),
            "pm-quote": lambda row: row.update({"people_management_quote": "lead a team"}),
            "malformed-reason": lambda row: row.update({"work_level_reason": {"unsupported": True}}),
            "malformed-quote": lambda row: row.update({"people_management_quote": None}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                slice_dir = build_slice(Path(self._tmp.name) / f"slice-{name}")
                postings_path = slice_dir / "postings.json"
                rows = json.loads(postings_path.read_text(encoding="utf-8"))
                mutate(rows[0])
                postings_path.write_text(json.dumps(rows), encoding="utf-8")
                candidate = make_candidate(slice_dir, Path(self._tmp.name) / f"work-{name}")
                with self.assertRaises(PublishError):
                    publish_release(self.root, candidate)
                self.assertEqual((self.root / "current.json").read_bytes(), pointer_before)


    def test_validator_rejects_source_contradicted_manager_even_with_matching_counts(self) -> None:
        slice_dir = build_slice(Path(self._tmp.name) / "slice-contradicted-manager")
        postings_path = slice_dir / "postings.json"
        postings = json.loads(postings_path.read_text(encoding="utf-8"))
        posting = postings[0]
        quote = "individual contributor role with no direct reports"
        posting["work_level"] = "people_manager"
        posting["work_level_reason"] = "Contradictory classifier label for a source passage that says no direct reports."
        posting["work_level_evidence"] = {
            **posting["work_level_evidence"],
            "quote": quote,
            "reason": "Deliberately contradicted by the exact source wording.",
        }
        posting["people_management_quote"] = quote
        posting["responsibility_band"] = "people_management"
        posting["responsibility_evidence"] = {
            **posting["responsibility_evidence"],
            "quote": quote,
            "reason": "Deliberately contradicted by the exact source wording.",
        }
        postings_path.write_text(json.dumps(postings), encoding="utf-8")

        occupations_path = slice_dir / "occupations.json"
        occupations = json.loads(occupations_path.read_text(encoding="utf-8"))
        occupations[0]["stats"]["work_level_counts"] = {"people_manager": 1}
        occupations[0]["stats"]["responsibility_band_counts"] = {"people_management": 1}
        occupations_path.write_text(json.dumps(occupations), encoding="utf-8")

        candidate = make_candidate(slice_dir, Path(self._tmp.name) / "work-contradicted-manager")
        problems = validate_release(candidate)
        self.assertTrue(
            any("people-manager posting lacks explicit direct-report wording" in problem for problem in problems),
            problems,
        )
        self.assertTrue(
            any("classified responsibility band lacks a verified source-extract quote" in problem for problem in problems),
            problems,
        )
        self.assertFalse(
            any("stats.work_level_counts" in problem or "stats.responsibility_band_counts" in problem for problem in problems),
            problems,
        )

    def test_excluded_source_cannot_supply_admitted_postings(self) -> None:
        pointer_before = (self.root / "current.json").read_bytes()
        slice_dir = build_slice(Path(self._tmp.name) / "slice-excluded-source")
        postings = json.loads((slice_dir / "postings.json").read_text(encoding="utf-8"))
        source_id = postings[0]["source_id"]
        source_path = slice_dir / "sources.json"
        sources = json.loads(source_path.read_text(encoding="utf-8"))
        for source in sources:
            if source["id"] == source_id:
                source["inclusion"] = False
        source_path.write_text(json.dumps(sources), encoding="utf-8")
        candidate = make_candidate(slice_dir, Path(self._tmp.name) / "work-excluded-source")
        with self.assertRaises(PublishError):
            publish_release(self.root, candidate)
        self.assertEqual((self.root / "current.json").read_bytes(), pointer_before)


    # -- API router bounds ----------------------------------------------

    def test_api_router_rejects_mutations_and_bad_input(self) -> None:
        def call(method: str, target: str, body: bytes = b"") -> tuple[int, bytes]:
            response = handle_request(method, target, body=body, store=self.store)
            return response.status, response.body

        status, _ = call("POST", "/api/query?q=onboarding")
        self.assertEqual(status, 405)
        status, _ = call("DELETE", "/api/release")
        self.assertEqual(status, 405)
        status, _ = call("GET", "/api/query?q=" + "a" * 500)
        self.assertEqual(status, 400)
        status, _ = call("GET", "/api/query?q=onboarding&limit=0")
        self.assertEqual(status, 400)
        status, _ = call("GET", "/api/query?q=onboarding&limit=abc")
        self.assertEqual(status, 400)
        status, _ = call("GET", "/api/evidence?section=secrets")
        self.assertEqual(status, 400)
        status, _ = call("GET", "/api/does-not-exist")
        self.assertEqual(status, 404)
        status, body = call("GET", "/api/query?q=onboarding")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "cited_evidence")
        status, _ = call("HEAD", "/api/health")
        self.assertEqual(status, 200)
        status, _ = call("POST", "/api/release", body=b"x" * 9000)
        self.assertEqual(status, 405)
        status, _ = call("GET", "/api/query?q=onboarding", body=b"x" * 9000)
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()
