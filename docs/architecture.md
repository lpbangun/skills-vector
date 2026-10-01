# Research and publication architecture

## Current market lane (`src/skills_vector/market/`)

Bounded live research over public sources, published as immutable versioned releases:

```
public foundations + employer job-board APIs → fixed-cap retrieval → fair candidate selection
  → model admission/extraction → reconciliation → agent evidence linking → challenge → validated atomic release
```

- **Discovery** picks allowlisted foundations and employer boards (`greenhouse`,
  `lever`, `ashby`, `smartrecruiters`, `workable`) with the pinned subscription
  task model. **Retrieval** is capped by code constants in
  [`limits.py`](../src/skills_vector/market/limits.py) (60 retrievals, 12 model
  calls, 2 MiB per response, 16 boards, 4 foundations). Truncated listings are
  salvaged from the JSON prefix and refetched compactly where the ATS supports
  it; description text is fetched per in-scope candidate.
- **Provenance: board listings vs per-posting detail responses.** A board listing
  response is the population record (`source_type: job-board`,
  `retrieval_kind: listing`): it drives enumerated/sampled postings, board
  denominators and employer counts. When a listing carries no description text,
  each successfully fetched posting detail becomes its own source record
  (`source_type: job-posting-detail`, `retrieval_kind: detail`,
  `parent_source_id` → the board listing) with its own detail URL, response
  hash, `retrieved_at`, rights and byte count. The stored posting, its claim
  verification and the published extract cite that origin source, so a quote is
  never attributed to a metadata-only listing. Detail sources do not inflate
  boards attempted/used, sampled postings or employer counts; published extracts
  carry short verified excerpts only, never full job descriptions.
- **Candidate selection** applies explicit US-geography, seniority-band and
  title-scope rules, records every inclusion/exclusion with its reason, and
  allocates the candidate cap fairly: one candidate per employer per provisional
  growth-variant/title bucket first, then a round-robin remainder (most recently
  published first within a bucket). Per-bucket/employer outcomes and cap
  exclusions are published. When real measured yield is below the configured
  minimum — or a required growth variant bucket has no candidate — one bounded
  discovery-feedback pass replaces boards the retrieval facts proved empty or
  variant-poor.
- **Admission/mapping** is agent-authored over retrieved text with byte-verbatim
  excerpt verification. Admission also carries an explicit scope decision:
  `work_level` (`individual_contributor` | `people_manager` | `unknown`) with a
  grounded `work_level_reason`, plus a byte-verbatim `people_management_quote`
  when the posting owns direct reports (including manager postings that also
  mention quota or account ownership). Only literal `individual_contributor`
  decisions with a rationale and no people-management evidence are admitted;
  manager ownership, `unknown`, and missing/malformed decisions are excluded and
  recorded with their rejection category in `exclusions.json` and run lineage.
  A `Manager` title alone is not people-management evidence, and advising or
  coordinating colleagues is not direct-report ownership. Long descriptions are
  windowed around responsibilities/duties headings within the fixed
  1800-character per-posting prompt bound, and release validation rejects any
  admitted posting without a clean individual-contributor decision.
  **Reconciliation** produces cited foundation and
  in-sample demand claims plus learning priorities authored in priority order.
  **Evidence linking** is a separate bounded agent pass: it selects, from the
  recorded claim ids/statements/quotes and role/variant scope, the claims each
  priority cites, with a short rationale. Deterministic validation only enforces
  identity/role/basis/variant discipline — unknown, other-role, wrong-basis or
  cross-variant ids are dropped, and a priority with no surviving link is dropped
  with an inspectable disagreement; lexical overlap is never used as evidence.
  **Challenge** drops unsupported claims and records disagreements.
- **Release** merges the slice into `preview/release/releases/<release_id>/`,
  writes write-once claim citations and atomically updates `current.json`. Thin or
  failed runs keep the previous release. The same core serves the frontend, the
  read-only `/api/*` function, the `skills_vector market` CLI and MCP tools; raw
  responses and model/tool receipts stay in the external evidence root.
  The release content hash includes exclusions, mappings, disagreements and
  lineage as well as core datasets and verified extract bytes. Re-publishing
  identical content keeps the same release id and citation bytes; a change to
  supporting evidence receives a distinct immutable version. A validated run
  snapshot is written before merging, so the immutable release contains its own
  generating-run lineage. The finalized publication receipt remains external
  rather than creating a recursive release-id dependency.

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
