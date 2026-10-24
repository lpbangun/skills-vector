"""Machine-readable read API schema and current market operator/agent guide."""

from __future__ import annotations

from typing import Any

from . import API_SCHEMA_VERSION, CORE_VERSION, RELEASE_SCHEMA_VERSION
from .core import COMPONENT_DEFINITION_VERSION, FACET_DIMENSIONS, HANDOFF_STAGES, INSPECTOR_SECTIONS
from .limits import RESEARCH_LIMITS
from .hosts import ALLOWED_SOURCE_HOSTS


def api_schema(*, base_url: str = "") -> dict[str, Any]:
    base = base_url.rstrip("/")
    return {
        "schema_version": API_SCHEMA_VERSION,
        "core_version": CORE_VERSION,
        "release_schema": RELEASE_SCHEMA_VERSION,
        "base_url": base or None,
        "read_only": True,
        "mutation_methods": "rejected (405) by REST; MCP exposes read-only tools",
        "bounds": {
            "max_query_length": 400,
            "max_limit": 200,
            "max_body_bytes": 8192,
            "max_mcp_body_bytes": 16384,
            "no_inference": "public search/refinement select stored claims and admitted source-backed postings only",
            "unknown_values": "retained and filterable; never inferred from title or years",
            "max_component_id_length": 24,
            "component_definition_version": COMPONENT_DEFINITION_VERSION,
            "component_pinning": "component_id is stable by occupation, measure, and definition version; pass release to reproduce the immutable release record",
        },
        "endpoints": [
            {"path": "/v1/track-record", "params": ["role?", "release?"], "returns": "pinned ledger counts, Brier/calibration or null scores, explicit backtest coverage"},
            {"path": "/v1/roles", "params": ["release?"], "returns": "role/2 index with release pin, data kind and fixture caveat"},
            {"path": "/v1/roles/{role}", "params": ["release?", "view=full|overview?"], "returns": "full (default) or overview: common skills, id/label status tails, deferred level expectations"},
            {"path": "/v1/roles/{role}/skills/{skill_id}", "params": ["release?"], "returns": "one complete skill, evidence, shares and forecast"},
            {"path": "/v1/roles/{role}/levels/{L}", "params": ["release?"], "returns": "one level and expectations"},
            {"path": "/v1/roles/{role}/graph", "params": ["release?"], "returns": "skill map, evidence, forecasts and edges"},
            {"path": "/v1/roles/{role}/industries/{naics}", "params": ["release?"], "returns": "broad sector, including null thin profiles"},
            {"path": "/v1/shifts", "params": ["since", "release?"], "returns": "current non-core measurements plus state_changes since YYYY-Qn or YYYY-MM, with history coverage"},
            {"path": "/v1/expectations/{id}@{release}", "returns": "pinned expectation or skill occurrences; missing posting/quote lineage explicitly marked not_recorded"},
            {"path": "/api/health", "returns": "release pointer status and role slugs"},
            {"path": "/api/brief", "params": ["release?"], "returns": "four registered roles, publication state, and release metadata"},
            {"path": "/api/release", "params": ["release?"], "returns": "release metadata, scope, versions"},
            {"path": "/api/occupations", "params": ["release?"], "returns": "registered roles with publication status, counts, and scope"},
            {"path": "/api/occupation", "params": ["slug", "release?"], "returns": "role brief, claims, admitted postings, citations, component_refs, and lineage"},
            {"path": "/api/component", "params": ["id", "release?"], "returns": "one immutable source-backed chart component; id is from /api/occupation component_refs"},
            {"path": "/api/search", "params": ["q", "occupation?", "limit?", "release?"], "returns": "deterministic search results with claim or source citations"},
            {"path": "/api/refine", "params": ["occupation?", "q?", "work_level?", "responsibility_band?", "expectation_dimension?", "expectation_basis?", "expectation_proficiency?", "expectation_identity?", "context_dimension?", "context_value?", "context_status?", "experience_status?", "experience?", "limit?", "release?"], "returns": "source-backed posting rows and raw sample facets, including observed exact source-literal expectation identities"},
            {"path": "/api/compare", "params": ["occupation=<slug> (repeat 2–4 times)", "release?"], "returns": "side-by-side admitted counts and observed dimensions"},
            {"path": "/api/query", "params": ["q", "occupation?", "limit?", "release?"], "returns": "claim-focused answer envelope with citation links"},
            {"path": "/api/claim", "params": ["id", "release?"], "returns": "claim, quote verification, sources, citation url"},
            {"path": "/api/citation", "params": ["id"], "returns": "immutable citation document"},
            {"path": "/api/evidence", "params": ["section", "occupation?", "limit?", "no_text?", "release?"], "returns": "inspector rows", "sections": [*INSPECTOR_SECTIONS, "all"]},
            {"path": "/api/inspector", "params": ["occupation?", "limit?", "no_text?", "release?"], "returns": "all inspector sections in one document"},
            {"path": "/api/research-plan", "params": ["q", "occupation?"], "returns": "bounded local handoff; never launches retrieval or inference"},
            {"path": "/api/schema", "returns": "this document"},
            {"path": "/api/agent-guide", "returns": "current agent and operator guide"},
            {"path": "/api/mcp", "methods": ["POST", "GET", "DELETE"], "returns": "official MCP SDK stateless Streamable HTTP read tools"},
        ],
        "mcp_tools": [
            {"name": "get_track_record", "params": ["role?", "release_id?"]},
            {"name": "list_roles", "params": ["release_id?"]},
            {"name": "get_role", "params": ["role", "release_id?", "view=full|overview?"]},
            {"name": "get_role_skill", "params": ["role", "skill_id", "release_id?"]},
            {"name": "get_role_level", "params": ["role", "level", "release_id?"]},
            {"name": "get_role_graph", "params": ["role", "release_id?"]},
            {"name": "get_role_industry", "params": ["role", "naics", "release_id?"]},
            {"name": "list_shifts", "params": ["since", "release_id?"]},
            {"name": "get_expectation", "params": ["expectation_id", "release_id"]},
            {"name": "release_info", "params": ["release_id?"]},
            {"name": "get_brief", "params": ["release_id?"]},
            {"name": "list_occupations", "params": ["release_id?"]},
            {"name": "get_occupation", "params": ["slug", "release_id?"]},
            {"name": "get_component", "params": ["component_id", "release_id?"]},
            {"name": "search_evidence", "params": ["query", "occupation?", "limit?", "release_id?"]},
            {"name": "refine_evidence", "params": ["occupation?", "work_level?", "responsibility_band?", "expectation_dimension?", "expectation_basis?", "expectation_proficiency?", "expectation_identity?", "context_dimension?", "context_value?", "context_status?", "experience_status?", "experience?", "query?", "limit?", "release_id?"]},
            {"name": "compare_roles", "params": ["occupations", "release_id?"]},
            {"name": "get_claim", "params": ["claim_id", "release_id?"]},
            {"name": "inspect_evidence", "params": ["section", "occupation?", "limit?", "include_text?", "release_id?"]},
            {"name": "research_plan", "params": ["query", "occupation?"], "mutates_catalog": False, "starts_research": False},
        ],
        "role_atlas": {
            "record_schema": "skills-vector.role/2",
            "selection": "disabled unless a research catalog or fixtures are explicitly configured; never a release-tree fallback",
            "fixture_caveat": "Fixture data is illustrative, not research. Preserve fixture_caveat, data_kind and limits in all downstream use.",
            "experimental": "publication is experimental exactly when role status is emerging; partial anchor fit alone does not imply experimental",
            "pinning": "use returned release_id with release= (REST) or release_id (MCP); expectation citations require id@release",
        },
        "component_schema": "market-component/1",
        "role_dimensions": {
            "work_level": ["individual_contributor", "people_manager", "unknown"],
            "responsibility_band": ["early_career", "independent_ic", "senior_strategic_ic", "people_management", "unknown"],
            "responsibility_band_boundary": "people_management is distinct from individual_contributor work level and requires source-backed management evidence; senior_strategic_ic is not management",
            "expectation_dimension": ["task", "capability", "tool", "knowledge", "experience", "contextual_expectation", "demonstration", "credential", "unknown"],
            "expectation_basis": ["employer_requirement", "employer_preference", "emergent_signal", "unknown"],
            "expectation_proficiency": ["explicitly_stated", "not_stated", "unknown"],
            "expectation_identity": "identity_id + identity_method; source-literal identity independent of basis; exact labels only, no synonym or semantic equivalence mapping",
            "expectation_id": "dimension + basis + normalized exact wording; not a posting edge",
            "mapping_method": "exact-normalized-label/2",
            "relationship_id": "posting/source/expectation edge identity; carries its own basis and observed posting context",
            "relationship_method": "posting-source-expectation/1",
            "responsibility_scope": "release-specific inclusion boundary; not inferred seniority or work level",
            "context_dimensions": list(FACET_DIMENSIONS),
            "advertised_experience": "verbatim wording or unknown; years never substitute for responsibility or work level",
        },
        "query_status_semantics": {
            "cited_evidence": "one or more stored, quote-verified claims match; answer text selects stored statements",
            "insufficient_evidence": "in-scope question without a claim clearing the citation threshold",
            "unsupported_question": "outside the covered occupational scope",
        },
        "citation_path_pattern": "/release/citations/<claim_id>.json",
        "release_path_pattern": "/release/releases/<release_id>/manifest.json",
        "research": {
            "launched_by_public_api": False,
            "operator_only": True,
            "handoff_kind": "bounded_research_request",
            "handoff_stages": list(HANDOFF_STAGES),
            "limits": {
                "max_retrievals": RESEARCH_LIMITS["max_retrievals"],
                "max_model_calls": RESEARCH_LIMITS["max_model_calls"],
                "max_response_bytes": RESEARCH_LIMITS["max_response_bytes"],
                "serialized_calls": RESEARCH_LIMITS["serialized_calls"],
            },
            "allowlisted_hosts": list(ALLOWED_SOURCE_HOSTS),
            "provider": "DeepInfra only; no credential is bundled or returned by the public API",
        },
    }


