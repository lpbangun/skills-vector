# Research and publication architecture

## Current four-role market lane (`src/skills_vector/market/`)

This product is a source-grounded reference for four registered U.S. roles:
HR Generalist, Growth Manager, Account Executive, and a provisional
Forward Deployed Engineer (FDE) pilot registration. FDE is not an official
occupation equivalence: its O*NET Software Developers code is a partial
task-level anchor only. Finding availability is determined by the selected
immutable release, never by the registration or role title.

The local operator pipeline is:

```
allowlisted public-source discovery/retrieval → hash and lineage receipts
  → admission and exact-quote checks → same-corpus primary/challenger arms
  → frozen comparison → fresh independent adjudication
  → structural validation → local immutable release
```

- **One product inference lane.** Research calls go only through the
  authenticated DeepInfra endpoint and the exact models pinned by the external
  operator config: primary `deepseek-ai/DeepSeek-V4.1-Flash`, challenger
  `zai-org/GLM-5.3-Flash`, and escalation `zai-org/GLM-5.3`. There is no OMP
  market-research call, fixture fallback, or silent model substitution. Reads,
  browsing, search, MCP tools, and static-site requests never invoke inference.
- **External authority and durable accounting.** The operator config, verified
  pricing receipt, mission reservation/ledger, request and response bytes, and
  run receipts stay outside the product worktree and deploy. `DEEPINFRA_API_KEY`
  is supplied only to the local process environment. Reservations precede
  calls; unverified/unknown usage remains held. Reconciliation accepts only an
  identical, hash-matching response already retained inside that run and never
  retries the provider.
- **Bounded public retrieval.** Sources are restricted to the configured public
  foundations and allowlisted employer-board APIs. Every retrieval attempt,
  response size, board, model call, token bound, retry, and run duration is
  capped. A failed, incomplete, truncated, or out-of-scope response is not
  treated as a complete source or a posting. Raw response bytes and exact
  provenance remain in the external evidence root.
- **Candidate integrity.** The primary-only and primary-plus-challenger arms
  share one frozen retrieved corpus. Model agreement is not independent
  corroboration. Admission requires source-backed role scope and verified
  wording; deterministic checks enforce exact quotes, source identity, and
  classification constraints. Responsibility bands require worker-duty evidence
  of autonomy, complexity, ownership, or influence—not a strategic customer tier.
  Sales segments require linked buyer or sales-market wording; product/artifact
  scale alone normalizes to unknown with provenance and an inspectable issue,
  without dropping an otherwise in-scope posting. Unsupported claims and
  disagreements remain inspectable rather than being repaired with title-based
  assumptions.
- **Distinct work dimensions.** Work level (individual contributor, people
  manager, unknown), responsibility band (`early_career`, `independent_ic`,
  `senior_strategic_ic`, `people_management`, unknown), advertised experience
  wording, employer/customer context, and each expectation's dimension, basis,
  and proficiency are separate. `people_management` requires source-backed
  management evidence; a senior strategic IC is not a manager. Exact
  source-literal `identity_id` groups only the same dimension and normalized
  wording, independent of basis; `expectation_id` retains basis and
  `relationship_id` identifies the posting/source edge. No semantic equivalence
  is inferred, and each edge retains its own wording, basis, and posting context.
  New releases use `market-release/2` and an explicit
  `role_scope.responsibility_scope` inclusion boundary instead of forced
  mid-level seniority.
  Existing immutable releases are read as recorded; catalog loading does not
  revalidate or relabel them under current semantic rules. Unknown classifications
  are not retroactively inferred.
  Advertised experience, contextual expectations, and requested demonstrations have
  separate expectation dimensions; explicit years-of-experience wording is not knowledge
  or proficiency. The source quote and relationship retain the distinction.
- **Review and release.** A comparison is not publishable without a fresh
  independent adjudication receipt bound to its frozen bytes. Publication is
  local-only: the slice is structurally validated, merged into
  `preview/release/releases/<release_id>/`, write-once claim citations are
  preserved, and `current.json` is swapped atomically under a per-release
  process lock. Each merged candidate is bound to the current release it used;
  a stale candidate cannot replace a newer publication. Historical releases
  and citation bytes remain immutable. Thin or failed candidates do not replace
  the current release.
  Every new publication also writes `changes.json` inside the same staged,
  write-once release transaction. Deltas compare canonical row contents by stable
  claim/posting/source IDs, and include the recorded sampling and expectation
  definitions on both sides. Historical releases without a summary remain unchanged;
  the public header offers a link only for releases that actually contain one.
