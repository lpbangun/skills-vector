"""Vercel ASGI entrypoint for the shared REST and stateless Streamable HTTP MCP API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src_root = ROOT / "src"
if src_root.is_dir():
    sys.path.insert(0, str(src_root))

from skills_vector.market.core import CatalogStore
from skills_vector.market.remote import RemoteCatalogStore
from skills_vector.market.web import create_market_app


def _catalog_store() -> CatalogStore:
    env_root = os.environ.get("SKILLS_VECTOR_RELEASE_ROOT")
    candidates = [Path(env_root)] if env_root else []
    candidates.append(ROOT / "preview" / "release")
    for candidate in candidates:
        try:
            if (candidate / "current.json").is_file():
                return CatalogStore(candidate)
        except OSError:
            continue
    deployed_url = os.environ.get("VERCEL_URL")
    if deployed_url:
        return RemoteCatalogStore(f"https://{deployed_url}")
    return CatalogStore(candidates[-1])


# One catalog object backs REST and MCP for this function instance.
CATALOG = _catalog_store()
app = create_market_app(CATALOG, release_root=ROOT / "preview" / "release", public_host=True)
