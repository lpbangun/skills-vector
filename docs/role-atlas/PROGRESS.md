# Role Atlas progress

Milestone reports from the implementer. Newest last.

## M1 — Contract

### Built

- **C1–C2:** `src/skills_vector/market/role_atlas/role.schema.json` defines
  `skills-vector.role/2` (Draft 2020-12). Typed dataclasses and lossless JSON
  loading live beside the market core in `market/role_atlas.py`. The contract
  carries identity, publication/data kind/release, anchors, timed foundation
  sources, configurable demand cadence, scope/ownership ladder and expectations,
  skill evidence/windows/forecasts, graph edges, top-level adds, sectors and limits.
  Unknown fields fail closed instead of disappearing during decoding.
- **C3–C4:** Pure status derivation and validation enforce thresholds, comparable
  windows, agreeing evidence families, four-quarter forecasts and dated resolution,
  edge and sector floors, timing classes, partial-anchor publication and reference
  integrity. Tests cover exact 25% and five-point boundaries, 7/8 and 9/10 employer
  boundaries, 20%/21% concentration, unconfirmed rises, one window, fading core,
  missing forecasts/base rates/timing and invalid probabilities. Monthly windows
  also retain the four-quarter horizon; quarterly is the default.
- **C5:** Three fixture roles, 40 skills, 21 accepted asked-together edges and 11
  visible sectors (one thin/null profile). All records say `data_kind: "fixture"`
  and use `fixture_role_atlas_2026q3`. Original prototype numbers are preserved
  and compared in tests against the frozen input snapshot and the HTML literals.
  `scripts/build_role_atlas_fixtures.py` rebuilds deterministically into the fixed
  package fixture directory, never `preview/release/`.
- The explicit, read-only sibling `RoleCatalogStore` provides full-record list and
  role reads, optional release pinning, fresh values and honest missing results.
  Fixture selection is opt-in, not a fallback for published market data.
- **G2/G4:** 27 new offline tests, with accepted and rejected rule cases. Declared
  `jsonschema` as a direct dependency because the contract uses it; it was already
  installed/locked transitively through MCP. No additional resolved packages.
  Package-data globs include the schema, provenance and fixtures.

### Verification (real commands/results)

All commands ran in this worktree on `feat/role-atlas` using codemode batches.

| Command | Result |
| --- | --- |
| `.venv/bin/python scripts/build_role_atlas_fixtures.py` | Exit 0; generated `account-executive.json`, `forward-deployed-engineer.json`, `hr-generalist.json`, each printed with a `fixture:` prefix. |
| `.venv/bin/python -m unittest discover -s tests -p test_role_atlas.py` | Exit 0; **27 tests, OK**, no skips. Covers model → JSON → independent schema validation → model equality, prototype fidelity, deterministic rebuild and store reads. |
| `.venv/bin/python -m unittest discover -s tests` | Exit 0; **155 tests, OK (skipped=2)**. No new skips or changes to existing tests. |
| `.venv/bin/python -m compileall -q src/skills_vector/market/role_atlas.py scripts/build_role_atlas_fixtures.py tests/test_role_atlas.py` | Exit 0, no errors. |
| `uv lock --offline` and `uv lock --check --offline` | Both exit 0; resolved 61 packages. Lock diff only declares the existing jsonschema dependency directly. |
| `git diff --check` | Exit 0, no whitespace errors. |
| `git diff --exit-code -- preview/release/` | Exit 0, no release changes; fixture paths are outside that tree. |
| `.venv/bin/python -m unittest discover -s tests -p test_structured_job_analysis.py -v` | Exit 0; 30 existing tests, OK. |
| `.venv/bin/python -m unittest discover -s tests -p test_poc_environment.py -v` | Exit 0; 8 existing tests, OK. |
| `.venv/bin/python -m unittest discover -s tests -p test_structured_job_analysis_browser.py -v` | Exit 0; existing browser test skipped because no Playwright browser path/executable is configured. |
| `.venv/bin/python -m unittest discover -s tests -p test_structured_job_analysis_public_export.py -v` | Exit 0; existing class skipped because sealed original-run artifacts are absent in this worktree. |

