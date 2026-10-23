# Role Atlas research projection (M4)

Build offline from a selected, published release and checked-in local mappings:

```sh
.venv/bin/python -m skills_vector.market.role_pipeline --output research/role-atlas/m4
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
fixture records remain unchanged; status, edge and sector floors are unchanged.
The reader shows unresolved nodes separately, missing samples as `?`/dashes, and
empty gap comparisons as insufficient evidence rather than "nothing missing".

Every current skill has one observed window, so every status is `early`.
Unverified employer mappings contribute to no sector: all 20 broad sectors remain
visible with null profiles and actual zero verified counts. No employer count,
previous window, official wage, proficiency rating or sector share is padded.

The role/2 contract requires a four-quarter forecast on every non-core skill.
To satisfy that without inventing calibrated probabilities, M4 explicitly emits
an **uncalibrated sample-persistence baseline**: probability and base_rate both
equal the observed current role-wide **recorded expectation-excerpt** mention
fraction. Non-matches are not verified absence from full posting text; the source
excerpts are not exhaustive. The future event assumes the same admission and
excerpt-selection process. This is a declared
assumption about a future same-recipe sampled posting, not a measured four-quarter
transition rate, market probability, trend finding or learned forecast. The UI
calls it an unscored estimate. There is no forecast ledger, resolution, historical
base-rate archive or backtest. Those are M5, not implemented here.
