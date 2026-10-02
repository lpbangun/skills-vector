# Skills Vector · Evidence Atlas agent guide

## Start with the source and release

This guide was served by `http://127.0.0.1:8787`. Use this originating host for every request; do
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

Connect once to the Streamable HTTP MCP endpoint at `http://127.0.0.1:8787/api/mcp`. It exposes the
same read-only `CatalogStore` records as HTTP. This runnable Python script uses the
maintained `mcp` 2.x SDK (tested with 2.2.0), initializes one session, and discovers
all role registrations:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with streamable_http_client("http://127.0.0.1:8787/api/mcp") as (read_stream, write_stream):
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
BASE=http://127.0.0.1:8787
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
(`distribution/1`), and independent of later releases. Every component
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
uv run skills-vector market research refresh --once \
  --config "$SV_RESEARCH_CONFIG" --policy "$SV_REFRESH_POLICY" \
  --retention-record "$SV_STAGE_RETENTION" --evidence-root "$SV_EVIDENCE_ROOT" \
  --checkpoint "$SV_REFRESH_CHECKPOINT" --release-root preview/release
```

Both commands require operator-owned external configuration, source-policy allowlists,
evidence storage, and the reserved durable mission budget. Refresh installs no
schedule. Changing the retained stage requires fresh independent adjudication of the
exact frozen four-role comparison. Routine refresh uses the recorded retention
decision and frozen publication gates; it is not fresh independent or human review.
The public API never performs research or publication.