The initial focused run (25 tests at that point) had one test-setup failure:
changing a core status to early without adding a forecast correctly hit schema
validation before the intended status assertion. The test now supplies a valid
forecast so it reaches the status rule; the final 27-test run passes. No contract
rule was weakened. Existing full-suite HTTP logs are local/in-process test
transports; no external service or model was called.

### Normalizations, deviations and open risks

- No M2 work: no REST/MCP routes, agent-guide changes or UI; no M4/M5 research
  production, official imports or forecast ledger. No secrets, paid/model calls,
  immutable releases or other worktrees were touched. Build metadata changes are
  solely for the M1 validator and packaged contract data.
- Partial-anchor Account Executive is experimental as well as FDE, enforcing C4
  despite the prototype's narrower visual experimental flag. HR remains standard.
- Prototype legacy shift labels are retained only in the frozen illustrative input;
  original shift counts are exposed as observations, not unsupported skill statuses.
  Skill statuses use the prototype's newer skill-map SIGNALS. Below-threshold edge
  candidates remain in the input snapshot but are not accepted graph edges.
- The prototype lacks historical core measurements and edge employer counts.
  Fixture-only additions are explicit in every record's `limits` and in
  `src/skills_vector/market/role_atlas/README.md`: core peaks are repeated using
  role-wide employer counts as an illustrative assumption, accepted edge employer
  counts are assumed at the threshold of eight, and concentration uses the
  prototype's illustrative 20% cap. None is represented as observed research.
  Original thin ladder/sector counts are not padded or changed. These fixtures
  must never be used as research evidence or written to the release tree.
- The documented overlap order is fading → core → rising → early; core does not
  require external confirmation, and an eligible fall moves a core skill to
  fading. The current window supplies employer/spread floors, consistent with
  the prototype's rising examples whose earlier windows have fewer employers.
- JSON Schema handles structure and local constraints; arithmetic, comparability
  and reference integrity require the companion Python validator. Forecast rates
  are illustrative; no backtest performance is claimed.

**G6:** M1 is committed once on `feat/role-atlas` with message
`feat(role-atlas): add M1 role/2 contract and fixture catalog`. Stop for review;
M2 has not started.

## M1 review round 1 replies

Re-read the corrected BRIEF.md and STANDARDS.md and addressed every must/should
finding in `reviews/M1-r1.md`. This section supersedes the original M1 report's
partial-anchor publication rule, boolean agreement field and old data paths.

1. **Must — experimental rule: fixed.** Publication now equals `experimental`
   exactly when role status is `emerging`, in both JSON Schema and Python
   validation. Established roles cannot be experimental. Account Executive is
   `standard` with its original `fit: partial`; FDE remains experimental and HR
   remains standard. Removed the partial-anchor rule and regenerated fixtures.
   Regression tests accept/reject both publication directions for every role and
   for partial, close and exact anchor fits.

2. **Must — direction of agreement: fixed.** Replaced `Signal.agrees` with the
   closed `direction` values `up`, `down`, `flat`, `presence`. Rising requires
   an O/M signal explicitly pointing up; presence, flat or down cannot confirm
   it. Tests assert presence → early, M/up → rising, O/M/down → early, and
   rejection of an asserted rising record without upward confirmation. Fading
   remains posting-driven and accepts a lagging map's continued presence.
   Fixture posting directions follow their numeric changes, including HR's up
   signals. Existing map/Hot Technologies listings and high Economic Index use
   are presence; Hiring Lab trends and the addition of the FDE emerging task
   point up. Prototype statuses, counts, shares and forecast rates still match.

3. **Must — source-kind timing/family: fixed.** Added the exact closed set of
   eleven `source_kind` values to Signal models and schema. `SOURCE_RULES` in
   `market/role_atlas.py` is the single canonical timing/family table, used by
   both validation and fixture construction. Display names no longer drive
   classification. The table-driven regression test accepts each canonical pair
   and rejects a timing mismatch and a family mismatch for every kind; it also
   covers the review's `O*NET importance`/coincident example. Missing/unknown
   kinds and directions and the old `agrees` property are rejected. The vocabulary
   has no wages-series kind, so the previous extra wages/employment Signal was
   omitted rather than mislabeled as a matrix/projection. All illustrative
   foundation pay/employment values remain unchanged; this is documented in the
   contract README.

