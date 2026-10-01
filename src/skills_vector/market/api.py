"""Read-only HTTP API router shared by the local server and the Vercel function.

Contract: every route is a GET read of the published release tree or a
deterministic query over it. Public mutation methods are rejected (405) and
nothing here can launch research or write state; research handoff is returned
as a bounded plan for an operator to run locally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .core import INSPECTOR_SECTIONS, CatalogStore
from .guide import agent_guide_markdown, api_schema

MAX_QUERY_LENGTH = 400
MAX_LIMIT = 200
MAX_BODY_BYTES = 8 * 1024  # public reads accept no meaningful body
MUTATION_METHODS = ("POST", "PUT", "PATCH", "DELETE", "CONNECT", "TRACE")


@dataclass
class ApiResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "content_type": self.content_type, "bytes": len(self.body)}


def _json(status: int, payload: Any) -> ApiResponse:
    return ApiResponse(status=status, body=json.dumps(payload, ensure_ascii=False, sort_keys=False).encode("utf-8"))


def _error(status: int, code: str, detail: str, **extra: Any) -> ApiResponse:
    return _json(status, {"status": code, "detail": detail, **extra})


def _text(status: int, payload: str, content_type: str = "text/plain; charset=utf-8") -> ApiResponse:
    return ApiResponse(status=status, body=payload.encode("utf-8"), content_type=content_type)


def _one(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key) or []
    if not values:
        return None
    return str(values[0]).strip()


def _bounded_int(params: dict[str, list[str]], key: str, default: int, maximum: int) -> int | None:
    raw = _one(params, key)
    if raw is None or raw == "":
        return default
    if not re.fullmatch(r"[0-9]{1,4}", raw):
        return None
    value = int(raw)
    if value < 1 or value > maximum:
        return None
    return value


def _flag(params: dict[str, list[str]], key: str) -> bool:
    raw = (_one(params, key) or "").lower()
    return raw in ("1", "true", "yes", "on")


def handle_request(
    method: str,
    target: str,
    *,
    body: bytes = b"",
    store: CatalogStore | None = None,
    store_factory: Callable[[], CatalogStore] | None = None,
    base_url: str = "",
) -> ApiResponse:
    """Route one API request. ``store_factory`` is resolved lazily per request."""

    method = (method or "GET").upper()
    split = urlsplit(target or "/")
    path = split.path.rstrip("/") or "/"
    params = parse_qs(split.query, keep_blank_values=True)

    if method in MUTATION_METHODS:
        return _error(
            405,
            "method_not_allowed",
            "This is a read-only public interface. Research and publishing run only as local operator commands.",
            allowed=["GET", "HEAD"],
        )
    if method not in ("GET", "HEAD"):
        return _error(405, "method_not_allowed", "Only GET/HEAD reads are served.", allowed=["GET", "HEAD"])
    if body and len(body) > MAX_BODY_BYTES:
        return _error(413, "payload_too_large", f"request body exceeds {MAX_BODY_BYTES} bytes")

    if not path.startswith("/api"):
        return _error(404, "not_found", f"no API route for {path!r}")

    catalog = store or (store_factory() if store_factory else None)
    if catalog is None:
        return _error(503, "release_unavailable", "release tree is not available to this function")

    route = path[len("/api") :] or "/"
    if route == "/health":
        return _json(200, catalog.health())
    if route == "/schema":
        return _json(200, api_schema(base_url=base_url))
    if route == "/agent-guide":
        return _text(200, agent_guide_markdown(base_url=base_url))
    if route == "/release":
        return _json(200, catalog.release_info(release_id=_one(params, "release")))
    if route == "/occupations":
        payload = catalog.list_occupations(release_id=_one(params, "release"))
        return _json(200, payload)
    if route == "/occupation":
        slug = _one(params, "slug") or _one(params, "occupation") or ""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
            return _error(400, "invalid_request", "slug must be a lowercase occupation slug")
        payload = catalog.occupation(slug, release_id=_one(params, "release"))
        status = 200 if payload.get("status") == "ok" else 404
        return _json(status, payload)
    if route == "/query":
        raw_query = _one(params, "q") or ""
        if len(raw_query) > MAX_QUERY_LENGTH:
            return _error(400, "invalid_request", f"q exceeds {MAX_QUERY_LENGTH} characters")
        occupation = _one(params, "occupation")
        if occupation is not None and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", occupation):
            return _error(400, "invalid_request", "occupation must be a lowercase occupation slug")
        limit = _bounded_int(params, "limit", 5, MAX_LIMIT)
        if limit is None:
            return _error(400, "invalid_request", f"limit must be an integer between 1 and {MAX_LIMIT}")
        payload = catalog.query(raw_query, occupation, limit=limit, release_id=_one(params, "release"))
        return _json(200, payload)
    if route == "/research-plan":
        raw_query = _one(params, "q") or ""
        if len(raw_query) > MAX_QUERY_LENGTH:
            return _error(400, "invalid_request", f"q exceeds {MAX_QUERY_LENGTH} characters")
        occupation = _one(params, "occupation")
        return _json(200, catalog.research_plan(raw_query, occupation))
    if route == "/claim":
        claim_id = _one(params, "id") or ""
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", claim_id):
            return _error(400, "invalid_request", "id must be a claim id")
        payload = catalog.claim(claim_id, release_id=_one(params, "release"))
        status = 200 if payload.get("status") == "ok" else 404
        return _json(status, payload)
    if route == "/citation":
        claim_id = _one(params, "id") or ""
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", claim_id):
            return _error(400, "invalid_request", "id must be a claim id")
        payload = catalog.citation(claim_id)
        status = 200 if payload.get("status") == "ok" else 404
        return _json(status, payload)
    if route == "/inspector":
        occupation = _one(params, "occupation")
        limit = _bounded_int(params, "limit", 20, MAX_LIMIT)
        if limit is None:
            return _error(400, "invalid_request", f"limit must be an integer between 1 and {MAX_LIMIT}")
        payload = catalog.inspector(
            occupation,
            limit=limit,
            release_id=_one(params, "release"),
            include_text=not _flag(params, "no_text"),
        )
        return _json(200, payload)
    if route == "/evidence":
        section = _one(params, "section") or ""
        if section != "all" and section not in INSPECTOR_SECTIONS:
            return _error(
                400,
                "invalid_request",
                f"section must be one of {list(INSPECTOR_SECTIONS)}",
                sections=list(INSPECTOR_SECTIONS),
            )
        occupation = _one(params, "occupation")
        limit = _bounded_int(params, "limit", 50, MAX_LIMIT)
        if limit is None:
            return _error(400, "invalid_request", f"limit must be an integer between 1 and {MAX_LIMIT}")
        payload = catalog.evidence(
            section,
            occupation,
            limit=limit,
            release_id=_one(params, "release"),
            include_text=not _flag(params, "no_text"),
        )
        return _json(200, payload)
    return _error(404, "not_found", f"no API route for {path!r}")


def handler_for_release_root(release_root: Path | str, *, base_url: str = "") -> Callable[..., ApiResponse]:
    """Bind a router callable to a release root (used by the local server)."""

    def _handle(method: str, target: str, *, body: bytes = b"") -> ApiResponse:
        return handle_request(
            method,
            target,
            body=body,
            store_factory=lambda: CatalogStore(Path(release_root), base_url=base_url),
            base_url=base_url,
        )

    return _handle


__all__ = [
    "ApiResponse",
    "MAX_BODY_BYTES",
    "MAX_LIMIT",
    "MAX_QUERY_LENGTH",
    "MUTATION_METHODS",
    "handle_request",
    "handler_for_release_root",
]
