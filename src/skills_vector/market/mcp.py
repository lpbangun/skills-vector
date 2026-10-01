"""MCP (Model Context Protocol) stdio server over the shared market core.

Real JSON-RPC 2.0: ``initialize``, ``tools/list``, ``tools/call``, ``ping``.
Tools read either a local release tree (``--release-root``) or the deployed read
API (``--base-url``), so an independent agent context can consume the deployed
surface directly. No tool mutates state.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .api import MAX_LIMIT
from .core import INSPECTOR_SECTIONS, CatalogStore
from .guide import api_schema

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "skills-vector-market", "version": "1.0.0"}


class Backend(Protocol):
    def release_info(self) -> dict[str, Any]: ...
    def list_occupations(self) -> dict[str, Any]: ...
    def occupation(self, slug: str) -> dict[str, Any]: ...
    def query(self, q: str, occupation: str | None, limit: int) -> dict[str, Any]: ...
    def claim(self, claim_id: str) -> dict[str, Any]: ...
    def evidence(self, section: str, occupation: str | None, limit: int, include_text: bool) -> dict[str, Any]: ...
    def research_plan(self, q: str, occupation: str | None) -> dict[str, Any]: ...


class LocalBackend:
    def __init__(self, release_root: Path) -> None:
        self.store = CatalogStore(release_root)

    def release_info(self) -> dict[str, Any]:
        return self.store.release_info()

    def list_occupations(self) -> dict[str, Any]:
        return self.store.list_occupations()

    def occupation(self, slug: str) -> dict[str, Any]:
        return self.store.occupation(slug)

    def query(self, q: str, occupation: str | None, limit: int) -> dict[str, Any]:
        return self.store.query(q, occupation, limit=limit)

    def claim(self, claim_id: str) -> dict[str, Any]:
        return self.store.claim(claim_id)

    def evidence(self, section: str, occupation: str | None, limit: int, include_text: bool) -> dict[str, Any]:
        return self.store.evidence(section, occupation, limit=limit, include_text=include_text)

    def research_plan(self, q: str, occupation: str | None) -> dict[str, Any]:
        return self.store.research_plan(q, occupation)


class RemoteBackend:
    """Read the deployed API (stdlib urllib; GET only)."""

    def __init__(self, base_url: str, *, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        clean = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
        request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": SERVER_INFO["name"]})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - operator-supplied base URL
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                return {"status": "error", "detail": f"http {exc.code} from {url}"}
        except (urllib.error.URLError, TimeoutError) as exc:
            return {"status": "error", "detail": f"unreachable {url}: {exc}"}

    def release_info(self) -> dict[str, Any]:
        return self._get("/api/release")

    def list_occupations(self) -> dict[str, Any]:
        return self._get("/api/occupations")

    def occupation(self, slug: str) -> dict[str, Any]:
        return self._get("/api/occupation", {"slug": slug})

    def query(self, q: str, occupation: str | None, limit: int) -> dict[str, Any]:
        return self._get("/api/query", {"q": q, "occupation": occupation, "limit": limit})

    def claim(self, claim_id: str) -> dict[str, Any]:
        return self._get("/api/claim", {"id": claim_id})

    def evidence(self, section: str, occupation: str | None, limit: int, include_text: bool) -> dict[str, Any]:
        return self._get(
            "/api/evidence",
            {"section": section, "occupation": occupation, "limit": limit, "no_text": None if include_text else "1"},
        )

    def research_plan(self, q: str, occupation: str | None) -> dict[str, Any]:
        return self._get("/api/research-plan", {"q": q, "occupation": occupation})


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


SLUG = {"type": "string", "description": "occupation slug (hr-generalist, growth-manager, account-executive)"}

TOOLS: list[dict[str, Any]] = [
    _tool("release_info", "Current release id, scope, version history and citation pattern.", {}),
    _tool("list_occupations", "Roles in the current release with scope, counts and deduplicated statistics.", {}),
    _tool("get_occupation", "Role page bundle: foundations, advertised demand (with variant split), learning priorities, claims and lineage.", {"slug": SLUG}, ["slug"]),
    _tool(
        "search_evidence",
        "Cited answer over stored evidence. Returns status cited_evidence | insufficient_evidence | unsupported_question plus citation urls; never fabricates an answer.",
        {
            "query": {"type": "string"},
            "occupation": SLUG,
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
        },
        ["query"],
    ),
    _tool("get_claim", "A stored claim with byte-verified quote, sources and immutable citation url.", {"claim_id": {"type": "string"}}, ["claim_id"]),
    _tool(
        "inspect_evidence",
        "Evidence inspector rows: extracts, admissions, exclusions, mappings, disagreements, lineage (or all sections in one document).",
        {
            "section": {"type": "string", "enum": [*INSPECTOR_SECTIONS, "all"]},
            "occupation": SLUG,
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            "include_text": {"type": "boolean"},
        },
        ["section"],
    ),
    _tool("research_plan", "Bounded local research handoff plan for an unanswered question (read-only; never launches research).", {"query": {"type": "string"}, "occupation": SLUG}, ["query"]),
]


def call_tool(backend: Backend, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    arguments = arguments if isinstance(arguments, dict) else {}
    try:
        if name == "release_info":
            payload = backend.release_info()
        elif name == "list_occupations":
            payload = backend.list_occupations()
        elif name == "get_occupation":
            slug = str(arguments.get("slug") or "").strip()
            if not slug:
                return {"content": [{"type": "text", "text": json.dumps({"status": "invalid_params", "detail": "slug is required"})}], "isError": True}
            payload = backend.occupation(slug)
        elif name == "search_evidence":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return {"content": [{"type": "text", "text": json.dumps({"status": "invalid_params", "detail": "query is required"})}], "isError": True}
            limit = arguments.get("limit")
            limit = int(limit) if isinstance(limit, int) and 1 <= limit <= MAX_LIMIT else 5
            payload = backend.query(query, arguments.get("occupation") or None, limit)
        elif name == "get_claim":
            claim_id = str(arguments.get("claim_id") or "").strip()
            if not claim_id:
                return {"content": [{"type": "text", "text": json.dumps({"status": "invalid_params", "detail": "claim_id is required"})}], "isError": True}
            payload = backend.claim(claim_id)
        elif name == "inspect_evidence":
            section = str(arguments.get("section") or "").strip()
            if section != "all" and section not in INSPECTOR_SECTIONS:
                return {"content": [{"type": "text", "text": json.dumps({"status": "invalid_params", "detail": f"section must be one of {[*INSPECTOR_SECTIONS, 'all']}"})}], "isError": True}
            limit = arguments.get("limit")
            limit = int(limit) if isinstance(limit, int) and 1 <= limit <= MAX_LIMIT else 50
            payload = backend.evidence(section, arguments.get("occupation") or None, limit, bool(arguments.get("include_text", True)))
        elif name == "research_plan":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return {"content": [{"type": "text", "text": json.dumps({"status": "invalid_params", "detail": "query is required"})}], "isError": True}
            payload = backend.research_plan(query, arguments.get("occupation") or None)
        else:
            return {"content": [{"type": "text", "text": json.dumps({"status": "unknown_tool", "detail": f"no tool named {name!r}"})}], "isError": True}
    except Exception as exc:  # noqa: BLE001 - report tool failure as a tool result
        return {"content": [{"type": "text", "text": json.dumps({"status": "tool_error", "detail": str(exc)})}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": False}


class McpServer:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.initialized = False

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        method = message.get("method")
        message_id = message.get("id")
        if method is None:
            return {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32600, "message": "missing method"}}
        if message_id is None:
            # notification: no response
            return None
        if method == "initialize":
            self.initialized = True
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "Read-only occupational evidence. search_evidence returns cited answers or an honest insufficient/unsupported status with a bounded local research handoff.",
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": message_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            result = call_tool(self.backend, str(params.get("name") or ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": message_id, "result": result}
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    def serve_stdio(self, stdin=None, stdout=None) -> int:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
                stdout.flush()
                continue
            response = self.handle_message(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
        return 0


def build_backend(*, release_root: str | None, base_url: str | None) -> Backend:
    if base_url:
        return RemoteBackend(base_url)
    if release_root:
        return LocalBackend(Path(release_root))
    raise SystemExit("market mcp requires --base-url <deployed url> or --release-root <dir>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--print-tools", action="store_true", help="print tool definitions and exit")
    args = parser.parse_args(argv)
    if args.print_tools:
        print(json.dumps({"protocol_version": PROTOCOL_VERSION, "schema": api_schema(), "tools": TOOLS}, indent=2))
        return 0
    backend = build_backend(release_root=args.release_root, base_url=args.base_url)
    return McpServer(backend).serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
