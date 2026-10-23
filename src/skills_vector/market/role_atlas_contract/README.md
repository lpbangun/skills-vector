# Role Atlas contract (M1)

Contract data lives in `market/role_atlas_contract/`, separate from the Python
module `market/role_atlas.py`.

- `role.schema.json`: JSON Schema Draft 2020-12, `skills-vector.role/2`.
- `../role_atlas.py`: typed dataclasses, lossless JSON loading, structural validation,
  pure status derivation, evidence-rule validation and a read-only sibling of the
  market `CatalogStore`.
- `fixtures/`: three **fixture** records. Access is explicit via
  `RoleCatalogStore.fixtures()`. These are not a fallback for market releases.
- `prototype.json`: frozen illustrative inputs from
  `design-concepts/role-atlas.html`, labeled `data_kind: "fixture"`. A test compares
  it with the prototype literals without executing JavaScript.

Use `RoleRecord.from_dict()` / `from_json()` to load a record. Construction and
`validate_role()` enforce both structure and evidence rules; schema validation
alone cannot enforce arithmetic, source-kind metadata or cross-record references. Unknown fields are
rejected, not silently dropped. JSON Schema uses local references only. The
`jsonschema` package was already a transitive MCP dependency; it is declared
explicitly because this contract now uses it directly.

`RoleCatalogStore(root)` reads role/2 JSON documents from an explicitly selected
catalog directory. `list_roles(release_id=None)` returns full records;
`role(slug_or_id, release_id=None)` returns a full record or `None`. An unknown
release returns no records. Parsing and validation are cached by resolved path
and nanosecond mtime. Changed files are revalidated; stale/deleted cache entries
are pruned after a successful read. Each public read returns a fresh dictionary,
so callers cannot mutate cached models. Duplicate role/release pairs and invalid
records fail closed, including modified files. Neither the store nor the fixture
builder reads or writes `preview/release/`.

## Units and rule decisions

Skill `share_by_level` preserves the prototype's **0–100 percentages**. Window
measurements, industry skill/certification shares, employer concentration,
numeric probabilities and base rates use **0–1 fractions**.

Every forecast declares `basis`: `fixture` (illustrative) or `backtest` (historical
archive) requires both numeric probability and base_rate. `none` requires both to
be null and a nonempty `reason`. A non-core skill still needs the dated forecast
object: claim, horizon (4 quarters), created, resolves and scored_against are
retained even when no forecast can be made. No current sample fraction is a
substitute for a forecast probability or a historical base rate.

Status uses chronological windows with identical level scope, cadence and
`comparison_group`. The latest comparable pair supplies change; the current
window supplies employer count and concentration. This accommodates the
prototype's rising signals whose previous window had fewer than eight employers.
Core needs a current share of at least 25%, another comparable window at 25%+,
and ten current employers. Core does not need O/M confirmation or a concentration
cap. Fading is checked first (including for core), then core, then rising, then
early. Rising needs an O/M signal with `direction: "up"`. The other directions
are `down`, `flat` and `presence`; presence means listed without asserting a
trend. A lagging map that still lists a fading skill is presence and does not
block posting-driven fading. Decimal arithmetic preserves the exact five-point
boundary. `disputed` is explanatory text, not a fifth status.

Every Signal carries a closed `source_kind` as well as a display-only `source`.
`SOURCE_RULES` in `market/role_atlas.py` is the canonical timing/family table,
shared by validation and the fixture builder. Validation never infers source
identity from display-name substrings. The supported kinds are postings, new
posting phrasing, O*NET ratings/emerging tasks/Hot Technologies, ESCO, Hiring Lab,
Economic Index, BLS projections/industry matrix and CareerOneStop. For example,
O*NET ratings are lagging/O, Emerging Tasks leading/O, Hot Technologies
coincident/M, and BLS projections long-term/context.

Signals optionally carry `as_of`: a four-digit year or a valid ISO `YYYY-MM-DD`
date (null/omission means unknown). The validator checks calendar dates. Readers
render its year in source timing tags, never extracting it from `reading` prose.
M5-imported signals also carry `retrieved_at`, an ISO timestamp with timezone,
validated for actual calendar dates. It is not an as-of/rating date. The field is
optional and omitted when unknown so pre-M5 records round-trip unchanged. Every
new importer requires it and the merge preserves it. Source timing/family and
status rules are unchanged.

The fixtures use the frozen snapshot's O*NET rating years for foundation, skill
evidence and the long-run bar; other source dates remain unknown.

Window cadence is a demand-window config value, defaulting to quarterly in the
Python model and fixtures; monthly is supported. The horizon is always four
quarters (twelve months), and resolve dates are checked against creation dates.
Management remains a linked role. Top-level additions compare posting shares
with the core level; the O*NET long-run bar is separately labeled lagging.

## Fixture provenance and explicit assumptions

Rebuild offline:

```sh
.venv/bin/python scripts/build_role_atlas_fixtures.py
```

The builder has a fixed fixture destination, not a release-output argument.
Original role/level counts, pay/outlook strings, per-level skill percentages,
shift counts, sector counts, certification percentages, heatmap values and
forecast probabilities/base rates are preserved and tested. The contract
normalizes the prototype rather than adopting its older role/1 JSON shape:

- All records carry `data_kind: "fixture"` and a fixture-specific release id.
- Only emerging roles are experimental: FDE is experimental; established Account
  Executive and HR Generalist are standard. Account Executive retains its partial
  anchor fit. Publication is independent of anchor fit in both directions.
- The prototype's legacy `growing`/`contested` graph labels are not skill statuses.
  The skill-map `SIGNALS` supply statuses; dispute text remains a separate flag.
  Raw shift counts are retained as observations without promoting legacy states.
- The prototype states core peaks are steady across two windows, but supplies no
  historical per-level counts or skill-specific employer counts. Fixture core
  measurements repeat the peak share and use the role-wide employer count as an
  **illustrative assumption**, not measured level-specific coverage. This does
  not override the actual thin L4 FDE ladder counts (7 postings / 5 employers).
- Shift measurements use the original mention/denominator fractions, not rounded
  prose percentages. The prototype's 20% employer cap is an illustrative spread
  assumption, not measured allocation.
- Asked-together candidates retain their exact lift and posting counts. Candidates
  below 1.5 lift or 25 postings are excluded, as in the prototype. It provides no
  edge-specific employer counts: eight is an explicit **fixture-only assumption**
  to exercise the threshold, not an observed count. Rejected candidates remain
  visible in the frozen input snapshot, never as accepted edges.
- The thin FDE finance sector remains visible with `profile: null`, a reason,
  and its original 12 postings / 5 employers; it receives no heatmap shares.
- Source kinds, timing, families and directions, forecast ids, creation dates and
  the four-quarter horizon are contract metadata added to the illustrative inputs.
  Posting directions follow the numeric window changes (steady core is flat).
  Hiring Lab trend readings and the addition of an O*NET emerging task indicate
  up; existing O*NET/ESCO/Hot Technologies listings and high Economic Index use
  alone are presence, not rising confirmation. This preserves the prototype's
  statuses without treating map presence as an upward trend.
- The closed source-kind vocabulary has no wages-series kind. The prior extra
  wages/employment Signal is omitted rather than mislabeled as an industry
  matrix or projection; the illustrative pay/employment values are unchanged.
  No backtest scores are copied or claimed. All forecast rates are illustrative.

The same assumptions are disclosed in each fixture record's `limits`. These
fixtures exercise the contract only. They are not suitable input to publication
or an evidence pipeline, and nothing here implements M2–M5.
