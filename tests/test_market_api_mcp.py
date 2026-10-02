"""Behavioral checks for the public ASGI API and maintained MCP client protocol."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_support import build_slice, default_claim_id, publish_test_release  # noqa: E402

from skills_vector.market.core import CatalogStore  # noqa: E402
from skills_vector.market.web import create_market_app  # noqa: E402

WORKTREE = Path(__file__).resolve().parents[1]


class MarketServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name) / "release"
        root.mkdir(parents=True)
        (root / "current.json").write_text(
            json.dumps({"schema_version": 1, "current": None, "releases": []}), encoding="utf-8"
        )
        cls.publish = publish_test_release(root, build_slice(Path(cls._tmp.name) / "slice"))
        cls.release_root = root
        cls.store = CatalogStore(root)
        app = create_market_app(
            cls.store,
            worktree=WORKTREE,
            release_root=root,
            serve_static=True,
        )
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls._tmp.cleanup()

    def test_health_brief_and_unpublished_role_are_honest(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(health.json()["release_id"], self.publish["release_id"])

        brief_response = self.client.get("/api/brief")
        self.assertEqual(brief_response.status_code, 200)
        roles = brief_response.json()["roles"]
        self.assertEqual(
            [role["slug"] for role in roles],
            ["hr-generalist", "growth-manager", "account-executive", "forward-deployed-engineer"],
        )
        by_slug = {role["slug"]: role for role in roles}
        self.assertEqual(by_slug["hr-generalist"]["status"], "published")
        self.assertEqual(by_slug["forward-deployed-engineer"]["status"], "pilot_only")
        self.assertEqual(by_slug["forward-deployed-engineer"]["counts"]["claims"], 0)
        self.assertEqual(by_slug["forward-deployed-engineer"]["official_anchor"]["status"], "partial_taxonomy_anchor_only")

        occupation = self.client.get("/api/occupation", params={"slug": "forward-deployed-engineer"})
        self.assertEqual(occupation.status_code, 200)
        self.assertEqual(occupation.json()["status"], "pilot_only")

        listed = self.client.get("/api/occupations").json()["occupations"]
        self.assertEqual([role["slug"] for role in listed], [role["slug"] for role in roles])

    def test_query_search_refine_and_compare_are_cited_sample_reads(self) -> None:
        cited = self.client.get("/api/query", params={"q": "onboarding"})
        self.assertEqual(cited.status_code, 200)
        self.assertEqual(cited.json()["status"], "cited_evidence")

        search = self.client.get("/api/search", params={"q": "onboarding"})
        self.assertEqual(search.status_code, 200)
        self.assertIn("claim", {row["kind"] for row in search.json()["results"]})
        self.assertIn("market prevalence", search.json()["sample_note"])

        refined = self.client.get(
            "/api/refine",
            params={"occupation": "hr-generalist", "work_level": "individual_contributor"},
        )
        self.assertEqual(refined.status_code, 200)
        self.assertEqual(refined.json()["count"], 1)
        self.assertEqual(refined.json()["rows"][0]["work_level"], "individual_contributor")

        compared = self.client.get(
            "/api/compare",
            params=[
                ("occupation", "hr-generalist"),
                ("occupation", "forward-deployed-engineer"),
            ],
        )
        self.assertEqual(compared.status_code, 200)
        by_slug = {role["slug"]: role for role in compared.json()["roles"]}
        self.assertEqual(by_slug["hr-generalist"]["postings_admitted"], 1)
        self.assertIsNone(by_slug["forward-deployed-engineer"]["postings_admitted"])
        self.assertEqual(by_slug["forward-deployed-engineer"]["published_findings"], 0)
        self.assertNotIn("coverage_percent", compared.json())

        unsupported = self.client.get("/api/query", params={"q": "quantum welding certification"})
        self.assertEqual(unsupported.status_code, 200)
        self.assertEqual(unsupported.json()["status"], "unsupported_question")
        self.assertTrue(unsupported.json()["handoff"]["local_only"])
        self.assertFalse(unsupported.json()["handoff"]["mutates_public_state"])

    def test_component_http_reconstruction_and_fail_closed_ids(self) -> None:
        role = self.client.get("/api/occupation", params={"slug": "hr-generalist"}).json()
        ref = next(row for row in role["component_refs"] if row["measure"] == "responsibility_band_distribution")
        response = self.client.get("/api/component", params={"id": ref["component_id"], "release": role["release_id"]})
        self.assertEqual(response.status_code, 200)
        component = response.json()
        self.assertEqual(component["release_id"], role["release_id"])
        self.assertEqual(component["rows"][0]["value"], "independent_ic")
        self.assertEqual(component["rows"][0]["numerator"], 1)
        self.assertEqual(component["denominator"], len(component["observations"]))
        self.assertEqual(self.client.get("/api/component", params={"id": "invalid"}).status_code, 400)
        self.assertEqual(self.client.get("/api/component", params={"id": "cmp_" + "0" * 20}).status_code, 404)
        self.assertEqual(
            self.client.get("/api/component", params={"id": ref["component_id"], "release": "../current"}).status_code,
            400,
        )

    def test_public_api_rejects_mutations_and_enforces_input_bounds(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = self.client.request(method, "/api/query?q=onboarding", json={"q": "onboarding"})
            self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.get("/api/query", params={"q": "a" * 500}).status_code, 400)
        self.assertEqual(self.client.get("/api/query", params={"q": "onboarding", "limit": "0"}).status_code, 400)
        self.assertEqual(self.client.get("/api/evidence", params={"section": "secrets"}).status_code, 400)
        self.assertEqual(self.client.get("/api/refine", params={"context_value": "remote"}).status_code, 400)
        self.assertEqual(self.client.get("/api/compare", params=[("occupation", "hr-generalist")]).status_code, 400)
        self.assertEqual(
            self.client.request("GET", "/api/query?q=onboarding", content=b"x" * 9000).status_code,
            413,
        )

    def test_citation_is_served_and_static_traversal_is_rejected(self) -> None:
        claim_id = default_claim_id()
        citation = self.client.get(f"/release/citations/{claim_id}.json")
        self.assertEqual(citation.status_code, 200)
        self.assertEqual(citation.json()["claim_id"], claim_id)
        traversal = self.client.get("/release/releases/../../.gitignore")
        self.assertEqual(traversal.status_code, 404)


class MarketMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "release"
        root.mkdir(parents=True)
        (root / "current.json").write_text(
            json.dumps({"schema_version": 1, "current": None, "releases": []}), encoding="utf-8"
        )
        publish_test_release(root, build_slice(Path(self._tmp.name) / "slice"))
        self.store = CatalogStore(root)
        self.app = create_market_app(self.store, release_root=root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sdk_session_lists_read_only_tools_and_calls_shared_catalog(self) -> None:
        async def exercise() -> None:
            transport = httpx2.ASGITransport(app=self.app)
            async with self.app.router.lifespan_context(self.app), httpx2.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:8000",
            ) as http_client:
                async with streamable_http_client(
                    "http://127.0.0.1:8000/api/mcp",
                    http_client=http_client,
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        listing = await session.list_tools()
                        tools = {tool.name: tool for tool in listing.tools}
                        self.assertTrue(
                            {
                                "get_brief",
                                "get_occupation",
                                "search_evidence",
                                "refine_evidence",
                                "compare_roles",
                                "get_claim",
                                "get_component",
                                "release_info",
                                "inspect_evidence",
                                "research_plan",
                            }.issubset(tools)
                        )
                        for tool in tools.values():
                            self.assertIsNotNone(tool.annotations)
                            self.assertTrue(tool.annotations.read_only_hint)
                            self.assertFalse(tool.annotations.open_world_hint)

                        mcp_brief = await session.call_tool("get_brief", {})
                        mcp_data = mcp_brief.structured_content or json.loads(mcp_brief.content[0].text)
                        rest_data = (await http_client.get("/api/brief")).json()
                        self.assertEqual(mcp_data, rest_data)

                        profile = await session.call_tool(
                            "get_occupation",
                            {"slug": "forward-deployed-engineer"},
                        )
                        profile_data = profile.structured_content or json.loads(profile.content[0].text)
                        self.assertEqual(profile_data["status"], "pilot_only")
                        self.assertEqual(profile_data["occupation"]["publication_status"], "no_fde_findings_without_admissible_source_evidence")

                        role_result = await session.call_tool("get_occupation", {"slug": "hr-generalist"})
                        role = role_result.structured_content
                        ref = next(row for row in role["component_refs"] if row["measure"] == "responsibility_band_distribution")
                        result = await session.call_tool(
                            "get_component", {"component_id": ref["component_id"], "release_id": role["release_id"]},
                        )
                        component = result.structured_content
                        self.assertEqual(component["release_id"], role["release_id"])
                        self.assertEqual(component["rows"][0]["value"], "independent_ic")
                        self.assertEqual(component["rows"][0]["numerator"], 1)
                        self.assertEqual(component["denominator"], len(component["observations"]))

                        handoff = await session.call_tool(
                            "research_plan",
                            {"query": "onboarding", "occupation": "forward-deployed-engineer"},
                        )
                        handoff_data = handoff.structured_content or json.loads(handoff.content[0].text)
                        self.assertTrue(handoff_data["local_only"])
                        self.assertFalse(handoff_data["mutates_public_state"])

        anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
