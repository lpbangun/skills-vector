"""Offline end-to-end Role Atlas REST/MCP parity, configuration and release pins."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
import httpx2
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from skills_vector.market.api import handle_request, handler_for_release_root
from skills_vector.market.core import CatalogStore
from skills_vector.market.guide import agent_guide_markdown, api_schema
from skills_vector.market.mcp import main as mcp_main
from skills_vector.market.role_atlas import FIXTURE_ROOT, SCHEMA_PATH, RoleCatalogStore, RoleRecord
from skills_vector.market.role_reads import FIXTURE_CAVEAT, RoleAtlasReads, role_atlas_from_environment
from skills_vector.market.server import build_server
from skills_vector.market.web import create_market_app
from skills_vector.market_cli import build_parser, run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RELEASE = "fixture_role_atlas_2026q3"


def synthetic_snapshot(source: dict, release: str, dates: dict[str, str]) -> dict:
    """Create explicitly fixture-labeled historical snapshots in temp directories."""
    def update(value: object) -> object:
        if isinstance(value, dict):
            return {key: update(child) for key, child in value.items()}
        if isinstance(value, list):
            return [update(child) for child in value]
        if isinstance(value, str):
            return dates.get(value, value).replace("@" + source["release_id"], "@" + release)
        return value
    result = update(source)
    result["release_id"] = release
    return result


class RoleAtlasHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.legacy = CatalogStore(Path(cls.tmp.name) / "legacy")
        cls.reads = RoleAtlasReads.configured(fixtures=True)
        cls.client = TestClient(create_market_app(cls.legacy, role_catalog=cls.reads))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    def test_all_read_surfaces_have_pins_caveats_and_expected_values(self) -> None:
        index = self.client.get("/v1/roles")
        self.assertEqual(index.status_code, 200)
        self.assertEqual(len(index.json()["roles"]), 3)
        self.assertEqual({role["data_kind"] for role in index.json()["roles"]}, {"fixture"})
        role = self.client.get("/v1/roles/account-executive").json()["role"]
        Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(role)
        self.assertEqual(role["publication"], "standard")
        self.assertEqual(role["anchors"][0]["fit"], "partial")
        citation = role["ladder"]["levels"][0]["expectations"][0]["cite"]
        paths = ["/v1/roles", "/v1/roles/account-executive", "/v1/roles/account-executive/levels/L3",
                 "/v1/roles/account-executive/graph", "/v1/roles/account-executive/industries/51",
                 "/v1/shifts?since=2026-Q2", citation]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["release_id"], FIXTURE_RELEASE)
                self.assertEqual(payload["data_kind"], "fixture")
                self.assertEqual(payload["fixture_caveat"], FIXTURE_CAVEAT)
                self.assertIn("assumption", " ".join(payload["limits"]))
        level = self.client.get("/v1/roles/account-executive/levels/3").json()["level"]
        self.assertEqual((level["name"], level["evidence"]["postings"]), ("Mid-Market AE", 121))
        graph = self.client.get("/v1/roles/account-executive/graph").json()["graph"]
        self.assertEqual(graph, role["skill_map"])
        industry = self.client.get("/v1/roles/account-executive/industries/31-33").json()["industry"]
        self.assertEqual(industry["naics"], "31–33")
        self.assertEqual(industry["evidence"]["postings"], 112)

    def test_experimental_and_thin_profiles_remain_honest(self) -> None:
        role = self.client.get("/v1/roles/forward-deployed-engineer").json()
        self.assertEqual(role["publication"], "experimental")
        self.assertEqual(role["role"]["status"], "emerging")
        sector = self.client.get("/v1/roles/forward-deployed-engineer/industries/52").json()["industry"]
        self.assertIsNone(sector["profile"])
        self.assertEqual(sector["reason"], "below 30 postings / 8 employers")
        self.assertEqual((sector["evidence"]["postings"], sector["evidence"]["employers"]), (12, 5))
        shifts = self.client.get("/v1/shifts?since=2026-07").json()
        self.assertEqual(shifts["history_status"], "not_recorded")
        self.assertEqual(shifts["state_changes"], [])
        self.assertEqual(len(shifts["uncompared_roles"]), 3)

    def test_current_shifts_answer_the_question_without_snapshot_history(self) -> None:
        payload = self.client.get("/v1/shifts?since=2026-Q2").json()
        current = payload["current"]
        self.assertEqual(len(current), 9)
        self.assertEqual({item["status"] for item in current}, {"rising", "early", "fading"})
        self.assertNotIn("core", {item["status"] for item in current})
        ai = next(item for item in current if item["role_id"] == "role.account-executive" and item["skill_id"] == "skill.aires")
        self.assertEqual(ai["status"], "rising")
        self.assertEqual(ai["short_label"], "AI account research")
        self.assertEqual([window["window"] for window in ai["windows"]], ["2026-Q2", "2026-Q3"])
        self.assertEqual([window["employers"] for window in ai["windows"]], [6, 23])
        self.assertAlmostEqual(ai["windows"][-1]["share"], 58 / 412)
        self.assertEqual(ai["forecast"]["probability"], 0.72)
        self.assertEqual(ai["forecast"]["base_rate"], 0.62)
        self.assertEqual(ai["forecast"]["resolves"], "2027-Q3")
        self.assertIn("M", ai["evidence_families"])
        self.assertTrue(any(item["family"] == "M" and item["direction"] == "up" for item in ai["evidence"]))
        order = {"rising": 0, "early": 1, "fading": 2}
        scores = [(order[item["status"]], -item["forecast"]["probability"]) for item in current]
        self.assertEqual(scores, sorted(scores))
        for item in current:
            self.assertIn("disputed", item)
            self.assertEqual(item["release_id"], FIXTURE_RELEASE)
            cited = self.client.get(item["cite"])
            self.assertEqual(cited.status_code, 200)
            self.assertEqual(cited.json()["skill"]["id"], item["skill_id"])
            self.assertEqual(cited.json()["citation_id"], item["skill_id"] + "@" + FIXTURE_RELEASE)
            self.assertEqual(cited.json()["fixture_caveat"], FIXTURE_CAVEAT)
        self.assertEqual(payload["state_changes"], [])
        self.assertEqual(payload["history_status"], "not_recorded")
        self.assertEqual(self.client.get("/v1/shifts?since=2026-Q3").json()["current"], [])
        self.assertEqual(self.client.get("/v1/shifts?since=2026-Q4").json()["current"], [])

    def test_every_expectation_citation_resolves_without_inventing_quotes(self) -> None:
        for record in self.reads.catalog.list_roles():
            for level in record["ladder"]["levels"]:
                for expectation in level["expectations"]:
                    with self.subTest(cite=expectation["cite"]):
                        response = self.client.get(expectation["cite"])
                        self.assertEqual(response.status_code, 200)
                        payload = response.json()
                        self.assertEqual(payload["citation_id"], expectation["id"] + "@" + record["release_id"])
                        self.assertEqual(payload["expectation"]["label"], expectation["label"])
                        self.assertTrue(any(item["role_id"] == record["id"] and item["level"] == level["level"] for item in payload["occurrences"]))
                        self.assertEqual(payload["postings"], [])
                        self.assertEqual(payload["excerpts"], [])
                        self.assertEqual(payload["evidence_status"], "not_recorded")
                        self.assertEqual(payload["fixture_caveat"], FIXTURE_CAVEAT)

    def test_unknown_entities_and_pins_are_structured_404s(self) -> None:
        for path, status in (
            ("/v1/roles/missing", "unknown_role"),
            ("/v1/roles/forward-deployed-engineer/levels/L5", "unknown_level"),
            ("/v1/roles/account-executive/industries/00", "unknown_industry"),
            ("/v1/expectations/exp.missing@" + FIXTURE_RELEASE, "unknown_expectation"),
            ("/v1/roles?release=missing", "unknown_release"),
            ("/v1/roles/account-executive?release=missing", "unknown_release"),
            ("/v1/expectations/exp.missing@missing", "unknown_release"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["status"], status)
                self.assertIsInstance(response.json()["detail"], str)
                self.assertEqual(response.json()["fixture_caveat"], FIXTURE_CAVEAT)

    def test_bad_inputs_mutations_head_and_body_bounds(self) -> None:
        for path in (
            "/v1/roles/INVALID", "/v1/roles/" + "a" * 70,
            "/v1/roles/account-executive?release=../current", "/v1/roles?release=",
            "/v1/roles/account-executive/levels/nope", "/v1/roles/account-executive/industries/nope",
            "/v1/shifts", "/v1/shifts?since=2026-13", "/v1/shifts?since=2026-Q5",
            "/v1/roles?release=one&release=two", "/v1/shifts?since=2026-Q1&since=2026-Q2",
            "/v1/expectations/exp.missing", "/v1/expectations/exp.missing@" + FIXTURE_RELEASE + "?release=other",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["status"], "invalid_request")
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self.assertEqual(self.client.request(method, "/v1/roles").status_code, 405)
        self.assertEqual(self.client.request("GET", "/v1/roles", content=b"x" * 9000).status_code, 413)
        head = self.client.head("/v1/roles")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(self.client.get("/v1/not-a-route").status_code, 404)
        self.assertEqual(self.client.get("/api/health").json()["status"], "no_release")

    def test_disabled_is_not_a_fixture_fallback_even_with_query_flags(self) -> None:
        for target in ("/v1/roles", "/v1/roles?fixtures=true", "/v1/roles/account-executive"):
            response = handle_request("GET", target, store=self.legacy)
            self.assertEqual(response.status, 503)
            self.assertEqual(json.loads(response.body)["status"], "role_catalog_unavailable")
            self.assertIsNone(json.loads(response.body)["fixture_caveat"])
        bound = handler_for_release_root(self.legacy.root, role_catalog=self.reads)
        self.assertEqual(json.loads(bound("GET", "/v1/roles").body), self.client.get("/v1/roles").json())


class RoleAtlasPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        source = RoleCatalogStore.fixtures().role("account-executive")
        old = synthetic_snapshot(source, "fixture_old", {"2026-Q3": "2026-Q2", "2026-Q2": "2026-Q1", "2027-Q3": "2027-Q2"})
        new = synthetic_snapshot(source, "fixture_new", {})
        later = synthetic_snapshot(source, "fixture_later", {"2026-Q3": "2026-Q4", "2026-Q2": "2026-Q3", "2027-Q3": "2027-Q4"})
        new["label"] += " (new)"
        for record in (old, later):
            skill = next(item for item in record["skill_map"]["skills"] if item["id"] == "skill.aires")
            skill["windows"] = skill["windows"][-1:]
            skill["status"] = "early"
        for record in (old, new, later):
            model = RoleRecord.from_dict(record)
            (cls.root / f"{record['release_id']}.json").write_text(model.to_json(), encoding="utf-8")
        cls.reads = RoleAtlasReads(RoleCatalogStore(cls.root), fixtures=True)
        cls.client = TestClient(create_market_app(CatalogStore(cls.root / "legacy"), role_catalog=cls.reads))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    def test_all_subresources_respect_requested_release_not_latest(self) -> None:
        old = self.client.get("/v1/roles/account-executive?release=fixture_old").json()
        new = self.client.get("/v1/roles/account-executive?release=fixture_new").json()
        self.assertEqual(old["role"]["label"], "Account Executive")
        self.assertEqual(new["role"]["label"], "Account Executive (new)")
        self.assertEqual(self.client.get("/v1/roles").json()["release_id"], "fixture_later")
        for suffix, key in (("", "role"), ("/levels/L1", "level"), ("/graph", "graph"), ("/industries/51", "industry")):
            response = self.client.get("/v1/roles/account-executive" + suffix + "?release=fixture_old")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["release_id"], "fixture_old")
            self.assertIn(key, response.json())
        expectation = old["role"]["ladder"]["levels"][0]["expectations"][0]
        cited = self.client.get(expectation["cite"]).json()
        self.assertEqual(cited["release_id"], "fixture_old")
        self.assertEqual(cited["expectation"]["cite"], expectation["cite"])
        self.assertEqual(self.client.get("/v1/roles?release=unknown").status_code, 404)

    def test_partial_history_reports_uncompared_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for release in ("fixture_old", "fixture_new"):
                (root / f"{release}.json").write_text((self.root / f"{release}.json").read_text(), encoding="utf-8")
            fde = synthetic_snapshot(RoleCatalogStore.fixtures().role("forward-deployed-engineer"), "fixture_new", {})
            (root / "fde.json").write_text(RoleRecord.from_dict(fde).to_json(), encoding="utf-8")
            reads = RoleAtlasReads(RoleCatalogStore(root), fixtures=True)
            payload = reads.shifts("2026-Q2")
            self.assertEqual(payload["history_status"], "partial")
            self.assertEqual(payload["uncompared_roles"], ["role.forward-deployed-engineer"])
            self.assertEqual(len(payload["state_changes"]), 1)

    def test_shifts_use_history_exclusive_since_and_inclusive_release_pin(self) -> None:
        response = self.client.get("/v1/shifts?since=2026-Q2&release=fixture_new")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["history_status"], "available")
        self.assertEqual(len(payload["state_changes"]), 1)
        change = payload["state_changes"][0]
        self.assertEqual((change["skill_id"], change["previous_status"], change["status"], change["release_id"]),
                         ("skill.aires", "early", "rising", "fixture_new"))
        self.assertEqual(change["data_kind"], "fixture")
        self.assertEqual(change["fixture_caveat"], FIXTURE_CAVEAT)
        self.assertEqual(self.client.get("/v1/shifts?since=2026-07&release=fixture_new").json()["state_changes"], [])
        self.assertEqual(self.client.get("/v1/shifts?since=2026-Q4").json()["state_changes"], [])
        changes = self.client.get("/v1/shifts?since=2026-Q2").json()["state_changes"]
        self.assertEqual([(item["previous_status"], item["status"]) for item in changes], [("early", "rising"), ("rising", "early")])
        self.assertEqual(self.client.get("/v1/shifts?since=2026-Q1&release=fixture_old").json()["history_status"], "not_recorded")


class RoleAtlasMcpTests(unittest.TestCase):
    def test_unselected_mcp_returns_the_same_unavailable_payload_as_rest(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as directory:
                app = create_market_app(CatalogStore(Path(directory)))
                async with app.router.lifespan_context(app), httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000",
                ) as http:
                    async with streamable_http_client("http://127.0.0.1:8000/api/mcp", http_client=http) as (incoming, outgoing):
                        async with ClientSession(incoming, outgoing) as session:
                            await session.initialize()
                            result = await session.call_tool("list_roles", {})
                            rest = await http.get("/v1/roles?fixtures=true")
                            self.assertEqual(rest.status_code, 503)
                            self.assertEqual(result.structured_content, rest.json())
                            self.assertIsNone(result.structured_content["fixture_caveat"])
                            self.assertNotIn("roles", result.structured_content)
                            tool = next(tool for tool in (await session.list_tools()).tools if tool.name == "list_roles")
                            self.assertNotIn("fixtures", tool.input_schema["properties"])
        anyio.run(exercise)

    def test_sdk_calls_every_pair_with_identical_json_including_errors(self) -> None:
        reads = RoleAtlasReads.configured(fixtures=True)
        record = reads.catalog.role("account-executive")
        expectation_id = record["ladder"]["levels"][0]["expectations"][0]["id"]
        pairs = [
            ("/v1/roles", "list_roles", {}),
            ("/v1/roles/account-executive", "get_role", {"role": "account-executive"}),
            ("/v1/roles/account-executive/levels/L3", "get_role_level", {"role": "account-executive", "level": "L3"}),
            ("/v1/roles/account-executive/graph", "get_role_graph", {"role": "account-executive"}),
            ("/v1/roles/account-executive/industries/31-33", "get_role_industry", {"role": "account-executive", "naics": "31-33"}),
            ("/v1/shifts?since=2026-Q2", "list_shifts", {"since": "2026-Q2"}),
            (f"/v1/expectations/{expectation_id}@{FIXTURE_RELEASE}", "get_expectation", {"expectation_id": expectation_id, "release_id": FIXTURE_RELEASE}),
            (f"/v1/expectations/skill.aires@{FIXTURE_RELEASE}", "get_expectation", {"expectation_id": "skill.aires", "release_id": FIXTURE_RELEASE}),
            ("/v1/roles/missing", "get_role", {"role": "missing"}),
            ("/v1/roles/forward-deployed-engineer/levels/L5", "get_role_level", {"role": "forward-deployed-engineer", "level": "L5"}),
            ("/v1/roles/account-executive/industries/00", "get_role_industry", {"role": "account-executive", "naics": "00"}),
            ("/v1/shifts?since=invalid", "list_shifts", {"since": "invalid"}),
            ("/v1/roles?release=missing", "list_roles", {"release_id": "missing"}),
        ]
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as directory:
                app = create_market_app(CatalogStore(Path(directory)), role_catalog=reads)
                async with app.router.lifespan_context(app), httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000",
                ) as http:
                    async with streamable_http_client("http://127.0.0.1:8000/api/mcp", http_client=http) as (incoming, outgoing):
                        async with ClientSession(incoming, outgoing) as session:
                            await session.initialize()
                            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                            for _, tool, _ in pairs:
                                self.assertTrue(tools[tool].annotations.read_only_hint)
                                self.assertFalse(tools[tool].annotations.open_world_hint)
                            for path, tool, arguments in pairs:
                                with self.subTest(tool=tool, path=path):
                                    response = await http.get(path)
                                    result = await session.call_tool(tool, arguments)
                                    self.assertFalse(result.is_error)
                                    self.assertEqual(result.structured_content, response.json())
                                    if response.status_code == 200:
                                        self.assertEqual(result.structured_content["release_id"], FIXTURE_RELEASE)
                                        self.assertEqual(result.structured_content["fixture_caveat"], FIXTURE_CAVEAT)
                            for path, tool, arguments in pairs[:6]:
                                pinned_path = path + ("&" if "?" in path else "?") + "release=" + FIXTURE_RELEASE
                                result = await session.call_tool(tool, {**arguments, "release_id": FIXTURE_RELEASE})
                                self.assertEqual(result.structured_content, (await http.get(pinned_path)).json())
        anyio.run(exercise)


class RoleAtlasConfigTests(unittest.TestCase):
    def test_environment_selection_is_explicit_and_conflicts_fail_closed(self) -> None:
        for env, fixture in (({}, False), ({"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "0"}, False),
                             ({"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "1"}, True),
                             ({"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "true"}, True)):
            with patch("skills_vector.market.role_reads.os.environ", env):
                selected = role_atlas_from_environment()
                self.assertEqual(selected.fixtures, fixture)
                self.assertEqual(selected.list_roles()["status"], "ok" if fixture else "role_catalog_unavailable")
        with tempfile.TemporaryDirectory() as directory:
            with patch("skills_vector.market.role_reads.os.environ", {"SKILLS_VECTOR_ROLE_ATLAS_ROOT": directory}):
                selected = role_atlas_from_environment()
                self.assertEqual(selected.catalog.root, Path(directory))
                self.assertFalse(selected.fixtures)
        for env in ({"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "typo"},
                    {"SKILLS_VECTOR_ROLE_ATLAS_ROOT": ""},
                    {"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "1", "SKILLS_VECTOR_ROLE_ATLAS_ROOT": "/unused"}):
            with patch("skills_vector.market.role_reads.os.environ", env), self.assertRaises(ValueError):
                role_atlas_from_environment()

    def test_research_mode_rejects_fixture_records_and_invalid_catalogs(self) -> None:
        selected = RoleAtlasReads.configured(FIXTURE_ROOT)
        self.assertEqual(selected.list_roles()["status"], "role_catalog_unavailable")
        self.assertIsNone(selected.list_roles()["fixture_caveat"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reads = RoleAtlasReads.configured(root)
            self.assertEqual(reads.list_roles()["status"], "role_catalog_unavailable")
            # Simulate a future research producer's kind in a temporary test-only
            # record. No test record is published or written into a release tree.
            record = RoleCatalogStore.fixtures().role("hr-generalist")
            record["data_kind"] = "research"
            (root / "role.json").write_text(json.dumps(record), encoding="utf-8")
            payload = reads.role("hr-generalist")
            self.assertEqual(payload["data_kind"], "research")
            self.assertIsNone(payload["fixture_caveat"])
            self.assertEqual(payload["role"]["label"], "HR Generalist")
            (root / "broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(reads.list_roles()["status"], "role_catalog_unavailable")
        self.assertEqual(reads.list_roles()["status"], "role_catalog_unavailable")

    def test_deployed_entrypoint_and_rewrites_select_the_same_catalog(self) -> None:
        for env, expected in (({}, 503), ({"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "1"}, 200)):
            with patch("skills_vector.market.role_reads.os.environ", env), patch.object(sys, "path", list(sys.path)):
                spec = importlib.util.spec_from_file_location("role_atlas_entrypoint", ROOT / "api" / "index.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with TestClient(module.app) as client:
                    response = client.get("/v1/roles")
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(response.json()["fixture_caveat"], FIXTURE_CAVEAT if expected == 200 else None)
        rewrites = json.loads((ROOT / "vercel.json").read_text())["rewrites"]
        self.assertIn({"source": "/v1/(.*)", "destination": "/api/index"}, rewrites)

    def test_local_server_and_cli_flags_select_fixtures_explicitly(self) -> None:
        parser = build_parser()
        for command in ("serve", "mcp"):
            args = parser.parse_args([command, "--role-atlas-fixtures"])
            self.assertTrue(args.role_atlas_fixtures)
            self.assertFalse(parser.parse_args([command]).role_atlas_fixtures)
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                parser.parse_args([command, "--role-atlas-fixtures", "--role-atlas-root", "/unused"])
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(ROOT, Path(directory), role_atlas_fixtures=True)
            with TestClient(server.config.app) as client:
                self.assertEqual(client.get("/v1/roles").json()["fixture_caveat"], FIXTURE_CAVEAT)
            with patch("skills_vector.market.server.run_server", return_value=0) as start:
                self.assertEqual(run(["serve", "--role-atlas-fixtures"]), 0)
                self.assertTrue(start.call_args.kwargs["role_atlas_fixtures"])
            with patch("builtins.print") as output:
                self.assertEqual(run(["mcp", "--release-root", directory, "--role-atlas-fixtures", "--print-tools"]), 0)
                self.assertIn("get_role", output.call_args.args[0])
                self.assertEqual(mcp_main(["--release-root", directory, "--role-atlas-fixtures", "--print-tools"]), 0)
                self.assertIn("get_role", output.call_args.args[0])

    def test_agent_guide_and_schema_document_every_read_and_caveat(self) -> None:
        schema = api_schema()
        endpoints = {endpoint["path"] for endpoint in schema["endpoints"]}
        tools = {tool["name"] for tool in schema["mcp_tools"]}
        for endpoint in ("/v1/roles", "/v1/roles/{role}", "/v1/roles/{role}/levels/{L}",
                         "/v1/roles/{role}/graph", "/v1/roles/{role}/industries/{naics}",
                         "/v1/shifts", "/v1/expectations/{id}@{release}"):
            self.assertIn(endpoint, endpoints)
        for tool in ("list_roles", "get_role", "get_role_level", "get_role_graph", "get_role_industry", "list_shifts", "get_expectation"):
            self.assertIn(tool, tools)
            self.assertIn(tool, agent_guide_markdown())
        guide = agent_guide_markdown(base_url="https://example.test")
        for phrase in ("skills-vector.role/2", "fixture_caveat", "publication: experimental", "SKILLS_VECTOR_ROLE_ATLAS_FIXTURES=1",
                       "not_recorded", "no role/2 catalog by default", "https://example.test", "role-atlas-root",
                       "current", "state_changes", "probability", "skill ids"):
            self.assertIn(phrase, guide)


if __name__ == "__main__":
    unittest.main()
