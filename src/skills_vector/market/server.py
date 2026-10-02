"""Local ASGI server for the same read API and Streamable HTTP MCP app used on Vercel."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import uvicorn

from .core import CatalogStore
from .web import create_market_app


def build_server(
    worktree: Path,
    release_root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> uvicorn.Server:
    """Build an ASGI server over one local catalog store."""

    worktree = Path(worktree).resolve()
    release_root = Path(release_root).resolve()
    app = create_market_app(
        CatalogStore(release_root),
        worktree=worktree,
        release_root=release_root,
        serve_static=True,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    return uvicorn.Server(config)


def run_server(
    worktree: Path,
    release_root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> int:
    server = build_server(worktree, release_root, host, port)
    print(f"skills-vector market ASGI server on http://{host}:{port} (MCP /api/mcp)", flush=True)
    server.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--release-root", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    worktree = Path(args.worktree).resolve()
    release_root = Path(args.release_root).resolve() if args.release_root else worktree / "preview" / "release"
    return run_server(worktree, release_root, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