def agent_guide_markdown(*, base_url: str = "") -> str:
    base = (base_url.rstrip("/") if base_url else "") or "http://127.0.0.1:8787"
    return f"""# Skills Vector · Evidence Atlas agent guide

## Start with the source and release

This guide was served by `{base}`. Use this originating host for every request; do
not substitute another deployment. Start with `/api/brief`, `/api/release`, and
`/api/occupations`. The four registrations are HR Generalist, Growth Manager, Account
Executive, and provisional Forward Deployed Engineer (FDE). `family`, `aliases`,
publication state, and counts come from release data; they are not an invitation to
merge roles by title. `role_scope.responsibility_scope` describes the release's
inclusion boundary, not seniority or work level. FDE is mission-authorized and
provisional, not human-reviewed. O*NET 15-1252.00 is a partial task-level anchor,
not an official FDE code or role equivalence. Read the selected release's records for
the current finding status.

Use the id returned by `/api/release` or MCP `release_info` for HTTP requests that
accept `release=` and MCP evidence tools that accept `release_id=`. This includes
role, search, refinement, comparison, claim, inspector, and component reads. Omit a
pin only for a fresh current browse. Historical releases and component data remain
addressable and immutable; never replace a pinned result with current while
interpreting or citing it. Citation documents are immutable by claim id.
New artifacts use `market-release/2`. The responsibility-scope boundary replaces
the old forced mid-level scope; it never relabels historical records. Immutable
`market-release/1` releases remain readable with their original scope.


## One remote MCP connection

Connect once to the Streamable HTTP MCP endpoint at `{base}/api/mcp`. It exposes the
same read-only `CatalogStore` records as HTTP. This runnable Python script uses the
maintained `mcp` 2.x SDK (tested with 2.2.0), initializes one session, and discovers
all role registrations:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with streamable_http_client("{base}/api/mcp") as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            release = await session.call_tool("release_info", dict())
            release_id = release.structured_content["release_id"]
            roles = await session.call_tool("list_occupations", dict(release_id=release_id))
            print(roles.structured_content)

asyncio.run(main())
```

Discover aliases and role families from `list_occupations`; fetch a role with
`get_occupation`. Use `refine_evidence` for bounded posting filters and
`compare_roles` for two to four registered role slugs. A four-role comparison may
include FDE, but an unpublished role stays an explicit registration with no findings.
Do not invent cross-role skill equivalence: comparison reports separately scoped raw
counts and exact recorded identities only.

Use the same initialized session for supported filters, comparisons, findings, and
charts. The following is a continuation, not a standalone script: insert these calls
inside the `async with ClientSession(...)` block in `main`, after role discovery.
Select ids from returned records rather than guessing them:

```python
role_result = await session.call_tool(
    "get_occupation", dict(slug="hr-generalist", release_id=release_id)
)
role = role_result.structured_content
refined = await session.call_tool(
    "refine_evidence",
    dict(occupation="hr-generalist", responsibility_band="unknown", limit=50, release_id=release_id),
)
comparison = await session.call_tool(
    "compare_roles",
    dict(occupations=[
        "hr-generalist", "growth-manager", "account-executive", "forward-deployed-engineer",
    ], release_id=release_id),
)
if role.get("claims"):
    claim = await session.call_tool(
        "get_claim", dict(claim_id=role["claims"][0]["claim_id"], release_id=release_id)
    )
if role.get("component_refs"):
    component = await session.call_tool(
        "get_component",
        dict(component_id=role["component_refs"][0]["component_id"], release_id=release_id),
    )
```

`get_occupation` returns `component_refs` for measures supported by that release.
`get_component` accepts the reference's `component_id` and `release_id`, returning the
same immutable record as `/api/component`. `refine_evidence` and `compare_roles`
return separately scoped raw counts and exact recorded identities; never infer
cross-role skill equivalence.

## HTTP fallback

```sh
BASE={base}
curl "$BASE/api/brief"
curl "$BASE/api/release"
curl "$BASE/api/occupations"
curl "$BASE/api/occupation?slug=hr-generalist"
curl "$BASE/api/search?q=employee+relations"
curl "$BASE/api/refine?occupation=hr-generalist&responsibility_band=unknown&limit=50"
curl "$BASE/api/compare?occupation=hr-generalist&occupation=growth-manager&occupation=account-executive&occupation=forward-deployed-engineer"
curl "$BASE/api/claim?id=CLAIM_ID&release=RELEASE_ID"
curl "$BASE/api/component?id=COMPONENT_ID&release=RELEASE_ID"
curl "$BASE/api/citation?id=CLAIM_ID"
```

Replace `CLAIM_ID`, `COMPONENT_ID`, and `RELEASE_ID` only with values returned by the
pinned release records. `/api/occupation` is the source of component references;
`/api/claim` resolves a stored claim, while `/api/citation` returns its write-once
citation document. `/api/component` requires `id=cmp_<20 lowercase hex digits>`;
`release` pins an old component to its immutable release. Unknown component ids fail
closed and never trigger inference.

## Components, calculations, and citation rules

Component ids are deterministic for the occupation, measure, and definition version
(`{COMPONENT_DEFINITION_VERSION}`), and independent of later releases. Every component
record carries its `release_id`, stable `component_id`, `reference` in
`component_id@release_id` form, definition, explicit numerator/denominator and
calculation, sample scope/dates, aggregate `rows`, recomputable source-backed
`observations`, original source links, and limitations. Responsibility-band
distributions count each admitted posting once by its stored band; missing bands are
shown as unknown. Supported context distributions retain exact stored values, and
missing or non-present context stays in an unknown bucket. Each row exposes raw counts
and supporting posting/source ids—there are no fabricated percentages, trends,
importance, proficiency, competence, or individual-fit scores.

For a finding, cite its exact `claim_id`, `release_id`, and returned citation URL.
For a chart, cite its exact `component_id` and `release_id` together (the copyable
reference is `component_id@release_id`) and link the original source records. Do not
cite a component id without its release pin. Claim and component records are read-only;
they never collect or infer evidence.

Sample counts describe only admitted source-backed records, not market prevalence,
hiring outcomes, skill absence, or individual capability. Unknown means not
demonstrated by this evidence—not that someone lacks a skill. Personal work history
and self-assessment stay with the user's own local agent; never upload them to this
service. No personal evidence is required for browsing, and the catalog cannot make
personal assessments.

## Role Atlas role/2 reads

The Role Atlas is a separate `skills-vector.role/2` catalog, not a reinterpretation
of the legacy market releases. REST and MCP return identical JSON envelopes:

| REST GET | MCP tool |
| --- | --- |
| `/v1/roles` | `list_roles(release_id?)` |
| `/v1/roles/{{role}}?view=overview` | `get_role(role, release_id?, view?)` |
| `/v1/roles/{{role}}/skills/{{skill_id}}` | `get_role_skill(role, skill_id, release_id?)` |
| `/v1/roles/{{role}}/levels/{{L}}` | `get_role_level(role, level, release_id?)` |
| `/v1/roles/{{role}}/graph` | `get_role_graph(role, release_id?)` |
| `/v1/roles/{{role}}/industries/{{naics}}` | `get_role_industry(role, naics, release_id?)` |
| `/v1/shifts?since=2026-07` | `list_shifts(since, release_id?)` |
| `/v1/expectations/{{id}}@{{release}}` | `get_expectation(expectation_id, release_id)` |

Use this host (`{base}`) for these requests and `/api/mcp` for the tools. Optional
REST pins use `?release=<release_id>`. Each success envelope carries `release_id`,
`data_kind`, `fixture_caveat` and `limits`. The full schema-valid record is under
`role`; other reads return `roles`, `level`, `graph`, `industry`, or the shifts
collections `current` and `state_changes`.
Unknown role, level, sector, expectation or release returns a structured 404;
invalid parameters return 400. No unknown pin silently falls back to current.
Levels accept `L3` or `3`. NAICS ranges accept `31–33` or `31-33`.

A server serves **no role/2 catalog by default** (503, never fixture fallback).
For local REST+MCP, explicitly select one of:

```sh
uv run skills-vector market serve --role-atlas-root /absolute/research-role-catalog
uv run skills-vector market serve --role-atlas-fixtures
uv run skills-vector market mcp --release-root preview/release --role-atlas-fixtures
```

The module server/MCP entrypoints accept the same options. These options are
mutually exclusive. The catalog directory contains flat role/2 JSON documents;
multiple releases use different filenames. Without a pin, the release with the
latest demand window is selected, with release-id lexical order breaking ties.
Always reuse the returned pin to reproduce a read. The legacy MCP `--base-url`
backend does not auto-select a role/2 catalog; use deployed HTTP MCP or explicitly
configure local role/2 data for stdio.

For the deployed `api/index.py`, set `SKILLS_VECTOR_ROLE_ATLAS_ROOT` to an explicit
research catalog directory, or `SKILLS_VECTOR_ROLE_ATLAS_FIXTURES=1` to select the
packaged fixtures. Do not set both. A research selection containing fixture
records fails closed. Neither a query parameter nor a tool argument can enable
fixtures. No catalog read writes a release or calls an external service.

**Fixture caveat:** `data_kind: fixture` means illustrative prototype numbers,
not research findings. Keep the returned `fixture_caveat` and all `limits`,
including synthetic core measurements, assumed edge employer counts, concentration
and illustrative forecast rates. Never relabel fixtures as research. An emerging
role (FDE in the fixtures) has `publication: experimental`; retain that caveat.
Established Account Executive stays standard even though its anchor fit is partial.
Thin sectors remain visible with `profile: null` and a reason. The human reader is
at `/role-atlas.html`, using the same configured API and release pin. Fixture role
and graph envelopes may also carry labeled `presentation` family/row layout from
the approved concept; this is illustration metadata, not evidence or a change to
the schema-valid record under `role`. Research selections never receive fixture
presentation metadata.

M4 offline research output can be selected with `--role-atlas-root` pointing at
its `roles/` directory. Unknown `first_level` and missing `share_by_level`
denominators are null, not L1 or zero demand. Measurement strata may explicitly
include `"unassigned"` alongside numeric levels. Level titles/expectations can
be empty; `introduced_at: null` asserts neither introduction nor carry-over.
Do not infer a career level from these absences. Current one-snapshot research
forecast objects have `basis: "none"`, null `probability` and `base_rate`, and an
explicit reason. They preserve a dated claim for future evaluation, not a forecast
percentage. Numeric probabilities/base rates require `basis: "backtest"` (historical
archive) or `basis: "fixture"` (illustrative); both numbers must be in 0–1.

M6 `market posting-volume capture|project` is a separate quarterly rules/1 lane.
It reuses allowlisted public ATS parsing, US geography and dedup, with an independent
500-posting ceiling and 20% per-employer cap. It never imports a model/provider or
uses model-backed budgets. Capture is offline by default unless `--live` is explicit;
Pi does not run live job-board retrieval. See `docs/role-atlas/POSTING_VOLUME.md`
for native samples, exact operator commands, provenance and comparable-quarter rules.
Role envelopes also carry `method`: every SOURCE_RULES kind, its canonical timing,
family and use. This is policy, not a claim that all those sources were imported.

M5 official-signal importers and forecast ledger are local operator CLI commands;
see `docs/role-atlas/OFFICIAL_SIGNALS_AND_LEDGER.md`. They never fetch files or
accept API keys. Select a ledger with `--role-atlas-ledger` (serve/MCP) or
`SKILLS_VECTOR_ROLE_ATLAS_LEDGER`; this does not enable a catalog. The read-only
`GET /v1/track-record?role=...&release=...` and MCP `get_track_record(role, release_id)`
return identical pinned counts, Brier/calibration or null/[], and backtest coverage.
Full-role envelopes carry the same `track_record` outside the immutable role JSON.
Wrong-kind/corrupt selected ledgers fail closed. None-basis and fixture calls are
never scored. Research calls enter the ledger as none; only a causal historical
backtest can estimate numbers, before resolution. Without an archive the status
is `not run: no historical posting archive`, with a reason and no numeric score.
The web Method section renders this response, not prototype calibration numbers.

Expectation citation ids are `id@release`, exactly as returned in level `cite`
paths. They resolve to the stored expectation and all its role/level occurrences.
Current shift items also carry a skill `cite` in that same endpoint namespace;
`get_expectation` accepts skill ids and returns the pinned skill and occurrences.
The current contract stores level evidence counts, not posting ids/quote excerpts:
`postings` and `excerpts` are empty and `evidence_status` is `not_recorded`. Do not
invent citations from those counts. The shifts read returns two distinct collections:
`current` includes every rising, early or fading skill whose latest measured window
is after `since`, even with only one stored snapshot. Each item retains comparable
windows with shares/employers, evidence families/directions, dispute text, forecast
and a pinned skill citation. It is sorted rising → early → fading, then available probability
descending, with unavailable forecasts last within a status and ties ordered by ids. Core skills are excluded. `state_changes` compares stored release
snapshots and reports `history_status` and `history_note`; a single fixture snapshot
has no asserted state transitions, but still answers which skills are changing now.
`since` is an exclusive window boundary; `release` is the inclusive upper pin.
No person-specific gap or assessment data is accepted by these read-only tools.

## Local stdio fallback and operator boundary

For an installed local checkout, connect through maintained MCP stdio with:

```sh
uv run skills-vector market mcp --release-root preview/release
```

HTTP/MCP tools are read-only. `research-plan` only prepares a bounded local handoff;
it does not retrieve sources, call a model, or publish. `research run` is an explicit,
bounded research/candidate-collection run; it freezes candidate arms and does not
publish or replace the current release. Routine refresh is the separate operator
command `research refresh --once`, which follows the external independently adjudicated
four-role retention record and fixed bounded policy:

```sh
uv run skills-vector market research refresh --once \\
  --config "$SV_RESEARCH_CONFIG" --policy "$SV_REFRESH_POLICY" \\
  --retention-record "$SV_STAGE_RETENTION" --evidence-root "$SV_EVIDENCE_ROOT" \\
  --checkpoint "$SV_REFRESH_CHECKPOINT" --release-root preview/release
```

Both commands require operator-owned external configuration, source-policy allowlists,
evidence storage, and the reserved durable mission budget. Refresh installs no
schedule. Changing the retained stage requires fresh independent adjudication of the
exact frozen four-role comparison. Routine refresh uses the recorded retention
decision and frozen publication gates; it is not fresh independent or human review.
The public API never performs research or publication.
"""


__all__ = ["agent_guide_markdown", "api_schema"]
