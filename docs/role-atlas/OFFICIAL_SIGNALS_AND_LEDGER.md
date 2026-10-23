# Offline official signals and forecast ledger (M5)

All commands are under `skills-vector market`. They read local files only.
There is no downloader, API-key option, model, public mutation endpoint or new
dependency. Output cannot overlap inputs, checked-in fixtures, or the published
release tree; output symlinks and differing immutable artifacts are rejected.
Ledger updates are atomic JSON replacement for a **single operator/writer**.

## Import a previously downloaded file

```sh
.venv/bin/skills-vector market signals import hot-technologies --input "/local/Technology Skills.txt" --retrieved-at 2026-10-02T00:00:00Z --output /tmp/hot.json
.venv/bin/skills-vector market signals import emerging-tasks --input "/local/Emerging Tasks.txt" --retrieved-at 2026-10-02T00:00:00Z --output /tmp/tasks.json
.venv/bin/skills-vector market signals import bls-industry --input /local/occupation.xlsx --year 2024 --retrieved-at 2026-10-02T00:00:00Z --output /tmp/matrix.json
.venv/bin/skills-vector market signals import certifications --input /local/certifications.json --soc 13-1071.00 --naics 51 --retrieved-at 2026-10-02T00:00:00Z --output /tmp/certifications.json
```

Use the **actual** retrieval timestamp, not these example dates. Retrieval and
source as-of dates are different. No date is inferred from the wall clock.
O*NET TSV/TXT, scalar CSV/XLSX tables, and saved CareerOneStop JSON are supported.
Spreadsheet formulas are rejected, not executed; no workbook links are followed.
The first worksheet must contain the matrix. Local value-only CSV export is an
alternative for older binary XLS files, formula-bearing workbooks or other layouts.

Column/key samples and public format references:
[`tests/samples/role-atlas/README.md`](../../tests/samples/role-atlas/README.md).
BLS accepts base-year National Employment Matrix code/occupation code, industry
code/title, Employment and Percent of occupation employment columns, including
two/three-row headers and merged spreadsheet cells. Numbers retain upstream
thousands/percent units. The projected-year columns are not skill evidence.
CareerOneStop requires CertList, CertId, CertName, Organization (or CertOrg).
Its original query SOC/optional NAICS comes from the operator, not an invented
column. This catalog is not evidence that employers require a credential.

Normalized `skills-vector.official-signals/1` bundles retain input filename/hash,
native field values, scope, stable content ids and per-signal `retrieved_at`,
source/source_kind, timing and family:

| Source | Timing | Family | Direction |
| --- | --- | --- | --- |
| Hot Technologies (Hot Technology=Y) | coincident | M | presence, not measured growth |
| Emerging Tasks (Task Type=Emerging) | leading | O | up: an addition to the official task record |
| BLS occupation × industry | lagging | context | presence |
| CareerOneStop | lagging | O | presence |

Authored sample inputs require `--sample`. Output says `data_kind: sample` and
**cannot merge into research**, even with an explicit binding. Samples are not
official observations; source-format testing is not evidence about the market.

## Deterministic role/2 merge

```sh
.venv/bin/skills-vector market signals merge --roles-root research/role-atlas/m4/roles --bundle /tmp/hot.json --bundle /tmp/tasks.json --bundle /tmp/matrix.json --bundle /tmp/certifications.json --output /tmp/roles-with-official-signals
```

Default merge is **anchor context only**, in foundation.sources. O*NET/COS use
exact detailed anchor codes; BLS parent SOC is context, not title equivalence.
Unmatched rows are listed in `_provenance/merge.json`. Retrieval must not postdate
the role window (UTC). No label normalization, employer sector assignment,
denominator changes, edge padding, broad-sector aggregation or certification
requirement inference occurs. Null sector profiles stay null.

For a justified skill alignment, supply `--bindings bindings.json`, a list like:

```json
[{
  "signal_id": "signal.<id from the imported bundle>",
  "role_id": "role.forward-deployed-engineer",
  "skill_id": "<exact existing skill id>",
  "confidence": "close",
  "rationale": "<specific relation supported by the native row>"
}]
```

Confidence is exact/close/loose, qualitative, not a probability. Only exact/close
O/M bindings enter skill evidence; loose proposals stay audit-only. Context cannot
corroborate skill growth. The existing pure `derive_status` function recalculates
bound skills; **all M1 floors/rules stay unchanged**. Labels are never rewritten.
Every output validates via `RoleRecord.from_dict` before any writes. The new
release pin and expectation citations are content-derived. Identical reruns match;
different existing output is rejected.

