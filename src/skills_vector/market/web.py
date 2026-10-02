"""ASGI composition for the local preview and deployed API/MCP surfaces.

One CatalogStore instance backs REST handlers and official-SDK MCP tools.
Public reads are bounded and deterministic; this module never performs
research, inference, or writes to the catalog.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from .api import MAX_BODY_BYTES, handle_request
from .core import CatalogStore
from .mcp import create_mcp_server

MAX_MCP_BODY_BYTES = 16 * 1024
API_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]


def _safe_file(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def create_market_app(
    store: CatalogStore,
    *,
    worktree: Path | None = None,
    release_root: Path | None = None,
    serve_static: bool = False,
    public_host: bool = False,
):
    """Build a stateless-SDK HTTP app with REST, MCP, and optional local files."""

    server = create_mcp_server(store)
    roots = {
        "worktree": Path(worktree).resolve() if worktree is not None else None,
        "release": Path(release_root).resolve() if release_root is not None else None,
    }

    async def api_request(request: Request) -> Response:
        if request.method not in ("GET", "HEAD"):
            result = handle_request(request.method, request.url.path, store=store)
        else:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    payload = {
                        "status": "payload_too_large",
                        "detail": f"request body exceeds {MAX_BODY_BYTES} bytes",
                    }
                    return Response(
                        json.dumps(payload).encode("utf-8"),
                        status_code=413,
                        media_type="application/json",
                    )
                body.extend(chunk)
            result = handle_request(
                request.method,
                request.url.path + ("?" + request.url.query if request.url.query else ""),
                body=bytes(body),
                store=store,
                base_url=str(request.base_url).rstrip("/"),
            )
        return Response(
            result.body,
            status_code=result.status,
            headers={"content-type": result.content_type, **(result.headers or {})},
        )

    # These custom routes are registered after the SDK's exact MCP endpoint;
    # only the MCP endpoint can accept protocol POST/GET/DELETE exchanges.
    server.custom_route("/api", methods=API_METHODS)(api_request)
    server.custom_route("/api/{path:path}", methods=API_METHODS)(api_request)

    if serve_static:
        async def index_file(request: Request) -> Response:
            root = roots["worktree"]
            if root is None:
                return Response("static surface unavailable", status_code=404, media_type="text/plain")
            path = root / "preview" / "index.html"
            return FileResponse(path) if path.is_file() else Response("not found", status_code=404)

        async def static_file(request: Request) -> Response:
            root = roots["worktree"]
            release = roots["release"]
            request_path = request.url.path
            if request_path in ("/", "/index.html"):
                return await index_file(request)
            if request_path.startswith("/release/") and release is not None:
                path = _safe_file(release, request_path.removeprefix("/release/"))
            elif request_path.startswith("/design-system/") and root is not None:
                path = _safe_file(
                    root / "design-concepts" / "design-system",
                    request_path.removeprefix("/design-system/"),
                )
            else:
                path = None
            return (
                FileResponse(path)
                if path is not None
                else Response("not found\n", status_code=404, media_type="text/plain")
            )

        server.custom_route("/", methods=["GET", "HEAD"])(index_file)
        server.custom_route("/{path:path}", methods=["GET", "HEAD"])(static_file)

    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if public_host else None
    return server.streamable_http_app(
        streamable_http_path="/api/mcp",
        stateless_http=True,
        max_request_body_size=MAX_MCP_BODY_BYTES,
        transport_security=transport_security,
        host="0.0.0.0" if public_host else "127.0.0.1",
    )


__all__ = ["create_market_app", "MAX_MCP_BODY_BYTES"]
