"""Stable identifiers and content fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(body: str | bytes) -> str:
    data = body.encode("utf-8") if isinstance(body, str) else body
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_id(prefix: str, *parts: str) -> str:
    digest = content_hash("\x1f".join(parts))[:20]
    return f"{prefix}_{digest}"
