"""Local read-only server: /api/* routes plus the static preview surface.

Serves exactly what the deployed preview serves (static files + the same API
router), so browser journeys, CLI and MCP can be exercised locally without any
inference or mutation.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .api import MAX_BODY_BYTES, handle_request
from .core import CatalogStore

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def resolve_static(path: str, worktree: Path, release_root: Path) -> Path | None:
    """Map a URL path to a served file (traversal-safe)."""

    if path in ("/", "/index.html"):
        return worktree / "preview" / "index.html"
    if path == "/desk.html":
        return worktree / "design-concepts" / "app.html"
    if path.startswith("/release/"):
        return _safe_file(release_root, path[len("/release/") :].lstrip("/"))
    if path.startswith("/design-system/"):
        return _safe_file(worktree / "design-concepts" / "design-system", path[len("/design-system/") :].lstrip("/"))
    return None


def _safe_file(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return resolved if resolved.is_file() else None


class MarketRequestHandler(BaseHTTPRequestHandler):
    server_version = "skills-vector-market/1.0"
    worktree: Path = Path(".")
    release_root: Path = Path("preview/release")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write("market-server: " + format % args + "\n")

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(min(length, MAX_BODY_BYTES + 1))

    def _send(self, status: int, body: bytes, content_type: str, *, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/api"):
            response = handle_request(
                self.command or "GET",
                self.path,
                body=self._read_body(),
                store_factory=lambda: CatalogStore(self.release_root),
            )
            self._send(response.status, response.body, response.content_type, headers=response.headers)
            return
        if self.command not in ("GET", "HEAD"):
            self._send(
                405,
                json.dumps({"status": "method_not_allowed", "detail": "static surface is read-only"}).encode(),
                "application/json; charset=utf-8",
            )
            return
        static = resolve_static(path, self.worktree, self.release_root)
        if static is None:
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
            return
        body = static.read_bytes()
        self._send(200, body, CONTENT_TYPES.get(static.suffix.lower(), "application/octet-stream"))

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()


def build_server(worktree: Path, release_root: Path, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    handler_cls = type(  # per-server bound handler
        "BoundMarketRequestHandler",
        (MarketRequestHandler,),
        {"worktree": worktree, "release_root": release_root},
    )
    server = ThreadingHTTPServer((host, port), handler_cls)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--release-root", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    worktree = Path(args.worktree).resolve()
    release_root = Path(args.release_root).resolve() if args.release_root else worktree / "preview" / "release"
    server = build_server(worktree, release_root, args.host, args.port)
    print(f"skills-vector market server on http://{args.host}:{args.port} (release root {release_root})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