- **Bounded routine refresh.** An explicit local `market research refresh --once` follows the already-adjudicated four-role stage-retention decision; it does not reselect a stage per role or install a scheduler. One shared reserved mission funds all four fixed roles. Publication is all-or-nothing after settled-cost reconciliation, stable role and identity-definition checks, source/quote/rights validation, and contradiction gates; failures retain the last-good pointer. Primary-only retention runs no challenger pass. A retained challenger refresh may be automatically published but is marked `human_reviewed: false`, and fresh challenge findings remain unresolved until independently reviewed.
  The selected immutable release supplies its publication-policy metadata. Automatic
  releases record the frozen policy fingerprint and concrete cadence/posting/board
  limits, and describe insufficient admission as a full-candidate block that retains
  last-good—not as an unimplemented foundation-only publication fallback.

- **Shared read domain.** `CatalogStore` is the single query core for the
  browser preview, read-only `/api/*` routes, CLI reads, and MCP tools. Public
  HTTP reads accept `GET`/`HEAD` only; they cannot start research or publish.
  Stateless Streamable HTTP MCP is exposed at `/api/mcp`; local operators can
  also use the maintained MCP SDK over stdio. These surfaces return the same
  role, citation, posting, filter, comparison, component, and research-handoff results.
- **Versioned read components.** `CatalogStore.get_component` derives
  `market-component/1` records directly from the selected immutable release.
  Ids hash occupation, measure, and `distribution/1` definition version, independently
  of the release id. `component_refs` in occupation reads expose the stable id and
  release pin. Responsibility-band and supported context distributions count each
  admitted posting once; missing or non-present values stay explicitly unknown.
  Aggregate rows, posting observations, dates, source links, calculation,
  numerator/denominator, and limitations let consumers reconstruct the measure.
  HTTP `/api/component`, MCP `get_component`, CLI `market component`, and preview
  charts all use this accessor. No component read infers, retrieves, or mutates data.

`preview/index.html` follows Evidence Atlas · Control. Global search and agent
onboarding precede role selection. Published roles open with a concise cited brief
and honest raw-count charts, then optional refinements and expandable evidence.
Growth dimension examples retain their recorded variant labels; equal wording
in distinct variants is not collapsed into a shared expectation.
Optional research handoff, refinement, comparison, and question forms are collapsed
on the overview. A selected role presents its brief directly instead of leaving the
overview's research controls above it; global search and agent navigation remain available.
Each chart has an accessible aggregate table, underlying observations, JSON,
original source links, and a copyable `component_id@release_id` reference.
Pinned role/component deep links preserve the release across catalog updates.
The complete evidence inspector is secondary to the brief. The stable
`/api/agent-guide` uses the originating host, documents one maintained MCP
session plus HTTP and local stdio alternatives, and keeps personal assessment
inside the user's own harness. No public read launches the operator pipeline.

## Historical occupational slice (pre-market)

Skills Vector followed the same separation as [OH SHI](https://github.com/lpbangun/oh-shi), without copying Cloudflare, D1, or hosted refresh:

```
permitted sources → durable provenance (SQLite) → query/export → CLI and future Sites static files
```

Jobsss remains the private consumer: it downloads an approved release and compares locally. This repository does not host personal evidence. The sections below describe that earlier lane and are historical.

## Workflow

`collect → extract → reconcile → challenge → review → release`

| Step | Who | Notes |
| --- | --- | --- |
| collect | ordinary code | Fetch, hash **body**, store URL, publisher, dates, parser version, rights. Failed fetches do not delete prior evidence. |
| extract | model or labeled offline interpreter | Tasks, skills, tools, context, supporting passages. Bounded JSON. |
| reconcile | model or offline interpreter | Terminology mapping. Model agreement ≠ independent corroboration. |
| challenge | model or offline interpreter | Receives **claims plus passages**. |
| review | human | Approve, request changes, or reject. Change-requests resume; rejects are terminal. |
| release | ordinary code | Atomic directory rename. No fixtures, checkpoints, credentials, or private evidence. |

Forecasts are optional (`--include-forecast`) and are not current requirements.

## Interfaces

| Command | Purpose |
| --- | --- |
| `skills-vector research occ_founding_engineer --fixtures` | Offline pilot path |
| `skills-vector research occ_founding_engineer` | Live O*NET fetch; no fixture fallback |
| `skills-vector review RUN approve --reviewer NAME` | Human gate |
| `skills-vector release` | Write `data/releases/<id>/` |
| `skills-vector rollback RELEASE` | Restore previous current pointer |
| `skills-vector assess ...` | Local Jobsss-shaped comparison |
| `skills-vector budget` | Remaining US$ cap |
| `skills-vector schedule-notes` | Computer must be awake; no hosted runner |

Open PR #6 (`codex/vps-roadmap-api-integration`) added a FastAPI always-on seam. This slice reuses its review-persistence and CLI ideas but does **not** merge that PR: publication is static files for a later Sites plugin, and research is not an HTTP service.

## Scheduling

Weekly collection and monthly review are commands you run locally. The computer must be awake. No cron, GitHub Action, or VPS runner is installed.
