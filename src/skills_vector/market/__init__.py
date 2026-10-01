"""Skills Vector market reference core.

One stdlib-only catalog/query core backs the static frontend API, the deployed
read API, the CLI (``skills-vector market ...``) and the MCP server. It never
performs model inference: releases are immutable, agent-authored artifacts that
are published by the local research pipeline after validation.
"""

from __future__ import annotations

__all__ = [
    "API_SCHEMA_VERSION",
    "CORE_VERSION",
    "RELEASE_SCHEMA_VERSION",
]

CORE_VERSION = "market-core/1.0"
RELEASE_SCHEMA_VERSION = "market-release/1"
API_SCHEMA_VERSION = "market-api/1"
