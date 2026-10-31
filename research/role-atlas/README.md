# Role Atlas research projection and ledger

## Selected union Q4 publication (M9-r5)

The deployed API default, Vercel runtime bundle and assembled static export select
`2026-Q4-union/`, release **`role_research_7333966260cf6538`**. Explicit operator
environment selectors still win and fail closed if invalid; the CLI/library still
requires explicit selection. This is configuration/build output, not a deployment.

The reviewer-run 71-ATS-board + 28-Workday-tenant union capture admits **564**
postings: AE **495/47** postings/employers, FDE **37/19**, Growth **20/12**, HR
**12/10**. It is one quarterly stock, not hiring flow or a second history window.
Source coverage is derived per role from admitted rows, not the global Method
table. All 3,024 nodes remain early, with 3,024 pending none-basis calls and no scores or probabilities.
Only AE Information has a sector profile (**217 postings / 12 employers**).

The compact tree is **5,982,575 bytes / 11 files**, ledger **297,377 bytes**.
Its **25,562,056-byte** audit is external and hash-pinned by `release.json`.
The 400 KB overview gate is unchanged: lower-employer tail labels may be
abbreviated to 80 characters; selection retrieves full labels and evidence.
Full records and agent JSON retain every node. No status or employer floor changed.

**Extraction remains provisional.** Flat-heading misses, corporate/benefits/
legal tails and lexical errors remain. HR's posting-ranked top nodes in particular
contain company/pay boilerplate from a single employer, not a validated HR skill
ranking. See the exact per-role top tens and limitations in
[PROGRESS.md](../../docs/role-atlas/PROGRESS.md).

```sh
.venv/bin/skills-vector market serve --role-atlas-root research/role-atlas/2026-Q4-union/roles --role-atlas-ledger research/role-atlas/2026-Q4-union/ledger.json
```

## Superseded verified-sector Q4 publication (M9)

Retained, unselected: `2026-Q4-sectors/roles` with `2026-Q4-sectors/ledger.json`,
release `role_research_71d6040270516002`. Its manifest alone now carries a
supersession pointer; its records, views and ledger are unchanged. It reused
exactly the 489 canonical postings,
adds SEC-backed broad employer sectors, and qualifies AE Information at 205
postings / 12 employers. Every below-floor sector retains counts and a null
profile. Unverified-employer counts are separate, role-wide API observations.
That historical sector-only publication contains no real structured-page
volume; the selected union publication above adds the reviewer-captured volume. The original M8 Q4
publication below is unchanged and retained on disk.

[Evidence, JSON-LD policy and exact operator workflow](../../docs/role-atlas/SECTORS_AND_STRUCTURED_CAREERS.md).

## Canonical 2026-Q4 (M8)

`2026-Q4/` contains compactly serialized full research records, hash-bound light
views, a small `release.json` manifest, compact none-basis forecast ledger and
explicit no-archive backtest. The full source-bound audit is **not in this tree**;
its byte hash/size are in the manifest and its operator-local location is
`~/.local/share/skills-vector-evidence/role-atlas/releases/<release_id>/audit.json`.
`read_audit` verifies it when present or reports **audit not available locally**.
It is never bundled into the function. Its 71-board canonical frame
supersedes the starter capture; the two captures are never mixed. It is a
tech-heavy public-board sample, not a market census. No raw board payloads or
full descriptions are committed. Original inputs remain external/read-only.

```sh
.venv/bin/skills-vector market serve --role-atlas-root research/role-atlas/2026-Q4/roles --role-atlas-ledger research/role-atlas/2026-Q4/ledger.json
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-q4 --role-atlas-root research/role-atlas/2026-Q4/roles --role-atlas-ledger research/role-atlas/2026-Q4/ledger.json
```

The release is `role_research_a490fb6c01baaadd`: AE 436/36 postings/employers,
FDE 31/17, Growth 17/11, HR 5/5. All 2,326 skill nodes remain early in one quarter;
all 2,326 ledger calls are pending with `basis: none`, null probabilities/base
rates and no scored/calibrated findings. There are three qualified AE edges;
all 80 employer-sector profiles remain null without reviewed primary sectors.
The raw node count increases where multi-sentence input spans are split for
safe publication; canonical tools/terms do not imply semantic coverage of every
unmatched phrase. Residual boilerplate and a small lexical-audit error rate are
reported in `docs/role-atlas/PROGRESS.md` and `audits/M8-matches.json` there.

For more than 100 skills, the default overview shows nodes mentioned by at least
three employers. Each status retains an expandable lower-employer group; the
full API/JSON and all rows remain available. The page fetches only the selected
role overview first, renders the id/label tail on expansion, and fetches its
complete skill evidence on selection. Other roles, full JSON, level expectations,
Connections and gap data are loaded only on demand. This display floor is separate
from status/edge/profile thresholds. AE segment scope follows the approved
M7-r2 refinements: same-level labels agree; Named in duties alone is not L5.

### Light and full reads (REST/MCP parity)

- `GET /v1/roles/{role}?view=overview` / `get_role(role, release_id?, view="overview")`:
  complete skills at the stated employer floor; each status tail has only count
  and id/label pairs. For large catalogs, level expectations are explicitly
  deferred; the existing level endpoint or full view supplies them.
- `GET /v1/roles/{role}/skills/{skill_id}?release=...` /
  `get_role_skill(role, skill_id, release_id?)`: complete skill, pinned to the
  role/release, with identical errors/caveats in REST and MCP.
