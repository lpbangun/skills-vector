"""HTTP release source: read the served release tree as a catalog backend.

Used by the deployed serverless function when the release bundle is not present
locally (it reads the same immutable files over the deployment's own static
paths), and usable directly for remote diagnostics. Read-only, bounded.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .core import CatalogStore
from .release import ReleaseData

MAX_REMOTE_BYTES = 4 * 1024 * 1024
EXTRACT_FETCH_LIMIT = 60


class RemoteCatalogStore(CatalogStore):
    """A :class:`CatalogStore` whose files are fetched from a deployed base URL."""

    def __init__(self, base_url: str, *, timeout: float = 15.0, cache_seconds: float = 30.0) -> None:
        super().__init__(Path("/nonexistent-remote"), base_url=base_url)
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache_time = 0.0
        self._remote_cache: ReleaseData | None = None
        self._pointer_cache: dict[str, Any] | None = None

    # -- low level -------------------------------------------------------

    def fetch_json(self, path: str) -> Any | None:
        url = self.base_url + "/" + path.lstrip("/")
        request = urllib.request.Request(url, headers={"user-agent": "skills-vector-market/1.0", "accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - operator-supplied base URL
                body = response.read(MAX_REMOTE_BYTES)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def fetch_text(self, path: str) -> str | None:
        url = self.base_url + "/" + path.lstrip("/")
        request = urllib.request.Request(url, headers={"user-agent": "skills-vector-market/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.read(MAX_REMOTE_BYTES).decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return None

    # -- CatalogStore overrides -----------------------------------------

    def pointer(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._pointer_cache is not None and now - self._cache_time < self.cache_seconds:
            return self._pointer_cache
        value = self.fetch_json("/release/current.json")
        pointer = value if isinstance(value, dict) else {}
        if not isinstance(pointer.get("releases"), list):
            pointer["releases"] = []
        self._pointer_cache = pointer
        return pointer

    def release(self, release_id: str | None = None) -> ReleaseData | None:
        rid = release_id or self.current_release_id()
        if not rid:
            return None
        now = time.monotonic()
        cached = self._remote_cache
        if cached is not None and cached.release_id == rid and now - self._cache_time < self.cache_seconds:
            return cached
        base = f"release/releases/{rid}"
        manifest = self.fetch_json(f"/{base}/manifest.json")
        if not isinstance(manifest, dict):
            return None

        def rows(name: str) -> list[dict[str, Any]]:
            value = self.fetch_json(f"/{base}/{name}.json")
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                return [value]
            return []

        data = ReleaseData(
            root=self.root,
            manifest=manifest,
            occupations=rows("occupations"),
            sources=rows("sources"),
            postings=rows("postings"),
            claims=rows("claims"),
            requirements=rows("requirements"),
            extracts={},
        )
        fetched = 0
        for source in data.sources:
            if fetched >= EXTRACT_FETCH_LIMIT:
                break
            extract_path = str(source.get("extract_path") or "").strip()
            if not extract_path:
                continue
            text = self.fetch_text(f"/{base}/{extract_path}")
            if text is not None:
                data.extracts[str(source.get("id"))] = text
                fetched += 1
        self._remote_cache = data
        self._cache_time = now
        return data

    def citation(self, claim_id: str) -> dict[str, Any]:
        document = self.fetch_json(f"/release/citations/{claim_id}.json")
        if not isinstance(document, dict):
            return {
                "status": "unknown_citation",
                "claim_id": claim_id,
                "detail": "citation document not found on the deployment",
            }
        document = dict(document)
        document["status"] = "ok"
        return document


def load_remote_release(base_url: str) -> ReleaseData | None:
    """Debug helper: load the current release purely over HTTP."""

    store = RemoteCatalogStore(base_url)
    return store.release()


__all__ = ["RemoteCatalogStore", "load_remote_release"]