## Create, backtest, resolve and score

```sh
.venv/bin/skills-vector market forecast-ledger create --roles-root research/role-atlas/m4/roles --ledger /tmp/ledger.json
.venv/bin/skills-vector market forecast-ledger backtest --ledger /tmp/ledger.json --output /tmp/backtest.json
.venv/bin/skills-vector market forecast-ledger show --roles-root research/role-atlas/m4/roles --ledger /tmp/ledger.json
```

Without an archive the literal status is **not run: no historical posting archive**,
with a reason, null Brier and empty calibration bins. The checked-in
`research/role-atlas/m5` contains this honest current state: 38 pending none-basis
calls, no estimated probabilities, resolutions or scores. No real official files
or historical posting archive were supplied; authored samples are not promoted.

The ledger retains id, role/skill/origin release and source digest, exact claim,
probability/base_rate, basis/reason, created/resolves, horizon and scored_against.
Research calls **always enter as none**, even if input records contain numbers.
Fixtures keep their explicitly illustrative basis but never contribute to scores.
Create can append later cohorts; identical calls are idempotent. Issued sources,
events, claims, probabilities and resolutions cannot be revised in place. A
republication of the same call in another release is not an independent forecast.

No thresholds are parsed from prose. To make a claim mechanically resolvable,
supply `create --events events.json`, after reviewing the claim's meaning:

```json
[{
  "role_id": "<role id>",
  "skill_id": "<skill id>",
  "claim": "<exact source forecast claim>",
  "event": {"kind": "share_at_least", "threshold": 0.2},
  "rationale": "<why this precisely operationalizes that claim>"
}]
```

`share_below` is also supported; thresholds are fractions. The event uses the
source measurement's entire explicit level scope (including unassigned), cadence
and comparison_group. Do not bind a window-share threshold to a claim about a
single future posting. Such skeleton claims remain tracked without a bound event;
this is intentionally the state of the current M4 claims, not an invented event.

```sh
.venv/bin/skills-vector market forecast-ledger backtest --ledger /tmp/ledger.json --archive /local/historical-role-snapshots --output /tmp/backtest.json
.venv/bin/skills-vector market forecast-ledger resolve --ledger /tmp/ledger.json --roles-root /local/resolving-role-snapshot
.venv/bin/skills-vector market forecast-ledger score --ledger /tmp/ledger.json --roles-root /local/origin-role-snapshot
```

Archive: directory of validated research role/2 JSON snapshots, optionally nested.
There must be independent posting windows, stable skill ids and the **same**
sampling group, level scope and cadence. Duplicate comparable windows are rejected;
select one origin/resolution release explicitly with `--release-id` if necessary.

The conservative predictor is the empirical frequency of the bound event across
archived origin → 12-month target pairs, whose targets strictly precede creation.
Rolling-origin validation uses only targets strictly before each historical
origin. At least two matured pairs and one causal held-out prediction are required.
No future/current outcome, fixture archive, missing skill or changed scope fills
a gap. Receipts retain archive/pair hashes, counts, causal scores and recipe.
Probabilities are frozen once estimated; already-resolved none calls cannot be
retrofitted. This is a thin historical base-rate baseline, not a learned,
evidence-profile-conditioned or market-wide calibrated model.

Resolution requires the **exact** four-quarter target window and a measured
nonempty matching scope. Missing skills are not zero; too-early/late windows,
missing events and incompatible samples are deferred with reasons. None calls
can resolve but are never scored. Brier is the equal-weight mean of (p − y)²
over resolved research/backtest calls. Ten left-closed probability bins include
100% in the last bin; empty scores yield null/[] rather than zero.

## Read surfaces and Method

Select a ledger separately; it never enables or changes catalog selection:

```sh
.venv/bin/skills-vector market serve --role-atlas-root research/role-atlas/m4/roles --role-atlas-ledger research/role-atlas/m5/ledger.json
```

Environment: `SKILLS_VECTOR_ROLE_ATLAS_LEDGER` alongside the existing explicit
root/fixture selection. REST `/v1/track-record?role=...&release=...` and MCP
`get_track_record(role, release_id)` are identical. Full-role envelopes also
carry track_record **outside** the immutable schema-valid role record. The
ledger's lifecycle never mutates the published role's forecast JSON or release pin.

Counts/scores are filtered by exact source role/release and verified source hash.
Missing, corrupt or wrong-kind explicitly selected ledgers fail closed. No ledger
selected is an honest empty state. The Method section renders these API values,
with observed-frequency calibration bars, bin-midpoint ticks, mean p and n.
There are no copied prototype calibration or official-map-lag numbers.
