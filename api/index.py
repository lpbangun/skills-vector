"""Vercel serverless entry (stdlib only): read-only market API.

Every request is a bounded read: the function loads the immutable release tree
(local files bundled via ``includeFiles``, or the deployment's own static
``/release/...`` files as a fallback) and answers through the shared core. It
never runs inference, research, or mutations; mutation methods are rejected by
the router with 405.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

MAX_BODY = 8192


def _locate_root() -> Path | None:
    candidates: list[Path] = []
    env_root = os.environ.get("SKILLS_VECTOR_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    here = Path(__file__).resolve()
    candidates.extend([here.parent.parent, *list(here.parents)])
    for candidate in candidates:
        try:
            if (candidate / "src" / "skills_vector" / "market" / "api.py").is_file():
                return candidate
        except OSError:
            continue
    return None


ROOT = _locate_root()
if ROOT is not None:
    sys.path.insert(0, str(ROOT / "src"))

try:  # pragma: no cover - import failure is reported as an honest 503
    from skills_vector.market.api import MAX_BODY_BYTES, handle_request
    from skills_vector.market.core import CatalogStore
    from skills_vector.market.remote import RemoteCatalogStore
except Exception as exc:  # noqa: BLE001
    MAX_BODY_BYTES = MAX_BODY
    handle_request = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""


def _release_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_root = os.environ.get("SKILLS_VECTOR_RELEASE_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    if ROOT is not None:
        candidates.append(ROOT / "preview" / "release")
    return candidates


def _store():
    for candidate in _release_candidates():
        try:
            if (candidate / "current.json").is_file():
                return CatalogStore(candidate)
        except OSError:
            continue
    vercel_url = os.environ.get("VERCEL_URL")
    if vercel_url:
        return RemoteCatalogStore(f"https://{vercel_url}")
    return None


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel contract
    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(min(length, MAX_BODY_BYTES + 1))

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve(self) -> None:
        if handle_request is None:
            payload = {
                "status": "release_unavailable",
                "detail": f"market core import failed in the serverless bundle: {_IMPORT_ERROR}",
            }
            self._respond(503, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        try:
            store = _store()
        except Exception as exc:  # noqa: BLE001 - honest failure, no fallback content
            payload = {"status": "release_unavailable", "detail": f"release store unavailable: {exc}"}
            self._respond(503, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        if store is None:
            payload = {
                "status": "release_unavailable",
                "detail": "no release tree found for this deployment; publish a release and redeploy",
            }
            self._respond(503, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        try:
            response = handle_request(
                self.command or "GET",
                self.path,
                body=self._read_body(),
                store=store,
            )
        except Exception as exc:  # noqa: BLE001 - report, never fabricate
            payload = {"status": "internal_error", "detail": str(exc)}
            self._respond(500, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        headers = response.headers or {}
        self.send_response(response.status)
        self.send_header("content-type", response.content_type)
        self.send_header("content-length", str(len(response.body)))
        self.send_header("cache-control", "no-store")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def do_GET(self) -> None:  # noqa: N802 - Vercel contract
        self._serve()

    def do_HEAD(self) -> None:  # noqa: N802 - Vercel contract
        self._serve()

    def do_POST(self) -> None:  # noqa: N802 - Vercel contract
        self._serve()

    def do_PUT(self) -> None:  # noqa: N802 - Vercel contract
        self._serve()

    def do_PATCH(self) -> None:  # noqa: N802 - Vercel contract
        self._serve()

    def do_DELETE(self) -> None:  # noqa: N802 - Vercel contract
        self._serve()