4. **Should — module/data name collision: fixed.** Renamed the data directory to
   `src/skills_vector/market/role_atlas_contract/`. Updated `CONTRACT_ROOT`,
   package-data globs, the fixture script's documentation and the contract README.
   Python imports still use `skills_vector.market.role_atlas`. An offline wheel
   build and ZIP inspection verified that the module and all six contract data
   files are included, with no old `market/role_atlas/` directory entries.

5. **Should — repeated parsing/validation: fixed.** Chose a per-store cache keyed
   by `(resolved path, st_mtime_ns)`. Unchanged files reuse validated models;
   changed/new files are parsed and validated, and stale/deleted versions are
   pruned after successful reads. Public methods still return fresh dictionaries.
   Tests count parses across list/role reads, verify nested caller mutations do
   not leak, reload only the changed file, reject an invalid edit rather than
   returning stale data, and remove deleted records. Duplicate-role and fixture
   checks still run on every catalog read. This is an M1 store fix, not an M2
   read surface.

6. **Note — fixture caveats: retained.** Every fixture still carries
   `data_kind: "fixture"`, the fixture release id and the original `limits`
   language about synthetic core measurements, assumed edge employer counts,
   employer concentration and illustrative forecast rates. The renamed README
   preserves those caveats. No fixture was written under `preview/release/`.
   No API/UI work was started.

### Round 1 verification

Commands ran using codemode batches in this worktree on `feat/role-atlas`:

| Command | Real result |
| --- | --- |
| `.venv/bin/python scripts/build_role_atlas_fixtures.py` | Exit 0; printed all three fixture filenames and rebuilt the renamed fixture directory. |
| `.venv/bin/python -m unittest discover -s tests -p test_role_atlas.py` | Exit 0; **32 tests, OK**, no skips. Includes unchanged model/schema round-trip, prototype fidelity and deterministic fixture rebuild checks. |
| `.venv/bin/python -m unittest discover -s tests` | Exit 0; **160 tests in 33.836s, OK (skipped=2)**. The same pre-existing optional browser and missing sealed-original-artifact skips; no newly skipped tests. |
| `.venv/bin/python -m compileall -q src/skills_vector/market/role_atlas.py scripts/build_role_atlas_fixtures.py tests/test_role_atlas.py` | Exit 0, no errors. |
| `uv lock --check --offline` | Exit 0; resolved 61 packages, no lock or dependency changes. |
| `git diff --check` | Exit 0, no whitespace errors. |
| `git diff --exit-code -- preview/release/` | Exit 0; no immutable release edits. |

Offline packaging verification (exit 0; `wheel contract files: 6`):

```sh
tmp=$(mktemp -d /tmp/role-atlas-wheel.XXXXXX) && uv build --wheel --offline --out-dir "$tmp" && .venv/bin/python - "$tmp" <<'PY'
import sys
from pathlib import Path
from zipfile import ZipFile

wheel = next(Path(sys.argv[1]).glob('*.whl'))
with ZipFile(wheel) as archive:
    names = archive.namelist()
    prefix = 'skills_vector/market/role_atlas_contract/'
    contract = [name for name in names if name.startswith(prefix)]
    assert len(contract) == 6, contract
    assert 'skills_vector/market/role_atlas.py' in names
    assert not any(name.startswith('skills_vector/market/role_atlas/') for name in names)
    print('wheel contract files:', len(contract))
    for name in contract:
        print(name)
PY
```

No external services, paid/model calls or secrets were used. Existing suite HTTP
logs are local/in-process transports. No changes outside M1 review scope and no
M2 implementation. Remaining limitations are the explicit fixture assumptions
and normal mtime-cache semantics: content writers must advance file mtime when
changing a record. Fixes are committed on `feat/role-atlas` with message
`fix(role-atlas): address M1 review round 1`; stop for review.

## M2 — Read surfaces

### M1-r2 carry-over (done first)

- Changed `_validator()` to the requested process-wide `functools.cache` and
  added a regression test proving repeated lookups reuse the same checked schema.
  The previous implementation already had a bounded LRU cache; no claim that it
  was uncached is needed for the fix.