- Default and `view=full` stay byte-for-byte unchanged. Lazy/overview records
  are partial views, **not new schema-valid full role records**. The manifest
  binds each materialized light view and source file by byte hash; edits fail
  closed. No runtime data/fixture fallback is introduced.
- Ledger storage `/2` groups shared fields once per role and stores pending none
  calls as `[id, skill_id, created, resolves, status]` rows. `read_ledger` expands
  to the existing `/1` math model losslessly. Non-pending/scored calls retain
  their complete receipts. Issued ids, source pins and track-record JSON do not
  change.

Research `market posting-volume project` now writes this small publication
layout by default. `--evidence-root` can select another **out-of-tree** audit
store. Use a fresh output directory; ledger/backtest are then separate operator
commands. `--inline-audit` is an explicit diagnostic scratch-only escape hatch,
not a publication command. Never copy its audit into git. M4/M5 historical
artifacts are unchanged; git history is not rewritten.

The checker enforces **400,000 overview bytes / 2,000 ms first interactive map /
1,000 ms status-tail expansion** on the initial AE page in all three cases,
before any full-record verification fetch. Navigation/click-to-paint timings
are observed independently in the browser, with no role warm-up. Results include
measured budgets; overruns exit nonzero. Current release bytes are also tested
against **6,000,000**, and its ledger against **300,000**.

## Approved M4–M5 snapshots

Build a new projection offline from a selected, published release and checked-in local mappings.
The approved `m4/` snapshot is preserved; contract/recipe changes intentionally
produce a different release id and must use a new output directory:

```sh
.venv/bin/python -m skills_vector.market.role_pipeline --output /tmp/role-atlas-new-projection
```

Default input: `preview/release/current.json` and its selected release directory,
read-only. `--release-id` pins an indexed release; mappings must be reviewed for
that release. `--mapping-root` selects the checked-in mapping files.
`--window-length quarterly|monthly` configures retrieval-date bins (quarterly by
default). This is a single-snapshot projection, not a historical archive: versions
and repeated postings are never manufactured into independent temporal windows.
Only the most recent observed bin is emitted, with other-bin exclusions audited.

Output is separate from every input tree:

- `m4/roles/*.json`: validated `skills-vector.role/2`, `data_kind: research`.
- `m4/audit.json`: input/recipe/contract hashes, per-role counts, per-posting
  strong/weak triage evidence and reason, phrase/backbone links, and every dropped
  observed co-occurrence pair with all failing floors. Pairs that never co-occur
  are not candidates. Floor-failure counts can overlap.

The output id hashes the selected release files, mapping bytes, recipe code,
contract/schema and cadence. It contains no wall clock or absolute output path.
Identical reruns are allowed; different or unrelated existing output is rejected.
Input ancestors/descendants and symlink escapes are refused. No output is written
until all role models validate. Existing releases/current.json are never written.

## Serving and browser verification

Use the existing `market serve --role-atlas-root research/role-atlas/m4/roles`
selection, or `SKILLS_VECTOR_ROLE_ATLAS_ROOT=research/role-atlas/m4/roles` in the
deployed API. The Vercel function includes this research tree, but selection is
still operator-explicit: no default fixture/research fallback was enabled. Point
at **roles/**, not the audit's parent. REST and MCP use the same selection.

```sh
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m4-final --role-atlas-root research/role-atlas/m4/roles
```

## Thin-data representation and forecasts

M4 does not copy prototype numbers. `first_level: null` means unassigned; null
`share_by_level` cells mean no assigned denominator, not zero demand. Measurement
strata can explicitly include `unassigned`. Empty level expectations/titles are
valid; `introduced_at: null` does not claim introduction or carry-over. Existing
fixture measurements remain unchanged and forecasts explicitly use `basis: fixture`; status, edge and sector floors are unchanged.
The reader shows unresolved nodes separately, missing samples as `?`/dashes, and
empty gap comparisons as insufficient evidence rather than "nothing missing".

Every current skill has one observed window, so every status is `early`.
Unverified employer mappings contribute to no sector: all 20 broad sectors remain
visible with null profiles and actual zero verified counts. No employer count,
previous window, official wage, proficiency rating or sector share is padded.

Every non-core skill carries a dated four-quarter forecast object, but the
current pipeline cannot forecast: `basis: none`, `probability: null`,
`base_rate: null`, with a reason stating the missing historical archive. Claim,
horizon, creation/resolve window and scoring target are preserved for M5. Current
recorded mention fractions are measurements, never substitutes for probabilities.
The UI says **No forecast yet**, with the reason and claim in the detail panel.
Only `basis: backtest` or `basis: fixture` permits numeric probabilities/base rates;
these are required and bounded to 0–1. The separate M5 ledger does not mutate
these immutable role forecast objects.

## M5 official files and ledger

[Operator workflow and formats](../../docs/role-atlas/OFFICIAL_SIGNALS_AND_LEDGER.md).
Importers read local O*NET/BLS/CareerOneStop files only; authored samples cannot
merge into research. No official files or historical posting archive were
supplied in M5, so no sample is asserted as an official research finding.

`m5/ledger.json` tracks all 38 M4 research forecast skeletons, with `basis: none`,
null probabilities/base rates and no structured events inferred from prose.
`m5/backtest.json` reports **not run: no historical posting archive** with null
Brier and empty calibration. There are no resolutions or scores.

Select it explicitly in addition to the existing roles root:

```sh
.venv/bin/skills-vector market serve --role-atlas-root research/role-atlas/m4/roles --role-atlas-ledger research/role-atlas/m5/ledger.json
```

The Method section and `/v1/track-record` show the same pinned track record;
fixtures/none calls are excluded from scoring. The source role JSON stays intact.
