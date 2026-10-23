# Role Atlas research projection and ledger (M4–M5)

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
