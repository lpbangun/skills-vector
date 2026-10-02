"""Skills Vector Evidence Atlas read core and transport interfaces.

One CatalogStore domain model backs the static preview, read API, CLI, and
maintained MCP SDK server. It never performs model inference: immutable
evidence artifacts are assembled by the local operator research lane.
"""

from __future__ import annotations

__all__ = [
    "API_SCHEMA_VERSION",
    "CORE_VERSION",
    "RELEASE_SCHEMA_VERSION",
]

CORE_VERSION = "market-core/2.0"
RELEASE_SCHEMA_VERSION = "market-release/2"
API_SCHEMA_VERSION = "market-api/2"