- Cached dataclass type hints and stopped structural validation at the first
  error instead of collecting/sorting every error. Removed one redundant second
  invocation of the same JSON Schema validator per nested-timing rejection case;
  every original valid/invalid case still runs through the real model/schema
  validator. Fixture catalogs remain initialized once per test class. No tests,
  cases or skips were removed/added to weaken coverage.
- Final focused result: **33 tests in 9.879s, OK; 10.086s shell elapsed**, below
  the requested 15s. This includes the new cache regression test.

### Built and standards evidence

- **R1:** All seven REST reads exist: role index, full role, level, graph, broad
  NAICS sector, shifts since a window, and expectation citation by `id@release`.
  `market/role_reads.py` is the shared, read-only query service over the role/2
  sibling catalog. `market/api.py` returns the existing status/detail error
  envelope convention with structured 404s for unknown role, level, sector,
  expectation and release. Invalid inputs return 400; mutations return 405;
  request-body bounds and HEAD behavior are preserved.
- **R2:** Seven read-only MCP tools call that exact service: `list_roles`,
  `get_role`, `get_role_level`, `get_role_graph`, `get_role_industry`,
  `list_shifts`, `get_expectation`. An official-SDK ClientSession over an
  in-process ASGI transport calls every pair and asserts identical JSON,
  including explicit release pins, not-found cases and invalid input. Another
  SDK test proves an unselected catalog cannot be enabled by a client.
- **R3:** Every successful read carries `release_id`; full schema-valid role/2
  records are under the `role` key of the common envelope. Explicit unknown pins
  never fall back. Tests use three temporary fixture snapshots to prove old
  role/subresource/citation reads stay pinned when a newer snapshot exists.
  Every expectation cite in all three packaged fixture roles resolves to the
  exact expectation and its role/level occurrences. Historical shifts compare
  stored states, with an exclusive `since` boundary and inclusive release pin;
  tests cover changes in both directions and absent/partial history.
- **R4:** Updated the served agent guide and machine-readable API schema with
  every endpoint/tool, input/pin conventions, configuration, fixture and
  experimental caveats, null thin sectors and missing-lineage/history behavior.
- **Explicit fixture selection:** Role Atlas is disabled by default (503), not
  a fallback for the legacy release tree. Local server/MCP entrypoints and
  `skills-vector market serve|mcp` accept mutually exclusive
  `--role-atlas-root` and `--role-atlas-fixtures`. Deployed `api/index.py` selects
  `SKILLS_VECTOR_ROLE_ATLAS_ROOT` or `SKILLS_VECTOR_ROLE_ATLAS_FIXTURES=1`.
  Conflicting/invalid configuration fails closed; research mode rejects fixture
  records. Each fixture success/projection retains `data_kind`, the explicit
  `fixture_caveat`, original `limits`, and applicable experimental publication.
- **Integration:** One selected service instance backs REST and MCP in
  `create_market_app`. Added `/v1` ASGI routes and Vercel rewrites to the existing
  function, preserving `/api` and the existing static preview. No M3 UI or
  assembly work. The standalone MCP module now actually invokes its existing
  `main()` so its documented/configured `--print-tools` entrypoint works.
- **G2–G4:** Added 16 offline M2 tests plus the cache regression. No new
  dependencies, paid/model calls, external network requests, secret access or
  immutable release edits. Dataclasses/stdlib and the existing maintained MCP
  SDK/router patterns remain in use.

### Verification (exact commands and real results)

Run from this worktree on `feat/role-atlas`, using codemode batches:

