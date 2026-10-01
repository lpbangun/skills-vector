"""Deployed-surface behavior: local HTTP server routes, bounds, MCP JSON-RPC, remote reads."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_support import build_slice, default_claim_id, publish_test_release  # noqa: E402

from skills_vector.market.core import CatalogStore  # noqa: E402
from skills_vector.market.mcp import LocalBackend, McpServer # noqa: E402
from skills_vector.market.server import build_server  # noqa: E402

WORKTREE = Path(__file__).resolve().parents[1]


def _get(url: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - local test server
            return response.status, response.read(), response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "")


def _post(url: str, body: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class MarketServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name) / "release"
        root.mkdir(parents=True)
        (root / "current.json").write_text(json.dumps({"schema_version": 1, "current": None, "releases": []}), encoding="utf-8")
        cls.publish = publish_test_release(root, build_slice(Path(cls._tmp.name) / "slice"))
        cls.release_root = root
        cls.store = CatalogStore(root)
        cls.server = build_server(WORKTREE, root, host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_health_and_occupations(self) -> None:
        status, body, _ = _get(self.url("/api/health"))
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["release_id"], self.publish["release_id"])

        status, body, _ = _get(self.url("/api/occupations"))
        self.assertEqual(status, 200)
        occupations = json.loads(body)
        self.assertEqual([occ["slug"] for occ in occupations["occupations"]], ["hr-generalist"])

    def test_query_negative_paths_are_honest(self) -> None:
        status, body, _ = _get(self.url("/api/query?q=onboarding"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "cited_evidence")

        status, body, _ = _get(self.url("/api/query?q=quantum%20welding%20certification"))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "unsupported_question")
        self.assertIn("handoff", payload)
        self.assertNotIn("fixture", body.decode("utf-8").lower())

        status, body, _ = _get(self.url("/api/query?q=onboarding&occupation=account-executive"))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "insufficient_evidence")
        self.assertIn("handoff", payload)

    def test_mutations_rejected_and_bounds_enforced(self) -> None:
        status, _ = _post(self.url("/api/query?q=onboarding"), b'{"q": "onboarding"}')
        self.assertEqual(status, 405)
        status, _ = _post(self.url("/api/release"), b"x" * 100)
        self.assertEqual(status, 405)
        status, body, _ = _get(self.url("/api/query?q=" + "a" * 500))
        self.assertEqual(status, 400)
        status, body, _ = _get(self.url("/api/evidence?section=nope"))
        self.assertEqual(status, 400)

    def test_citation_routes_and_traversal_rejection(self) -> None:
        claim_id = default_claim_id()
        status, body, content_type = _get(self.url(f"/release/citations/{claim_id}.json"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["claim_id"], claim_id)

        status, _, _ = _get(self.url("/release/releases/../../.gitignore"))
        self.assertEqual(status, 404)


class MarketMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name) / "release"
        root.mkdir(parents=True)
        (root / "current.json").write_text(json.dumps({"schema_version": 1, "current": None, "releases": []}), encoding="utf-8")
        publish_test_release(root, build_slice(Path(cls._tmp.name) / "slice"))
        cls.release_root = root
        cls.backend = LocalBackend(root)
        cls.server = McpServer(cls.backend)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_protocol_errors(self) -> None:
        missing = self.server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "no/such"})
        self.assertEqual(missing["error"]["code"], -32601)
        bad_tool = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "does_not_exist", "arguments": {}}}
        )
        self.assertTrue(bad_tool["result"]["isError"])
        missing_arg = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "get_claim", "arguments": {}}}
        )
        self.assertTrue(missing_arg["result"]["isError"])
        notification = self.server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(notification)

    def test_stdio_framing(self) -> None:
        stdin = StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n"
        )
        stdout = StringIO()
        self.server.serve_stdio(stdin=stdin, stdout=stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["id"], 2)


if __name__ == "__main__":
    unittest.main()
