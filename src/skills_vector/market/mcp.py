"""Read-only MCP tools backed by the shared market catalog.

The maintained MCP Python SDK owns protocol validation and both stdio and
Streamable HTTP transport behavior. All tools return the same CatalogStore
results exposed by the public read API; none performs research or writes state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from pydantic import Field
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .core import (
    EXPECTATION_BASIS_VALUES,
    EXPECTATION_DIMENSION_VALUES,
    FACET_DIMENSIONS,
    INSPECTOR_SECTIONS,
    RESPONSIBILITY_BAND_VALUES,
    WORK_LEVEL_VALUES,
    CatalogStore,
)
from .remote import RemoteCatalogStore

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def create_mcp_server(store: CatalogStore) -> MCPServer:
    """Create a stateless-capable MCP server over one shared catalog object."""

    server = MCPServer(
        "skills-vector-market",
        version="market-api/2",
        title="Skills Vector Evidence Atlas",
        description="Read-only, source-grounded occupational briefs for four role registrations.",
        instructions=(
            "Use only published, cited evidence. A provisional or missing role profile is not a finding. "
            "Sample counts are not workforce prevalence, proficiency, importance, or individual suitability. "
            "Never infer missing work level, responsibility, experience, context, or requirements from a title. "
            "people_management is distinct from people_manager work level and senior_strategic_ic; require explicit supervisory evidence. "
            "Expectation identity filters accept exact producer-supplied identity_id only; do not merge synonyms. "
            "expectation_id includes basis; relationship_id identifies the posting/source edge."
        ),
    )

    @server.tool(annotations=READ_ONLY)
    def get_brief(
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """List registered roles and publication state for the selected immutable release."""

        return store.brief(release_id)

    @server.tool(annotations=READ_ONLY)
    def release_info(
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Return current or explicitly pinned immutable release metadata."""

        return store.release_info(release_id)

    @server.tool(annotations=READ_ONLY)
    def list_occupations(
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """List role registrations, publication status, and sample counts for a pinned release."""

        return store.list_occupations(release_id)

    @server.tool(annotations=READ_ONLY)
    def get_occupation(
        slug: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$", description="Registered role slug.")],
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Return the selected release's role brief, citations, components, findings and lineage."""

        return store.occupation(slug, release_id)

    @server.tool(annotations=READ_ONLY)
    def get_component(
        component_id: Annotated[str, Field(pattern=r"^cmp_[a-f0-9]{20}$", description="Stable component id from get_occupation.")],
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Return an immutable release-pinned source-backed chart component; release_id defaults to current."""

        return store.get_component(component_id, release_id)

    @server.tool(annotations=READ_ONLY)
    def search_evidence(
        query: Annotated[str, Field(min_length=1, max_length=400, description="Search stored claims and admitted posting text.")],
        occupation: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Find cited claims and admitted postings in a selected immutable release."""

        return store.search(query, occupation, limit, release_id)

    @server.tool(annotations=READ_ONLY)
    def refine_evidence(
        occupation: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = None,
        work_level: Literal["individual_contributor", "people_manager", "unknown"] | None = None,
        responsibility_band: Literal["early_career", "independent_ic", "senior_strategic_ic", "people_management", "unknown"] | None = None,
        expectation_dimension: Literal["task", "capability", "tool", "knowledge", "experience", "contextual_expectation", "demonstration", "credential", "unknown"] | None = None,
        expectation_basis: Literal["employer_requirement", "employer_preference", "emergent_signal", "unknown"] | None = None,
        expectation_proficiency: Literal["explicitly_stated", "not_stated", "unknown"] | None = None,
        expectation_identity: Annotated[
            str | None,
            Field(max_length=160, description="Exact stored source-literal identity_id from the published expectation_identity facet."),
        ] = None,
        context_dimension: Literal[
            "employer_industry", "customer_industry", "sales_segment", "work_context", "employer_size", "employer_stage", "geography"
        ] | None = None,
        context_value: Annotated[str | None, Field(max_length=100)] = None,
        context_status: Literal["present", "unknown"] | None = None,
        experience_status: Literal["present", "unknown"] | None = None,
        experience: Annotated[str | None, Field(max_length=100)] = None,
        query: Annotated[str, Field(max_length=400)] = "",
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Filter admitted postings in one release; return raw facets and source lineage."""

        filters = {
            key: value
            for key, value in {
                "work_level": work_level,
                "responsibility_band": responsibility_band,
                "expectation_dimension": expectation_dimension,
                "expectation_identity": expectation_identity,
                "expectation_basis": expectation_basis,
                "expectation_proficiency": expectation_proficiency,
                "context_dimension": context_dimension,
                "context_value": context_value,
                "context_status": context_status,
                "experience_status": experience_status,
                "experience": experience,
            }.items()
            if value
        }
        return store.refine(occupation=occupation, filters=filters, query=query, limit=limit, release_id=release_id)

    @server.tool(annotations=READ_ONLY)
    def compare_roles(
        occupations: Annotated[list[str], Field(min_length=2, max_length=4, description="Two to four registered role slugs.")],
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Compare separately scoped raw counts and exact identities in one release."""

        return store.compare(occupations, release_id)

    @server.tool(annotations=READ_ONLY)
    def get_claim(
        claim_id: Annotated[str, Field(min_length=1, max_length=80)],
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Return a stored claim and citation metadata from the selected release."""

        return store.claim(claim_id, release_id)

    @server.tool(annotations=READ_ONLY)
    def inspect_evidence(
        section: Literal["extracts", "admissions", "exclusions", "mappings", "disagreements", "lineage", "all"],
        occupation: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        include_text: bool = True,
        release_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9._-]{1,80}$")] = None,
    ) -> dict[str, Any]:
        """Inspect provenance or classifications in a selected immutable release."""

        if section == "all":
            return store.inspector(occupation, limit=limit, release_id=release_id, include_text=include_text)
        return store.evidence(section, occupation, limit=limit, release_id=release_id, include_text=include_text)

    @server.tool(annotations=READ_ONLY)
    def research_plan(
        query: Annotated[str, Field(min_length=1, max_length=400)],
        occupation: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = None,
    ) -> dict[str, Any]:
        """Return a bounded local-operator handoff; never starts retrieval or inference."""

        return store.research_plan(query, occupation)

    return server


def build_backend(*, release_root: str | Path | None, base_url: str | None) -> CatalogStore:
    if base_url:
        return RemoteCatalogStore(base_url)
    if release_root:
        return CatalogStore(Path(release_root))
    raise SystemExit("market mcp requires --base-url <deployed API> or --release-root <dir>")


def tool_schemas(server: MCPServer) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        return [row.model_dump(by_alias=True, exclude_none=True) for row in await server.list_tools()]

    return anyio.run(load)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--print-tools", action="store_true")
    args = parser.parse_args(argv)
    store = build_backend(release_root=args.release_root, base_url=args.base_url)
    server = create_mcp_server(store)
    if args.print_tools:
        print(json.dumps(tool_schemas(server), indent=2, ensure_ascii=False))
        return 0
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port, stateless_http=True)
    return 0


__all__ = ["build_backend", "create_mcp_server", "tool_schemas"]