| Command | Result |
| --- | --- |
| `TIMEFORMAT='elapsed %3R s'; time .venv/bin/python -m unittest discover -s tests -p test_role_atlas.py` | Exit 0; **33 tests in 9.879s, OK; elapsed 10.086s**, no skips. |
| `.venv/bin/python -m unittest discover -s tests -p test_role_atlas_api_mcp.py` | Exit 0; **16 tests in 3.286s, OK**, no skips. Real ASGI/SDK requests, no external services. |
| `.venv/bin/python -m unittest discover -s tests` | Exit 0; **177 tests in 23.167s, OK (skipped=2)**. The same pre-existing optional-browser and absent sealed-original-artifact skips; no newly skipped tests. |
| `.venv/bin/python -m compileall -q api/index.py src/skills_vector/market src/skills_vector/market_cli.py tests/test_role_atlas.py tests/test_role_atlas_api_mcp.py` | Exit 0, no errors. |
| `uv lock --check --offline` | Exit 0; resolved 61 packages, lock/dependencies unchanged. |
| `git diff --check` | Exit 0, no whitespace errors. |
| `git diff --exit-code -- preview/release/` | Exit 0; release tree unchanged. |
| `.venv/bin/python -m skills_vector.market.mcp --release-root preview/release --role-atlas-fixtures --print-tools > /tmp/role-atlas-m2-tools.json` | Exit 0; valid tool JSON. A stdlib JSON inspection asserted all seven new read-tool names are present; CLI behavior is also covered by tests. |

The first standalone-module tool-list smoke produced no JSON because the existing
module defined `main()` without invoking it. Added the module guard and verified
both module and market CLI paths; the final smoke passes. Full suites passed
before and after this correction (final result above).

### Deviations and open risks

- No M3 work. Only the explicitly requested M1-r2 performance carry-over and M2
  read/configuration/routing/docs/tests changed. One M2 commit, no push or history
  rewrite: `feat(role-atlas): add M2 release-pinned REST and MCP reads`.
- The current M1 expectation contract contains level evidence counts, not
  individual posting ids or quote excerpts. Citation endpoints resolve the
  stored expectation/occurrences and explicitly return empty `postings` and
  `excerpts` with `evidence_status: not_recorded`; no source lineage is invented.
  Actual posting/excerpt lineage remains an evidence-pipeline input/contract
  extension for later work, not a claimed M2 finding.
- A single fixture snapshot cannot prove a status transition. `/v1/shifts`
  therefore returns no asserted transitions and `history_status: not_recorded`
  for that catalog. It returns real stored-state differences when historical
  snapshots exist, as the temporary multi-release tests demonstrate. It never
  retroactively applies current O/M confirmation to earlier windows.
- With no explicit pin, latest means greatest demand-window start, with lexical
  release id as tie-breaker. Clients should always reuse the returned release
  id. Historical files must be retained and treated as immutable by producers;
  these reads never publish or replace them. The inherited mtime cache requires
  content writers to advance mtime when changing files.
- Real role/2 research data is not produced by M2. Operators must configure a
  research catalog directory or explicitly opt into labeled fixtures. Legacy
  `--base-url` does not silently select local/remote Role Atlas data; deployed
  HTTP MCP shares its server's explicit role/2 selection.

M2 complete; stop for review. M3 has not started.

## M2 review round 1 replies

1. **Must — current shifts: fixed.** `/v1/shifts` and MCP `list_shifts` now return
   `current`: all non-core skills with a latest measurement after `since`, sorted
   rising → early → fading, then probability descending. Each carries role/skill
   ids, full/short labels, status, dispute flag, comparable measurements with
   shares/employers, evidence families/directions, forecast, publication/data kind,
   fixture caveat and a release-pinned skill `cite`. The citation endpoint/MCP
   resolver now resolves skill ids as well as expectation ids, without inventing
   posting excerpts. Existing historical transitions are unchanged under
   `state_changes`, with the original history status/note and coverage fields.
   The guide, schema descriptions and MCP descriptions document the distinction.
   Tests assert nine fixture current items, AE AI account research rising, fading
   inclusion, core exclusion, window boundaries, ordering, evidence/forecast values
   and every current citation. SDK tests verify REST/MCP parity including skills;
   historical transition tests still pass with their new collection name.

Verification:
- `.venv/bin/python -m unittest discover -s tests -p test_role_atlas_api_mcp.py`
  — exit 0, 17 tests, OK.
- `.venv/bin/python -m unittest discover -s tests` — exit 0, 178 tests in
  27.455s, OK (the same 2 pre-existing skips).
- `git diff --check` and `git diff --exit-code -- preview/release/` — exit 0.

No new dependencies, external services, secrets, release edits or fabricated
history. M3 has not started at this fix-up commit. Commit:
`fix(role-atlas): expose current shifts alongside state changes`.
