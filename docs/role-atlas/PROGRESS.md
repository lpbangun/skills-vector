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

## M3 — Web app

Built the API-backed Role Atlas at `/role-atlas.html` (also `/role-atlas`),
next to the existing preview. The M2-r1 fix above is a separate commit,
`d5a76cd`; this milestone does not start M4.

### Implementation and scope

- `preview/role-atlas.html`, `.css`, `.js`: adapted the approved render's
  masthead, role picker, foundation/demand panel, five tabs, list/connections
  switch, evidence detail, ladder, industry heatmap, local gap and agent JSON.
  Preserved its light/dark tokens, serif/sans/mono typography and flat surfaces.
- W2: fetch `/v1/roles`, then release-pinned full role/2 records. Reject mismatched
  schema, release or data kind; show a recoverable error instead of falling back
  to embedded fixtures. Role names, measurements, evidence, forecasts, limits,
  levels, sectors and JSON all come from those responses. The agent pane displays
  and copies the exact schema-valid record under `role`, with its pinned URL.
- W4/W5: WAI-ARIA tabs with roving focus and arrows/Home/End; full-label map
  buttons; visible keyboard focus and restored focus after regenerated controls;
  44px targets; text labels alongside color, including a separate graph dispute
  marker/detail. Fixture and experimental notices use API fields. Gap comparison
  runs in memory in the browser; checked skills are neither uploaded nor stored.
  Optional localStorage remembers only view preferences and cannot block browsing.
- `preview/role-atlas-assets/`: self-hosted IBM Plex Sans, IBM Plex Mono and
  STIX Two Text, with upstream OFL licenses and a source note. About 1.3MB of
  fonts preserves the reference typography without runtime third-party requests.
- `market/web.py` serves the page/assets with the existing safe static resolver;
  `scripts/assemble_vercel.sh` copies them beside the unchanged legacy preview.
  `tests/test_role_atlas_web.py` covers routing, traversal rejection, the
  accessible/no-embedded-data skeleton and isolated assembly.
- M3-required adjacent API change: `market/role_reads.py` adds optional, labeled
  fixture-only `presentation` family/row layout to role/graph envelopes. It reads
  the already checked-in prototype snapshot, not frontend constants. The role/2
  record/schema is unchanged. Research selections and unmatched custom fixtures
  receive no fixture layout; reads return fresh values. Updated the guide and
  REST/MCP parity/selection tests. No evidence classification or producer was added.

### Verification commands and real results

All commands below exited 0.

```sh
.venv/bin/python -m unittest discover -s tests
```

Final run: **182 tests in 60.193s, OK (skipped=2)**. Both skips predate this
milestone; no newly skipped tests. Focused API/MCP and web runs also passed:

```sh
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_api_mcp.py
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_web.py
```

**18 tests OK** and **3 tests OK**, respectively. They include REST/MCP equality
with presentation metadata, research isolation, unknown/mismatched fixture layout
fallback, fresh-value behavior, legacy preview and assembly checks.

```sh
uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m3-final-evidence --role-atlas-fixtures --compare-reference
```

W3: explicit executable path
`~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`. All three roles,
all five tabs and connections visited in each requested case:

| Case | App screenshots | Reference screenshots | Errors |
| --- | ---: | ---: | --- |
| 1280 light | 18 | 18 | none |
| 1280 dark | 18 | 18 | none |
| 390 light | 18 | 18 | none |

**108 checker screenshots**, with `results.json`, under the path above. The
checker fails on console/page errors and page overflow. It also asserts API
values, status/level filters, graph nodes/edges and top-level adds, dispute flags,
sector-floor gaps, exact JSON and copy behavior, tab/focus/target semantics,
local gap privacy, preference restoration and legacy preview. It runs with
reduced motion enabled. Additional probes verify unavailable/retry, schema/release/data-kind mismatch
rejection, escaped API text, absent optional layout and disabled-storage handling.
Product font loads are verified; comparison uses the same local fonts and UTF-8
reference HTML without external requests.

Opened nine side-by-side comparison images from the final evidence directory:
AE map at 1280 light and 390 light; FDE map at 1280 dark; HR levels at 1280 light;
FDE industries at 1280 light and 390 light; HR gap at 390 light; HR agents at
1280 dark; and an AE connection-detail crop from both rendered pages. Checked
masthead/type, tokens, panel proportions, ladder, heatmap, checkbox list, JSON
and connection-family layout against the reference. These are manual visual
comparisons, not a claim of pixel-identical screenshots. Earlier browser checking
found and fixed an inactive-tab arrow-focus bug; reference capture's font URL and
UTF-8 issues were also corrected before collecting final evidence.

```sh
bash scripts/assemble_vercel.sh
git diff --check
git diff --cached --check
git diff --exit-code -- preview/release/
uv lock --check --offline
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q scripts/check_role_atlas.py tests/test_role_atlas_web.py src/skills_vector/market/web.py src/skills_vector/market/role_reads.py
```

Assembly: **551 files**. Lock check: **61 packages resolved**. Diff/release-tree,
JS syntax and Python compilation checks were clean. No product dependency added.

### Intentional differences and open risks

- Canonical role/2 statuses and directional corroboration replace the concept's
  older status words/counts. Disputed is a flag, not a fifth status. The agent
  pane uses role/2 and the actual fixture release, not the concept's role/1 JSON.
- Prototype calibration/track-record numbers are not copied: the page explicitly
  says there is no resolved ledger/backtest result. Forecast/base-rate numbers
  remain visibly illustrative. M5 is not implemented.
- Connection SVG keeps a 960px minimum width for accessible node targets and
  scrolls inside its own container when necessary; the page never scrolls
  horizontally. Research without family layout uses explicitly described
  official-map-linked/posting-led bands, not invented domain-family assignments.
- Production data selection remains operator-explicit; no research producer,
  historical archive or signal importer was added. M4/M5 remain pending. The
  screenshot artifacts are in `/tmp`; the command above regenerates them.

No paid/model calls, secrets, immutable-release edits or unrelated refactors.
M3 complete for review; stop here before M4.

## M3 review round 1 replies

M2 approval in `reviews/M2-r2.md` acknowledged. This is an M3 review fix-up;
no M4 work is included.

1. **Must 1 — desktop Connections clipping: fixed.** The SVG now fits its
   container at desktop widths. Its 960-unit viewBox is unchanged, but the CSS
   minimum is 704px rather than 960px. Invisible 60×60 hit boxes remain at least
   44×44 CSS pixels at that minimum; their vertical offset includes the label
   without overlapping the next row. Below that width the chart alone scrolls,
   with a visible horizontal-scroll/arrow-key cue and a focusable scroll region.
   ResizeObserver keeps the cue current. This supersedes the original M3 report's
   960px-minimum deviation.

   `check_role_atlas.py` now requires, at 1280, `scrollWidth <= clientWidth` on
   the graph and every node/level-name bounding box inside its visible rectangle.
   It also checks exact API short labels, actual hit-box dimensions and cue
   visibility, and exercises keyboard scrolling at 390. Regression probes
   deliberately restore the old 960px minimum and move a node outside the
   visible rectangle: both must be rejected by the new fit check.

2. **Should 1 — structured rating year: fixed.** Review-requested contract
   extension: optional `Signal.as_of` in the model/schema, accepting a four-digit
   year, a valid ISO calendar date, or unknown/null/omission. Syntax is constrained
   by the schema; the runtime format checker also rejects impossible dates.
   The fixture builder uses only the frozen snapshot's `onet_year` values
   (AE 2021, FDE 2024, HR 2023), for foundation, O*NET skill evidence and the
   long-run bar. Other dates remain unknown. Fixtures were rebuilt; no market
   release was changed.

   Source timing tags render `lagging · rated {year}` from `as_of` in the role
   head, evidence detail and Method, never from prose. Method now collects the
   selected record's sources, so another role's year cannot overwrite its year.
   Tests cover year/date/null round trips, omission, invalid types/syntax/calendar
   dates, fixture provenance and deterministic output. Browser probes use a
   structured 2018 date with deliberately conflicting “rated 2099” prose, and
   then omit the date, verifying that the UI never parses the prose.

3. **Should 2 — plural copy: fixed.** The detail says “1 independent source
   points up” or “N independent sources point up”. Browser checks assert the
   actual fixture counts and add another upward source to exercise the plural.

4. **Optional note — ladder labels:** enabled `hyphens: auto` with the existing
   English `lang` metadata; browser checks verify both. Automatic hyphenation
   depends on browser dictionary support; the existing wrapping fallback remains.

### Exact verification commands and results

All commands below exited 0:

```sh
.venv/bin/python scripts/build_role_atlas_fixtures.py
.venv/bin/python -m unittest discover -s tests -p test_role_atlas.py
.venv/bin/python -m unittest discover -s tests
uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m3-r1 --role-atlas-fixtures --compare-reference
bash scripts/assemble_vercel.sh
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector/market/role_atlas.py scripts/build_role_atlas_fixtures.py scripts/check_role_atlas.py tests/test_role_atlas.py
uv lock --check --offline
git diff --check
git diff HEAD --exit-code -- preview/release/
```

- Builder: wrote the three explicitly labeled fixture files only.
- Focused contract tests: **36 tests, OK**.
- Final full suite: **185 tests in 61.105s, OK (skipped=2)**; the same two
  pre-existing skips. Includes REST/MCP parity and legacy preview/assembly tests.
- Final browser run: **1280 light, 1280 dark, 390 light**, every role and tab,
  18 app plus 18 reference screenshots per case (**108 total**), all `errors: []`.
  New graph-fit/negative probes, source-year and plural-copy checks all passed.
  Chromium: explicit `executable_path` to
  `~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`.
- Assembly: **551 files**. Lock check: **61 packages resolved**. JS syntax,
  compilation, whitespace and immutable-release checks clean.

Opened the saved 1280-light AE Connections screenshot next to the reference,
including a graph-detail crop. Confirmed **all five columns**, including L5
“Strategic AE” and “Multi-year deals”, and complete “Procurement and legal”,
“Strategic account plans” and “Executive alignment” labels. Measured graph
`scrollWidth = clientWidth = 754`. Artifacts under `/tmp/role-atlas-m3-r1/`:

- `app/1280-light/account-executive-connections.png`
- `reference/1280-light/account-executive-connections.png`
- `compare-1280-light-account-executive-connections.png`
- `compare-1280-light-account-executive-connections-detail.png`
- `connections-comparison-metrics.json` and `results.json`

No new dependency, paid/model calls, secrets or immutable-release edits. No known
remaining blocker from this review. Stop here; M4 has not started.

## M4 — Offline evidence pipeline

M3-r2 approval and its M4 carry-over were read. Research records have **no fixture
`presentation`**; the fallback official-map-linked/posting-led bands were exercised
against the generated research selection, not just against fixtures.

### Built and scope

- `market/role_pipeline.py`: deterministic operator module; no network/provider/
  LLM imports. Reads an indexed published snapshot, verifies the publisher's
  canonical manifest hash, included extract hashes and exact source-bound quotes,
  and validates mapping provenance. Defaults to quarterly retrieval-date bins;
  monthly is configurable. Selects the latest observed bin in that snapshot,
  explicitly auditing other-bin exclusions, with no invented comparable history.
- `market/role_atlas_mappings/{employers,skills}.json` and README: all 13 employers
  have source notes and are **unverified**, with null NAICS. Local excerpts do not
  verify primary-employer sectors; no assignment from names, customers or job
  functions. Such rows contribute to no sector. The 21 phrase alignments target
  11 exact, hash-checked local O*NET task excerpts. Unmapped skill phrases retain
  posting-led literal identities, not guessed ontology matches.
- Scope/ownership triage uses source-verified duty/capability quotes. Titles and
  tenure are retained as weak audit fields only, never votes. Past-experience
  wording is not current ownership. Conflicting strong levels and management
  scope remain `unassigned`. All evidence, reasons and phrase/backbone links are
  retained per posting in the audit. Multiple mentions count once per posting/node.
- Status derivation and all existing floors are reused unchanged. Observed pair
  lift uses actual marginals/co-mentions/employers; every dropped pair records its
  failing floors. Sector profiles require 30 postings and 8 employers from
  **verified** mappings; literal duties/credential/knowledge mentions are emitted
  only after that floor, without invented issuers or official catalog matches.
- Output: `research/role-atlas/m4/roles/*.json` plus sibling `audit.json`, all four
  records `data_kind: research`, validated by `RoleRecord`. Output id:
  **`role_research_c5c1bf7bde401bdd`**. Inputs:
  **`rel_20261002_cbdc6e8d5c4f`**, retrieval window **2026-Q4**. Id hashes input,
  mapping, recipe and contract/schema bytes plus cadence; no wall clock or output
  path. Different/unrelated output and input-tree/symlink overlaps are rejected.
- `tests/test_role_atlas_pipeline.py`: 12 tests, including byte-identical output
  in different directories and identical reruns, exact real-data counts, all edge/
  sector floor boundaries, nullable round trips/rejections, weak/strong/conflicting
  triage, bad hashes/quotes/mappings/paths, input protection, and real SDK REST/MCP
  parity over the research selection. No heavy dependency added.
- Guide/package metadata updated. The Vercel function includes the new research
  directory for operator-explicit selection; no automatic catalog selection,
  source retrieval, deployment or immutable-release publication was added.

### Necessary contract/reader changes (G3 scope note)

The fixture-era contract could not represent an unassigned skill, missing level
sample, or an empty level without inventing evidence. The same role/2 schema and
M1 validator now encode these absences: nullable `first_level`, nullable per-level
shares, explicit `unassigned` measurement stratum, empty title/expectation lists,
nullable `introduced_at`, and an empty skill list when none is recorded. Research
validation rejects numeric shares for absent denominators, missing shares for
observed denominators, and an asserted first level without an observed mention.
All prior M1 tests remain; no status, forecast, edge or sector floor was relaxed.
Existing fixture records were not rewritten. Consumers must support these explicit
unknowns rather than coercing them to L1 or zero.

The reader shows a separate **Unassigned** graph column (not L6), `?`/dashes for
missing samples, and insufficient-evidence gap/level/comparison states. It does
not claim BLS series or CareerOneStop imports where none exists. Unscored
persistence estimates are labeled as such. The checker accepts real missing data
while still asserting API values, graph fit, hit targets, notices, exact JSON,
privacy, and no fixture presentation on research responses.

### Per-role output counts

Level vectors are **L1 / L2 / L3 / L4 / L5 / unassigned**. Skill vectors are
**core / rising / early / fading**. The 20 sectors are a predeclared broad NAICS
vocabulary, not 20 observed industries. No employer-sector assignment was verified.
Only pairs actually observed together are edge candidates. Floor-failure counts
can overlap: one dropped pair may fail both the posting and employer floors.

#### Account Executive

| Metric | Count |
| --- | --- |
| Postings in | 5 |
| Postings per level, including unassigned | 0 / 0 / 0 / 0 / 0 / 5 |
| Skills by status | 0 / 0 / 11 / 0 |
| Edges kept / dropped | 0 / 12 |
| Dropped: lift / postings / employers floor | 1 / 12 / 12 |
| Sectors with / without profiles | 0 / 20 |

#### Forward Deployed Engineer

| Metric | Count |
| --- | --- |
| Postings in | 5 |
| Postings per level, including unassigned | 0 / 0 / 0 / 2 / 0 / 3 |
| Skills by status | 0 / 0 / 10 / 0 |
| Edges kept / dropped | 0 / 14 |
| Dropped: lift / postings / employers floor | 0 / 14 / 14 |
| Sectors with / without profiles | 0 / 20 |

#### Growth Manager

| Metric | Count |
| --- | --- |
| Postings in | 4 |
| Postings per level, including unassigned | 0 / 1 / 0 / 1 / 0 / 2 |
| Skills by status | 0 / 0 / 5 / 0 |
| Edges kept / dropped | 0 / 1 |
| Dropped: lift / postings / employers floor | 0 / 1 / 1 |
| Sectors with / without profiles | 0 / 20 |

#### HR Generalist

| Metric | Count |
| --- | --- |
| Postings in | 5 |
| Postings per level, including unassigned | 0 / 0 / 0 / 0 / 0 / 5 |
| Skills by status | 0 / 0 / 7 / 0 |
| Edges kept / dropped | 0 / 5 |
| Dropped: lift / postings / employers floor | 0 / 5 / 5 |
| Sectors with / without profiles | 0 / 20 |

Totals: **19 postings, 15 unassigned, 33 early skills, 0 kept/32 dropped observed
pairs, 0 populated/80 null sector profiles**. All input postings fall in the
current bin, so no older-bin exclusion affects these totals. This is honestly
thin output, not a market/trend finding.

### Exact verification commands and real results

All commands below exited 0:

```sh
.venv/bin/python -m skills_vector.market.role_pipeline --output research/role-atlas/m4
.venv/bin/python -m skills_vector.market.role_pipeline --output /tmp/role-atlas-m4-rebuild
diff -qr research/role-atlas/m4 /tmp/role-atlas-m4-rebuild
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_pipeline.py
.venv/bin/python -m unittest discover -s tests
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m4-final --role-atlas-root research/role-atlas/m4/roles
uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m4-fixture-regression --role-atlas-fixtures
bash scripts/assemble_vercel.sh
uv lock --check --offline
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector/market/role_pipeline.py src/skills_vector/market/role_atlas.py scripts/check_role_atlas.py tests/test_role_atlas_pipeline.py
git diff --check
git diff HEAD --exit-code -- preview/release/
```

- Producer: four validated role records and one audit. `diff -qr`: **no differences**
  for all five files. The determinism test also checks identical reruns and moves
  output locations; research API/SDK parity and release-pinned skill citations pass.
- Focused pipeline suite: **12 tests, OK**. Full suite: **197 tests in 57.241s,
  OK (skipped=2)**; the same two pre-existing skips, none newly skipped.
- Research browser: **1280 light, 1280 dark, 390 light**, all four roles, every tab
  plus Connections: **24 screenshots/case, 72 total**, all `errors: []`.
  Explicit Chromium executable is
  `~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`. Package resolution
  was forced offline for the final research run; page traffic is local-only.
- Fixture browser regression: same three cases, **18 screenshots/case, 54 total**,
  all `errors: []`. Legacy preview and existing API/contract tests remain green.
- Assembly: **551 files**; lock check: **61 packages resolved**. JS syntax, Python
  compilation and diff checks clean.

Published-tree integrity was checked against both pre-task SHA-256 receipts and
committed Git blobs. This reproducible Git-blob check also exited 0:

```sh
.venv/bin/python - <<'PY'
import hashlib, pathlib, subprocess
rows=subprocess.check_output(['git','ls-tree','-r','HEAD','--','preview/release/'],text=True).splitlines()
expected={}
for row in rows:
    metadata,name=row.split('\t',1)
    expected[name]=metadata.split()[2]
    data=pathlib.Path(name).read_bytes()
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==expected[name],name
actual={str(p) for p in pathlib.Path('preview/release').rglob('*') if p.is_file()}
assert actual==set(expected)
print(f'{len(expected)} published files match committed Git blobs, including current.json')
PY
```

Real output: **533 published files match committed Git blobs, including
current.json**. No original published file was modified or added.

Opened final research screenshots: AE Connections at 1280 light, FDE Connections
at 1280 dark, HR gap at 390 light, and Growth levels at 1280 light. Confirmed the
fallback source bands, visible separate unresolved assignments, full desktop fit,
missing-sample markers, unpopulated level/gap states and thin-evidence counts.
Artifacts: `/tmp/role-atlas-m4-final/` and its `results.json`.

### Deviations, limits and open risks

- The published excerpts do not supply official O*NET task database element IDs.
  Backbone ids are explicit local natural keys anchored to official SOC codes and
  exact O*NET task text, **not invented official element IDs**. No local ESCO
  snapshot exists, so no ESCO equivalence is asserted. Mapping alignments and
  scope rubrics are agent-authored, not human-adjudicated equivalence or validated
  career progression. This inherits the published sample/admission bounds.
- This build uses one selected snapshot, not eight repeated releases as eight
  independent periods. Retrieval bins are not complete quarterly market cohorts.
  No comparable past window or rising/fading/core evidence is manufactured.
- All 13 employer rows remain unverified. Primary-employer classification needs
  better local evidence before profiles can clear even the mapping gate; the
  actual samples also fall far below 30 postings/8 employers.
- Frequencies count recorded published expectation excerpts, **not exhaustive
  full posting text**. Non-matches are not verified absence of demand. Baselines
  explicitly assume the same admission and excerpt-selection process.
- M1 requires a four-quarter forecast for every non-core skill. The emitted
  **uncalibrated sample-persistence baseline** sets probability and base_rate to
  the observed current mention fraction. It is a declared assumption, not a
  measured transition base rate, learned trend forecast, market probability or
  calibrated result. Numeric official wages/headcounts/projections/ratings are
  not copied from fixtures. No ledger, importer, resolution or backtest was built.

No paid/model calls, secrets, input-release writes or unrelated refactors. M4 is
complete for review. Stop here; **M5 has not started**.

## M4 review round 1 replies

1. **Must 1 — sample fractions presented as forecasts: fixed.** The previous
   M4 sample-persistence policy was wrong; its discussion above is superseded.
   Current mention fractions are measurements, not future probabilities or
   historical base rates. Every regenerated research forecast now has
   `basis: "none"`, `probability: null`, `base_rate: null`, and a nonempty reason:
   “One observed window; no historical base rate or backtest archive.” Claim,
   four-quarter horizon, created/resolves and scoring target remain, without a
   prediction or invented numeric base rate. No M5 ledger/backtest was added.

   The schema/models require `basis` (`backtest`, `fixture`, `none`). Numeric
   probabilities/base rates are required and bounded to 0–1 for backtest/fixture;
   none requires nulls and a nonempty reason. Tests accept/reject each basis,
   missing fields, unsupported bases and blank reasons. Fixtures were rebuilt
   with `basis: "fixture"`, preserving their illustrative numeric values. API/MCP
   responses preserve nulls; shifts sort absent probabilities after available
   ones, without substituting zero (including a numeric-zero regression test).

   All forecast cells are now compact, one line; none displays **No forecast yet**
   with no percentage or claim in the row/ARIA forecast text. Claim and reason
   appear in the detail panel, without a big number. The checker asserts this
   for every skill, as well as numeric percentages for fixture forecasts.

2. **Should 1 — detail overflow: fixed.** Evidence spans use
   `overflow-wrap: anywhere` and `min-width: 0`; the panel inherits token wrapping.
   The checker verifies every descendant's bounds and each rendered text range
   inside the panel, not just page overflow. A deliberate no-wrap regression
   probe must be rejected. Long O*NET URLs and source ids now wrap inside the
   border at desktop and mobile sizes.

3. **Should 2 — nonexistent top-level comparison: fixed.** When either compared
   level has no assigned sample, the strip and graph summary state the missing
   comparison and actual assigned/total counts. AE says “Not enough level-assigned
   postings to compare levels: 0 of 5 postings have a level.” No hypothetical
   “Sales L3” comparison is shown. Generated levels with no observed titles are
   named only L1–L5; the reader also enforces that display rule. The checker
   compares this text to API counts and checks the missing-comparison graph state.

4. **Should 3 — mapping rationale/confidence: fixed.** All role/anchor, backbone
   and phrase rows in `skills.json` have nonempty rationale and qualitative
   confidence (`exact`, `close`, `loose`). These describe the particular relation,
   not a probability or human adjudication. All 21 phrase alignments were reviewed:
   **14 close, 7 loose**. Only exact/close phrase *and* backbone relations can
   create an O-family skill link. Loose rows retain their literal posting-led
   identity; audit links preserve the proposed target, confidence and rationale
   without asserting that target as a verified match.

   “Ensure high forecasting accuracy and consistency” is loose: forecasting
   quality is not record keeping. CRM use, language fluency, HR implementation,
   nonspecific counseling, and HR-system configuration/reporting also do not
   establish the previously proposed tasks and remain posting-led. Tests cover
   exact/close promotion, loose non-promotion, bad/missing rationale/confidence,
   and specifically prove the real forecasting-accuracy phrase has P only, no O.

### Note — missing scope signals (no triage changes)

There are still **15 of 19 unassigned**. Three recurring missing-signal patterns
in those 15, counted from published expectations plus audit weak fields (counts
**overlap**, not a partition):

| Pattern | Postings | What the extractor would need to retain |
| --- | ---: | --- |
| Task/capability descriptions without a leveling ownership boundary | 12 | Current-duty quotes establishing guidance/autonomy, end-to-end accountability, decision rights and deliverable scope; demos, implementation, communication or tool ability alone do not establish these. |
| Advertised experience/tenure blocks, but no current scope signal | 12 | Current responsibilities rather than years or past qualifications; title/tenure remain weak and cannot fill the gap. |
| Ownership/management language appears only as prior-experience wording | 4 | Current accountability and its boundary; distinguish employee/direct-report authority from vendor coordination or prior independent work. Three SpaceX HR excerpts and Chime's external-creative-partner experience illustrate this gap. |

The last count checks source wording beginning “Experience independently”,
“Experience leading”, “Experience managing” or “Experience directing”; the second
checks a nonempty audit `weak_signals.advertised_experience`; the first checks
at least one task/capability expectation, with no accepted current scope signal.
These are evidence-capture needs, not permission to assign a level from weak
wording. No new extraction, model call or triage relaxation was introduced.

### Proposed C4 wording — for the reviewer to commit

`STANDARDS.md` was **not edited**. Proposed replacement of the forecast bullets
under C4 (all other C4 bullets stay unchanged):

> Validation rejects:
> - a non-core skill without a forecast object;
> - a forecast without claim, horizon_quarters, created, resolves or scored_against,
>   or whose horizon is not 4 quarters (resolves must be four quarters after created);
> - a forecast without basis, or with basis outside backtest | fixture | none;
> - basis backtest (historical-archive base rate) or fixture (illustrative) unless
>   probability and base_rate are both present, numeric and within 0–1;
> - basis none unless probability and base_rate are both present and null, and
>   reason is a nonempty, non-whitespace string.

### Regenerated results

Output id: **`role_research_e647b3e2dce649d4`**. Same published input snapshot,
window, posting assignments and sector exclusions. Loose mappings no longer merge
unrelated phrases, so observed skill/pair counts change:

| Role | Postings / unassigned | Early skills | O-linked / posting-led skills | Edges kept / dropped | Sectors with / without profiles |
| --- | ---: | ---: | ---: | ---: | ---: |
| Account Executive | 5 / 5 | 12 | 3 / 9 | 0 / 12 | 0 / 20 |
| Forward Deployed Engineer | 5 / 3 | 11 | 2 / 9 | 0 / 14 | 0 / 20 |
| Growth Manager | 4 / 2 | 5 | 1 / 4 | 0 / 1 | 0 / 20 |
| HR Generalist | 5 / 5 | 10 | 2 / 8 | 0 / 10 | 0 / 20 |

All **38** research skills are early, and every forecast is none with both numeric
fields null. No core/rising/fading status or populated sector was manufactured.

### Exact commands and final results

```sh
.venv/bin/python scripts/build_role_atlas_fixtures.py
.venv/bin/python -m unittest discover -s tests -p test_role_atlas.py
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_pipeline.py
.venv/bin/python -m skills_vector.market.role_pipeline --output research/role-atlas/m4
.venv/bin/python -m skills_vector.market.role_pipeline --output /tmp/role-atlas-m4-r1-rebuild
diff -qr research/role-atlas/m4 /tmp/role-atlas-m4-r1-rebuild
.venv/bin/python -m unittest discover -s tests
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m4-r1-research --role-atlas-root research/role-atlas/m4/roles
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m4-r1-fixtures --role-atlas-fixtures
bash scripts/assemble_vercel.sh
uv lock --check --offline
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector/market/role_pipeline.py src/skills_vector/market/role_atlas.py scripts/check_role_atlas.py tests/test_role_atlas_pipeline.py tests/test_role_atlas.py
git diff --check
git diff HEAD --exit-code -- preview/release/ docs/role-atlas/STANDARDS.md
```

Final runs exited **0**. Contract: **37 tests OK**; pipeline: **14 tests OK**.
Full suite: **200 tests in 32.467s, OK (skipped=2)**, same two pre-existing skips.
All five generated files match the independent rebuild byte-for-byte; repeat
builds also match. Research REST/MCP parity and pinned citations pass.

Both browser selections passed **1280 light, 1280 dark, 390 light**, visiting
every role/tab plus Connections, with no console/page/overflow errors:

| Selection | Screenshots per case | Total | Errors |
| --- | ---: | ---: | --- |
| Research (four roles) | 24 | 72 | none |
| Fixtures (three roles) | 18 | 54 | none |

The checker uses explicit local Chromium
`~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`. Initial browser
checking exposed a local variable shadowing the new missing-comparison helper;
that was fixed before the successful full sweeps. Assembly: **551 files**; lock:
**61 packages resolved**; syntax/compilation/diff checks clean. A Git-blob hash
comparison also confirmed all **533** published files unchanged.

Opened `/tmp/role-atlas-m4-r1-research/app/1280-light/account-executive-map.png`:
confirmed every forecast cell is one-line “No forecast yet”, no forecast
percentage, the claim/reason are in the detail panel, and long evidence URLs wrap
inside its border. Both selections' `results.json` and screenshots are saved
under the paths in the commands above.

No STANDARDS edits, paid/model calls, secrets, original-release writes or M5 work.
Stop here for review.

## M5 — Official signals and forecast ledger

Read M4-r2 and the updated C4 before implementation. Both carry-overs are
preserved: **no posting-phrase normalization**, and research ledger calls enter
with **basis none**; only a local historical backtest can estimate numbers.
STANDARDS.md is unchanged in this milestone.

### Built (S1–S4)

- **S1:** Operator commands under the existing market CLI: `signals import`
  for Hot Technologies, Emerging Tasks, BLS occupation × industry and saved
  CareerOneStop certifications; `signals merge` for validated role/2 output.
  Inputs are local paths, never URLs fetched by the program. Tiny authored
  upstream-format samples live in `tests/samples/role-atlas/`, with explicit
  sample labels and a format README. O*NET TSV flags, native task dates, BLS
  CSV/scalar XLSX (including split headers/merged cells) and CareerOneStop
  CertList responses are covered. No dependency was added.
- **S2:** Imported signals retain publisher/source_kind, required operator
  retrieval timestamp, canonical timing/family/direction and source-file hash,
  scope and native values. The optional M1 `Signal.retrieved_at` extension is
  timezone/calendar-validated and omitted when unknown, preserving pre-M5 JSON
  round trips. Retrieval is not a source rating date. This narrow contract/model
  change is necessary to preserve S2 metadata in the merged record.
- Merge defaults to anchor context, never guessed skill equivalence. Skill
  bindings require rationale and qualitative exact/close/loose confidence;
  loose stays audit-only. BLS context cannot corroborate a skill rise; Hot
  Technology presence is not upward confirmation. The **unchanged** pure status
  engine recomputes bound skills. Every output passes `RoleRecord.from_dict`;
  counts, labels, edges, sector floors and null profiles stay intact. Sample
  bundles cannot merge into research. Output is deterministic, release-pinned,
  separately written, protected against input overlap/symlinks, and preflighted
  before writes. Native industry shares are not broad-sector aggregates;
  certification catalog membership is not an employer requirement.
- **S3:** `forecast-ledger create`, `backtest`, `resolve`, `score` and `show`.
  Stable ledger ids, source-role/release/digest, original forecast id, claim,
  probability/base rate, basis/reason, created/resolves/horizon/scored_against
  are retained. Create appends cohorts idempotently; issued sources/events,
  estimated probabilities and resolutions are immutable. Re-publication of
  the same call in another release is not an independent forecast.
- Events are explicit exact-claim operator bindings, not prose-derived numbers.
  Supported events test a recorded-scope share at/above or below a threshold.
  Resolution requires the exact four-quarter target, matching cadence, skill,
  levels/comparison_group and a nonempty denominator. Missing skills/scopes,
  unbound claims and wrong windows are deferred, not scored as failures. None
  forecasts can resolve but never contribute to Brier/calibration. Fixtures are
  also excluded from the research track record.
- Backtest uses a conservative **empirical historical event-rate baseline**.
  Only matured origin→12-month-target pairs strictly before creation are used;
  rolling-origin predictions use targets strictly before their own origin.
  At least two matured pairs and one causal scored prediction are required.
  Receipts retain archive/pair hashes and recomputable math. Future windows,
  fixture archives and duplicate comparable windows cannot fill gaps. An already
  resolved none call cannot be retrofitted with a probability.
- Hand-computed tests: Brier for p=.8/.4 and y=1/0 is **.1**; bin boundaries
  include 0, .1 and 1. Historical outcomes [0,1,1] yield baseline **2/3**, causal
  predictions [0,1/2] yield **5/8**, and a later positive outcome scores **1/9**.
  Monthly and quarterly cases agree. Corrupted receipts and look-ahead fail.
- **S4:** With no archive, the CLI and API report exactly **not run: no historical
  posting archive**, with a reason, null Brier, empty calibration and no estimated
  probabilities. Insufficient/incompatible supplied history has its own explicit
  not-run reason, rather than fabricated results.

### API / Method and actual current state

`GET /v1/track-record?role=...&release=...` and MCP `get_track_record` return
identical release-pinned payloads. Full-role envelopes include the same summary
outside the immutable role JSON. `--role-atlas-ledger` works with serve/MCP/checker;
`SKILLS_VECTOR_ROLE_ATLAS_LEDGER` supports deployment configuration. A ledger does
not enable a catalog, and corrupt/missing/wrong-kind explicit selections fail
closed. Counts verify the exact origin role/release/hash. No read writes files.

Method renders actual ledger counts, Brier or a dash, no-archive reason, and
calibration bars with midpoint ticks, observed frequency, mean probability and n.
Empty calibration is explicit. No prototype calibration numbers or lag are copied.
The frozen role's own forecast JSON is not silently rewritten by ledger updates.

Checked-in `research/role-atlas/m5/{ledger,backtest}.json` uses the approved M4
release `role_research_e647b3e2dce649d4`:

| Role | Tracked / pending none calls | Resolved / scored | Brier / calibration |
| --- | ---: | ---: | --- |
| Account Executive | 12 / 12 | 0 / 0 | null / [] |
| Forward Deployed Engineer | 11 / 11 | 0 / 0 | null / [] |
| Growth Manager | 5 / 5 | 0 / 0 | null / [] |
| HR Generalist | 10 / 10 | 0 / 0 | null / [] |
| **Total** | **38 / 38** | **0 / 0** | **null / []** |

No official downloaded files or historical archive were supplied. No sample
was promoted into research. The current single-future-posting skeleton claims
are not relabeled as window-share events; they remain unbound and pending.
The approved M4 files, posting-led phrases, 19 postings/15 unassigned, all status
floors and 80 null sector profiles are unchanged. The optional contract extension
changes future producer hashes, so README rebuild instructions now target a new
output directory rather than overwrite the approved M4 snapshot.

### Exact commands / results (G1–G5)

```sh
.venv/bin/skills-vector market forecast-ledger create --roles-root research/role-atlas/m4/roles --ledger research/role-atlas/m5/ledger.json > /tmp/role-atlas-m5-create.json
.venv/bin/skills-vector market forecast-ledger backtest --ledger research/role-atlas/m5/ledger.json --output research/role-atlas/m5/backtest.json
.venv/bin/skills-vector market forecast-ledger create --roles-root src/skills_vector/market/role_atlas_contract/fixtures --ledger /tmp/role-atlas-m5-fixture-ledger.json > /tmp/role-atlas-m5-fixture-create.json
.venv/bin/skills-vector market forecast-ledger create --roles-root research/role-atlas/m4/roles --ledger /tmp/role-atlas-m5-rebuild/ledger.json > /tmp/role-atlas-m5-rebuild-create.json
.venv/bin/skills-vector market forecast-ledger backtest --ledger /tmp/role-atlas-m5-rebuild/ledger.json --output /tmp/role-atlas-m5-rebuild/backtest.json > /tmp/role-atlas-m5-rebuild-backtest.json
diff -qr research/role-atlas/m5 /tmp/role-atlas-m5-rebuild
```

All exit **0**. Independent ledger/backtest files match byte-for-byte; no-archive
output is the status above, 0 estimates/scores, Brier null and bins []. Fixture
ledger tracks 9 illustrative calls, never research scores.

```sh
.venv/bin/skills-vector market signals import hot-technologies --input 'tests/samples/role-atlas/Technology Skills.sample.tsv' --retrieved-at 2026-09-01T12:00:00Z --sample --output /tmp/role-atlas-m5-hot.json > /tmp/role-atlas-m5-hot.log
.venv/bin/skills-vector market signals import emerging-tasks --input 'tests/samples/role-atlas/Emerging Tasks.sample.tsv' --retrieved-at 2026-09-01T12:00:00Z --sample --output /tmp/role-atlas-m5-tasks.json > /tmp/role-atlas-m5-tasks.log
.venv/bin/skills-vector market signals import bls-industry --input tests/samples/role-atlas/occupation.sample.csv --year 2024 --retrieved-at 2026-09-01T12:00:00Z --sample --output /tmp/role-atlas-m5-matrix.json > /tmp/role-atlas-m5-matrix.log
.venv/bin/skills-vector market signals import certifications --input tests/samples/role-atlas/certifications.sample.json --soc 13-1071.00 --retrieved-at 2026-09-01T12:00:00Z --sample --output /tmp/role-atlas-m5-certifications.json > /tmp/role-atlas-m5-certifications.log
.venv/bin/skills-vector market signals merge --roles-root src/skills_vector/market/role_atlas_contract/fixtures --bundle /tmp/role-atlas-m5-hot.json --bundle /tmp/role-atlas-m5-tasks.json --bundle /tmp/role-atlas-m5-matrix.json --bundle /tmp/role-atlas-m5-certifications.json --output /tmp/role-atlas-m5-sample-merged
```

All exit **0**: respectively 2/1/1/1 admitted sample signals; three schema-valid
fixture roles, release `role_signals_3881f0db7eae6ecd`. Only temporary fixture
output was written. Unit tests also prove byte-identical merge reruns/rebuilds,
exact/close promotion, loose non-promotion, presence not confirming rising,
malformed source/metadata rejection and source/output protection.

```sh
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_signals.py
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_ledger.py
.venv/bin/python -m unittest discover -s tests
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m5-research --role-atlas-root research/role-atlas/m4/roles --role-atlas-ledger research/role-atlas/m5/ledger.json
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m5-fixtures --role-atlas-fixtures --role-atlas-ledger /tmp/role-atlas-m5-fixture-ledger.json
bash scripts/assemble_vercel.sh
uv lock --check --offline
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector/market/role_artifacts.py src/skills_vector/market/role_signals.py src/skills_vector/market/role_ledger.py src/skills_vector/market/role_operations.py src/skills_vector/market/role_atlas.py scripts/check_role_atlas.py tests/test_role_atlas_signals.py tests/test_role_atlas_ledger.py
git diff --check
git diff HEAD --exit-code -- preview/release/ research/role-atlas/m4/ docs/role-atlas/STANDARDS.md src/skills_vector/market/role_atlas_contract/fixtures/
```

Final runs exit **0**. New focused suites: **10 signal tests**, **11 ledger tests**;
final full suite: **221 tests in 44.991s, OK (skipped=2)**, the same pre-existing
two skips. There are **21 new tests**, including source-format/schema negatives,
CLI lifecycle, both cadences, receipt/math corruption, source hash/kind errors,
REST/maintained-SDK-MCP parity and release pins. An initial pass exposed a stale
static HTML assertion and two test-helper mistakes (parent SOC matching and the
SDK's snake_case property); these were fixed before the final successful runs.
Extra merged-cell and empty-denominator cases were added before the final run;
the empty test initially violated M1's positive-share/employer rule, so the
synthetic empty measurement was corrected to zero share, not the rule relaxed.

Both browser selections visited every role/tab/Connections at **1280 light,
1280 dark and 390 light**, with explicit local Chromium 1243. Research: **72
screenshots** (24/case); fixtures: **54** (18/case); every `errors` array is empty.
The checker asserts ledger count/Brier/empty calibration against the pinned API,
including that fixtures cannot acquire research scores. Logs/results/screenshots
are under the paths in the commands above. Assembly: **551 files**. Offline lock:
**61 packages resolved**. Syntax/compilation/diff checks clean.

Additional scored-UI check used the explicitly synthetic test helpers (temporary
files only, not research evidence):

```sh
PYTHONPATH=tests:src .venv/bin/python - <<'PY'
from pathlib import Path
from test_role_atlas_ledger import sample_record,sample_event,archive_sample
from skills_vector.market.role_artifacts import canonical,write_json
from skills_vector.market.role_ledger import create_ledger,backtest_ledger,resolve_ledger
r=Path('/tmp/role-atlas-m5-synthetic-ui');r.mkdir(exist_ok=True)
a=r/'archive'
if not a.exists(): archive_sample(a)
roles=r/'roles';roles.mkdir(exist_ok=True)
origin=sample_record('2024-Q1',.1); later=sample_record('2025-Q1',.3)
(roles/'role.json').write_text(canonical(origin))
l=backtest_ledger(create_ledger([origin],sample_event(origin)),a)['ledger']
l=resolve_ledger(l,[later])['ledger']
write_json(r/'ledger.json',l)
print('Synthetic only: one resolved call, Brier 1/9; not research findings.')
PY
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m5-scored-browser --role-atlas-root /tmp/role-atlas-m5-synthetic-ui/roles --role-atlas-ledger /tmp/role-atlas-m5-synthetic-ui/ledger.json
```

Exit **0**, **18 screenshots**, all three cases, no errors; displayed Brier .111
and populated bin match the actual computed synthetic ledger. Also ran the checker
assertion after intentionally replacing the displayed Brier with “invented score”:
it rejected the corruption at all three cases for both real-empty and synthetic-
scored pages (six negative probes), then passed after restoration. Exact crop/
negative-probe command:

```sh
UV_OFFLINE=1 uv run --with playwright python - <<'PY'
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, 'scripts')
from check_role_atlas import local_server, CHROMIUM, assert_track_record, current_record
from playwright.sync_api import sync_playwright
out=Path('/tmp/role-atlas-m5-method');out.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=str(CHROMIUM),headless=True)
 for name,root,ledger in [('research','research/role-atlas/m4/roles','research/role-atlas/m5/ledger.json'),('synthetic','/tmp/role-atlas-m5-synthetic-ui/roles','/tmp/role-atlas-m5-synthetic-ui/ledger.json')]:
  args=SimpleNamespace(url=None,role_atlas_root=Path(root),role_atlas_fixtures=False,role_atlas_ledger=Path(ledger))
  with local_server(args) as base:
   for width,scheme in [(1280,'light'),(1280,'dark'),(390,'light')]:
    page=browser.new_page(viewport={'width':width,'height':900},color_scheme=scheme)
    page.goto(base+'/role-atlas.html',wait_until='networkidle')
    page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
    record=current_record(page)
    assert_track_record(page,base,'account-executive',record)
    before=page.locator('#ledgerBrier').inner_text()
    page.locator('#ledgerBrier').evaluate("node => node.textContent = 'invented score'")
    rejected=False
    try: assert_track_record(page,base,'account-executive',record)
    except AssertionError: rejected=True
    assert rejected,'checker did not reject a fabricated score'
    page.locator('#ledgerBrier').evaluate('(node,value) => node.textContent=value',before)
    assert_track_record(page,base,'account-executive',record)
    page.locator('#trackRecord').screenshot(path=str(out/f'{name}-{width}-{scheme}.png'))
    print(name,width,scheme,'Brier:',before,'negative fabricated-score probe rejected')
    page.close()
 browser.close()
PY
```

Opened Method crops under `/tmp/role-atlas-m5-method/`: research 1280 light and
390 light, synthetic 1280 dark and 390 light. Confirmed honest empty states,
visible no-archive reason, legible labels, fitting mobile panels, and numeric
calibration bars/midpoint ticks with mean p and n only in the synthetic scored
case. These are visual checks, not claims that research calibration exists.

A final Git-blob/file-set integrity check confirmed all **533** published files
unchanged, including current.json:

```sh
.venv/bin/python - <<'PY'
import hashlib,subprocess
from pathlib import Path
files=subprocess.check_output(['git','ls-files','-z','preview/release/']).decode().split('\0')[:-1]
for name in files:
 data=Path(name).read_bytes()
 blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
 expected=subprocess.check_output(['git','rev-parse','HEAD:'+name]).decode().strip()
 assert blob==expected,name
actual={str(p) for p in Path('preview/release').rglob('*') if p.is_file()}
assert actual==set(files),actual.symmetric_difference(files)
print(f'All {len(files)} published files and the file set unchanged against HEAD blobs.')
PY
```

The exact diff check above also confirms the
approved M4 research records, fixtures and STANDARDS unchanged. No paid/model
calls, downloads, secrets, immutable-release edits, new dependencies, deployment
or push. Operator formats/workflow and limitations are documented in
`docs/role-atlas/OFFICIAL_SIGNALS_AND_LEDGER.md` and the existing guide/READMEs.

### Remaining limitations / stop

No historical archive or real downloaded official files are present, so there
are deliberately **no research probabilities or scores**. Upstream layout variants
outside the documented local adapters may require a value-only local export;
none are silently guessed. The baseline is simple and unconditioned, with thin
counts visible in receipts. Operators must review event semantics and preserve
comparable samples. Ledger mutation is single-writer. No raw phrase normalization
or new status/floor policy was introduced. M5 is complete for review; commit this
milestone on `feat/role-atlas` and stop.

## M6 — Posting volume, no-model extraction lane

Read M5-r1, updated BRIEF (including M6 and settled quarterly windows), STANDARDS,
and the approved source/design vocabulary. No live job-board retrieval was run.

### First: the three M5-r1 carry-overs

1. **Decisions removed.** No “For your review”, “Still open” or Decisions section
   remains in the product; its unused CSS was also removed. Method states the settled quarterly policy and
   four-quarter horizon, edge/sector thresholds, level-pip threshold and linked
   management track; status thresholds remain in the Method rules table.
2. **Canonical source-timing table.** Full-role API/MCP envelopes now carry
   `method`, generated from every `SOURCE_RULES` kind with canonical timing,
   family and purpose. Method renders all **11** kinds, independent of role or
   selected skill, with an explicit policy/not-import-coverage disclaimer.
   Per-skill readings and rating years stay in their detail panels. Fixtures
   and research have the same canonical policy. No role/2 evidence is invented.
3. **Plurals fixed.** Shared count nouns cover role heads, level evidence,
   industry evidence and Method summaries. Legacy generated posting summaries
   display “1 employer”; raw role JSON/quotes are not rewritten. Tests exercise
   singular/plural, the 11-employer boundary, and leaving official source text
   alone. Browser checks/crops confirmed “1 employer” on the research AE detail.

### Built — V1–V4, P1–P5

- New `market posting-volume capture|project` operator commands. Capture reuses
  the existing public board parsers, US geography rule, dedup key and redirect
  allowlist. Greenhouse entity-escaped HTML, Lever structured lists and Ashby
  HTML/plain descriptions are exercised in checked-in native-format samples.
  The other existing parsers remain available; incomplete/paginated lists are
  excluded with coverage errors instead of silently becoming complete samples.
- **V1:** No model/provider/agent/research-budget imports on this lane. A fresh
  subprocess import guard exercises both capture and project through the real
  `skills-vector market` entrypoint, asserts no banned modules loaded, and
  proves its rejection guard. Narrow supporting change: legacy imports in
  `skills_vector/cli.py` are lazy, after market dispatch. Legacy model-backed
  behavior is tested unchanged; no budget or research-config file was edited.
- Independent `POSTING_VOLUME_LIMITS` appended in `market/limits.py`: max **500**
  admitted postings/role, 100 boards, 200 logical retrievals, 16 MiB/response,
  20-second transport timeout and at most two infrastructure retries. The
  original **RESEARCH_LIMITS AST is identical to HEAD**, including model-call
  and posting ceilings. No paid search, model service or new host was added.
- `rules/1` splits responsibilities, requirements and preferred sections from
  headings/list structure, dropping company/benefits/legal/unknown sections
  and recognizable boilerplate. Inline tags/entities are decoded, not
  paraphrased. Paragraphs/line breaks within a bullet remain one quote.
  Every posting records `extraction_method: rules/1`.
- **V3:** Exact quote/start/end/source-hash spans in the saved visible
  description; original native description and board bytes are separately
  hashed. Projection verifies every indexed file, binds each description back
  to its native row, replays extraction, and rejects rehashed forged quotes.
  Visible text is not falsely claimed to include literal HTML markup/entities.
- Dedup precedes deterministic round-robin allocation. The largest feasible
  sample has **no employer above 20%** and never exceeds policy/hard ceilings.
  Fewer than five employers cannot form a nonempty sample: output is zero,
  not padding. Employer identities/aliases are explicitly operator-declared.
  Coverage diagnostics include eligible postings whose sections were missed,
  not just successful extractions.
- Output: immutable per-quarter directories with manifest, file hashes, raw
  board responses, native/visible descriptions and posting provenance. Output
  cannot overlap inputs, fixtures or the published release tree. Raw real
  corpora are external operator evidence, not public preview exports.
- **P1/P2:** M4 `EvidenceInputs.from_snapshots` accepts one or more quarterly
  captures plus the existing published foundation and checked-in mappings.
  Snapshots are the additional input explicitly authorized by M6. Native
  bodies, policy, mapping and recipe hashes determine output; no output path
  or processing clock enters deterministic offline identity. Duplicate
  quarters are rejected. Comparison groups include stable frame/policy,
  complete-board set, extraction/normalization implementation and mappings;
  changed frames cannot manufacture a trend. Empty samples are not zeros in
  historical measurements. Current role/2 output retains comparable older
  window measurements and an honest compared_to pin.
- **P3:** All responsibility spans reach the existing ownership/scope rules.
  Qualifications do not assign current levels. The patterns, management and
  conflict rules are unchanged. Search whitespace alone is normalized while
  original strong-signal quotes are retained. Titles/years remain weak.
- Rules-lane-only `phrase-rules/1` lowercases and removes explicit experience/
  ability/knowledge prefixes and plus/preferred suffixes. Original quote,
  source, expectation id and normalized label remain traceable in audit links.
  No synonym model, stemming or negation removal. Ambiguous/loose lexical
  alignments cannot gain an O-link. Existing approved M4 labels are unchanged.
- **P4/P5:** All status/edge/sector functions and floors are reused, not loosened.
  Employer sectors default to explicit unverified/null rows with source notes;
  reviewed primary-employer maps require local employer-bound evidence. No
  customer/job-function sector inference. Every output passes M1 validation.
  Fixture snapshots produce fixture roles; the research-shaped branch is also
  tested in memory. Two quarters do not supply a four-quarter forecast
  backtest: no numeric forecast, score or false upward O/M signal is created.

### Sample results — not research findings

Six checked-in native payloads contain **48 authored postings**, 12 per role.
**V4: 42/48 (87.5%)** have responsibilities. The six intentional failures retain
requirements/preferred evidence but lack responsibilities:

| Role | Employer | Native posting id |
| --- | --- | --- |
| Account Executive | Sample employer 1 | `100001` |
| Account Executive | Sample employer 5 | `sample-employer-5-account-executive-1` |
| Forward Deployed Engineer | Sample employer 2 | `sample-employer-2-forward-deployed-engineer-1` |
| Forward Deployed Engineer | Sample employer 6 | `sample-employer-6-forward-deployed-engineer-1` |
| Growth Manager | Sample employer 3 | `sample-employer-3-growth-manager-1` |
| HR Generalist | Sample employer 4 | `100307` |

One additional HR posting, `sample-employer-6-hr-generalist-0`, deliberately
contains conflicting L2/L4 ownership. Thus **41/48** get a scope assignment;
**7** stay unassigned. This is constructed sample coverage, not an accuracy
estimate or a claim about live job boards.

| Role | L1/L2/L3/L4/L5/unassigned | Early skills | Profiles |
| --- | --- | ---: | --- |
| Account Executive | 0/6/4/0/0/2 | 7 | 0/20 |
| Forward Deployed Engineer | 0/6/4/0/0/2 | 7 | 0/20 |
| Growth Manager | 0/6/5/0/0/1 | 6 | 0/20 |
| HR Generalist | 0/5/5/0/0/2 | 8 | 0/20 |

Both sample quarters have six employers/role (max contribution 2/12), below
core's ten-employer floor. All 28 skills correctly remain early, no edges
survive, and all 80 profiles stay null. Projection id:
**`role_fixture_50fdfb7ce3a75792`**.

**V2 scale case:** deterministic expansion of those native templates to ten
sample employers × thirty distinct posting ids per role gives **300/role**,
**1,200/quarter**. Every employer contributes exactly **30 (10%)**, below 20%.
Two physical current-quarter trees match byte-for-byte. The two-quarter
projection has 5/5/4/5 core skills by role and one additional early HR skill;
this synthetic case satisfies the unchanged floors. There are no rising
skills or verified sector profiles, and these are not market findings.

### Exact verification commands / results

```sh
.venv/bin/skills-vector market posting-volume capture --policy tests/samples/posting-volume/policy.sample.json --quarter 2026-Q3 --payload-root tests/samples/posting-volume --retrieved-at 2026-09-01T00:00:00Z --output /tmp/role-atlas-m6-final-snapshots > /tmp/role-atlas-m6-final-q3.json
.venv/bin/skills-vector market posting-volume capture --policy tests/samples/posting-volume/policy.sample.json --quarter 2026-Q4 --payload-root tests/samples/posting-volume --retrieved-at 2026-10-15T00:00:00Z --output /tmp/role-atlas-m6-final-snapshots > /tmp/role-atlas-m6-final-q4.json
.venv/bin/skills-vector market posting-volume project --snapshot /tmp/role-atlas-m6-final-snapshots/2026-Q3 --snapshot /tmp/role-atlas-m6-final-snapshots/2026-Q4 --output /tmp/role-atlas-m6-final-projection > /tmp/role-atlas-m6-final-projection.json
.venv/bin/skills-vector market posting-volume project --snapshot /tmp/role-atlas-m6-final-snapshots/2026-Q3 --snapshot /tmp/role-atlas-m6-final-snapshots/2026-Q4 --output /tmp/role-atlas-m6-final-projection-rebuild > /tmp/role-atlas-m6-final-projection-rebuild.json
diff -qr /tmp/role-atlas-m6-final-projection /tmp/role-atlas-m6-final-projection-rebuild
.venv/bin/python -m unittest discover -s tests -p test_posting_volume.py
.venv/bin/python -m unittest discover -s tests
.venv/bin/skills-vector market forecast-ledger create --roles-root src/skills_vector/market/role_atlas_contract/fixtures --ledger /tmp/role-atlas-m6-fixture-ledger.json > /tmp/role-atlas-m6-fixture-ledger-summary.json
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m6-research --role-atlas-root research/role-atlas/m4/roles --role-atlas-ledger research/role-atlas/m5/ledger.json
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m6-fixtures --role-atlas-fixtures --role-atlas-ledger /tmp/role-atlas-m6-fixture-ledger.json
bash scripts/assemble_vercel.sh
uv lock --check --offline
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector/cli.py src/skills_vector/market/posting_volume.py src/skills_vector/market/posting_volume_inputs.py src/skills_vector/market/posting_volume_cli.py src/skills_vector/market/role_method.py src/skills_vector/market/role_pipeline.py tests/test_posting_volume.py scripts/check_role_atlas.py
git diff --check
git diff HEAD --exit-code -- preview/release/ research/role-atlas/m4/ research/role-atlas/m5/ src/skills_vector/market/budgeting.py src/skills_vector/market/research_config.py src/skills_vector/budget.py uv.lock docs/role-atlas/BRIEF.md docs/role-atlas/STANDARDS.md
```

Final verification exits **0**. Full suite: **237 tests in 145.727s, OK (skipped=2)**
(final run concurrent with both browser sweeps; the preceding standalone run
was 64.954s), the same two pre-existing skips; **16 new tests**, no new dependency. Focused lane
suite passes (also included in the final full run). Initial checks caught a
stale expected evidence phrase, two sample-test setup mistakes (protected output
placement and unassigned total), and a double-escaped test newline. They were
fixed without relaxing guards/rules before the final successful run.

Both browser selections pass **1280 light, 1280 dark, 390 light**, all roles,
tabs and Connections; explicit local Chromium 1243, no external requests.
Research: **72 screenshots**, fixtures: **54**, every `errors` array empty.
The checker now asserts all canonical source rows against the API and rejects
stale Decisions scaffolding; rating dates remain checked in head/detail rather
than incorrectly in the canonical table. Assembly **551 files**; offline lock
**61 packages resolved**. Syntax/compilation/diff checks pass.

The scale case is reproducible without a board request:

```sh
PYTHONPATH=tests:src .venv/bin/python - <<'PY'
from pathlib import Path
from test_posting_volume import stress_policy
from skills_vector.market.posting_volume import write_snapshot
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.role_artifacts import read_json
r=Path('/tmp/role-atlas-m6-300');p=r/'payloads';p.mkdir(parents=True,exist_ok=True)
policy=stress_policy(p)
for q,stamp in [('2026-Q3','2026-09-01T00:00:00Z'),('2026-Q4','2026-10-15T00:00:00Z')]:
 write_snapshot(policy,r/'snapshots',q,payload_root=p,retrieved_at=stamp)
write_snapshot(policy,r/'rebuild','2026-Q4',payload_root=p,retrieved_at='2026-10-15T00:00:00Z')
a,b=r/'snapshots/2026-Q4',r/'rebuild/2026-Q4'
assert {str(x.relative_to(a)):x.read_bytes() for x in a.rglob('*') if x.is_file()}=={str(x.relative_to(b)):x.read_bytes() for x in b.rglob('*') if x.is_file()}
inputs=EvidenceInputs.from_snapshots([r/'snapshots/2026-Q3',r/'snapshots/2026-Q4'],Path('preview/release'))
audit=inputs.write(r/'projection')
for role,data in read_json(a/'manifest.json')['roles'].items():
 print(role,'admitted',data['admitted'],'employers',len(data['employers']),'max employer',max(data['employers'].values()),'share',max(data['employers'].values())/data['admitted'],'statuses',audit['roles'][role]['skills_by_status'])
print('1200 fixture postings per quarter; physical snapshot rebuild byte-identical.')
PY
```

Exit **0**, actual counts/shares/statuses as above. Integrity proof:

```sh
.venv/bin/python - <<'PY'
import ast,hashlib,subprocess
from pathlib import Path
p='src/skills_vector/market/limits.py'
def limits(text):
 return next(ast.dump(n.value,include_attributes=False) for n in ast.parse(text).body if isinstance(n,ast.AnnAssign) and getattr(n.target,'id',None)=='RESEARCH_LIMITS')
assert limits(Path(p).read_text())==limits(subprocess.check_output(['git','show','HEAD:'+p]).decode())
files=subprocess.check_output(['git','ls-files','-z','preview/release/']).decode().split('\0')[:-1]
for name in files:
 data=Path(name).read_bytes();actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
 assert actual==subprocess.check_output(['git','rev-parse','HEAD:'+name]).decode().strip(),name
assert {str(p) for p in Path('preview/release').rglob('*') if p.is_file()}==set(files)
print('RESEARCH_LIMITS unchanged; all',len(files),'published files/file set unchanged.')
PY
```

Exit **0**: original limits unchanged; all **533** published files/file set
unchanged. Approved M4/M5 data, model budget/config files, lock and standards
are unchanged. The new operator policy is separate from model-backed policies.

Also captured `/tmp/role-atlas-m6-method/` crops at all three cases, asserting
11 canonical kinds, quarterly Method, no Decisions, and “1 employer” (not
“1 employers”) on AE. Opened source table 1280 light/dark and AE detail 390 light:
confirmed canonical purposes, correct timing/families, fitting/wrapping and an
honest no-forecast state. Full screenshots/results remain under the checker
paths above. These are UI checks, not newly retrieved research evidence.

### Exact live operator command — documented only, NOT executed

Starter roster and its source notes are checked in at
`config/role-atlas-posting-volume.json`. Detailed workflow, formats, quoting,
private raw evidence handling and limitations: `docs/role-atlas/POSTING_VOLUME.md`.
The reviewer/user reviews that public employer roster and runs:

```sh
QUARTER=$(.venv/bin/python -c 'from datetime import datetime,UTC; d=datetime.now(UTC); print(f"{d.year}-Q{(d.month-1)//3+1}")')
CAPTURE="$HOME/.local/share/skills-vector-evidence/role-atlas/capture-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/skills-vector market posting-volume capture --policy config/role-atlas-posting-volume.json --quarter "$QUARTER" --live --output "$CAPTURE"
```

Pi did **not** run this or any live job-board retrieval. No paid/model calls,
secrets, immutable release edits, push or deployment. No real new research
corpus is claimed: only labeled samples were generated here. The 13-employer
starter frame is biased and may not yield five eligible employers for every
role; it needs operator review/expansion, not a relaxed cap. Rules are deliberately
conservative and may miss unfamiliar headings. Employer aliases and primary
sector evidence still require operator curation. Quarterly captures are stock
observations, not counts of new hires/jobs. No forecast archive or calibration
was fabricated. M6 is complete for review; commit on `feat/role-atlas` and stop.


## M7 — real-data quality, implemented; Q3 honest stop

Built against BRIEF M7 and the approved M6 review (`reviews/M6-r1.md`).
Read-only diagnostic inputs were the 2026-Q4 capture and local O*NET 31.0
specified in BRIEF. **No live retrieval/probe, paid/model call, secret access,
push or deployment occurred.** No raw board payload or whole real posting was
copied into the repository. Checked-in audit excerpts are limited to one source
sentence per case; the native corpus remains outside the repo.

### Implementation and boundaries

- `posting_sections.py`: generic structural `rules/2` detection for h-tags,
  bold/strong paragraphs, short colon headings, heading variants and explicit
  current-duty prose. Company/benefits/legal/unknown bold boundaries and observed
  non-duty paragraphs are excluded. Bare unheaded lists remain unknown.
- Capture still uses the unchanged independent posting-lane ceilings and existing
  parsers/allowlist. Original `rules/1` extraction remains replayable. Projection
  validates the original manifest, indexed files, native rows, saved text and
  quote offsets **before** any offline upgrade.
- Offline upgrade reuses saved complete boards in memory and freezes the original
  admission IDs. It does not resample the newly extractable candidates. Every
  upgraded quote is still an exact saved-description substring.
- `onet_backbone.py`, `dwa_rules.json`, `onet31_lookup.json`: deterministic
  lexical grouping onto official DWA/software element IDs. The derived subset
  contains 94 role-scoped DWA records and 76 software categories, with native
  file hashes, source ZIP hash and CC BY 4.0 attribution. It is not the database.
  Global lexical DWA candidates outside anchor-SOC task membership are explicitly
  distinguished; no unsupported occupation equivalence is asserted.
- Matches retain method, matched text, qualitative confidence and source version.
  Only exact/close matches group; unmatched spans retain phrase-rules/1 labels
  and original quotes. Both DWA and Software Skills taxonomy use standing O /
  lagging presence under the existing durable O*NET source policy, not Hot
  Technologies M evidence. A software example alone does not assert the Hot
  Technology flag. Taxonomy-presence evidence is not upward confirmation.
  Processing/section/level/grouping recipes are part of comparability identities.
- The M5 importer now accepts 31.0 `Workplace Example`, `Element ID` and
  `Element Name` columns. Tiny authored sample added; no official signal bundle
  was silently imported/merged into research.
- Expanded AE scope rules apply to **rules/2 evidence only**. Titles and tenure
  remain weak, qualifications are not current ownership, and strong conflicts
  remain unassigned. Approved M4 inputs/records are not reclassified by this lane.
- `posting-volume roster-probe --candidates` supports saved samples or explicit
  operator `--live`, existing Greenhouse/Lever/Ashby hosts, bounded retrieval,
  existence/unknown states, native/observed totals, title counts, response hashes
  and source notes. Output is a draft (`reviewed: false`), never an approved frame.
  No live probe was run.
- UI/checker support genuinely empty admitted roles without treating zero
  admission as zero market demand. Nonempty roles still get every skill/detail,
  graph/focus and overflow check. Escaping probes tolerate empty role payloads.
  Display percentages round to one decimal; exact JSON evidence is unchanged.
- Operator/reproduction details: `REAL_DATA_QUALITY.md`; frozen review cases:
  `audits/M7-{sections,matches,levels}.json`. These are coding-agent manual
  judgments, **not independent expert ground truth or population accuracy**.

### Q1 — real responsibilities detection

The captured ATS is Greenhouse only. Lever/Ashby have offline native-format
tests, not an invented live accuracy result.

| ATS | Eligible | Before rules/1 | After rules/2 |
| --- | ---: | ---: | ---: |
| Greenhouse | 405 | 116 (28.6%) | 405 (100%) |

| Role | Before | After | Original admitted cohort |
| --- | ---: | ---: | ---: |
| AE | 101/333 | 333/333 | 70 |
| FDE | 8/62 | 62/62 | 0 |
| Growth Manager | 7/8 | 8/8 | 0 |
| HR Generalist | 0/2 | 2/2 | 0 |

The **232 AE failures** were recovered under these primary structures:
198 bold-paragraph headings, 31 h-tag/new-vocabulary headings, two plain
paragraph/colon headings and one explicit-duty/no-heading paragraph. The
**54 FDE failures**: nine bold paragraphs, one h-tag/new vocabulary and 44
plain paragraph/colon headings. Growth's one miss was explicit-duty prose;
HR's two were one bold paragraph and one h-tag/new vocabulary. These are the
first recovered responsibility section's structure, not independent causal
attributions: a posting can combine markup and vocabulary differences.

Seed `20261023`, sorted native job IDs, 20 random successes: **0 wrong spans
in the final reviewed sample**. Cases pin IDs, a source excerpt and judgments.
During development, a nested duty-category label and an in-office culture
paragraph were detected and excluded; this is a rechecked development audit,
not held-out validation. Detection means a responsibility section exists,
not that every task sentence has been recovered or semantically validated.

### Q2 — grouping and audit

Frozen AE cohort: **403 nodes before**, **391 after structural re-extraction
without grouping**, **284 after grouping**. Total reduction **119 (29.5%)**;
grouping alone removes 107 from the re-extracted node count. Of 284 final
nodes, 18 are O*NET 31 backbone-linked and 266 remain posting-led.

Seed `3100`, unique posting/expectation spans sorted by job ID and quote,
30 random matched spans: **29/30 judged correct (96.7%)**. The visible wrong/
borderline case is the adjectival relationship-focused enthusiasm span mapped
to developing business relationships; it remains recorded as an audit error,
not relabeled to inflate accuracy. Confidence is a lexical rule label, not a
calibrated correctness probability. Earlier development probes also caught
generic “database” career text and LinkedIn SalesNavigator being mistaken for
a generic software example; those aliases were restricted and negatively tested.

Requested top 25 grouped nodes: only **18** exist, so all 18 are reported rather
than padding seven. Shares use the frozen 70-posting role-wide denominator;
these are observed mentions, not proficiency or hiring counts.

| Rank | Backbone ID | Label | Posting share | Employers |
| ---: | --- | --- | ---: | ---: |
| 1 | `dwa.4.A.4.c.1.c.4` | Execute sales or other financial transactions. | 80.0% | 6 |
| 2 | `dwa.4.A.1.b.1.b.2` | Identify potential customers. | 72.9% | 6 |
| 3 | `dwa.4.A.4.a.4.a.2` | Develop business relationships. | 62.9% | 6 |
| 4 | `dwa.4.A.1.a.2.c.3` | Monitor sales activities. | 42.9% | 5 |
| 5 | `dwa.4.A.2.b.3.a.22` | Study product information to acquire professional knowledge. | 42.9% | 6 |
| 6 | `software.2.E.6.c` | Customer relationship management CRM software | 38.6% | 4 |
| 7 | `dwa.4.A.4.a.2.c.1` | Communicate with other workers to coordinate activities. | 30.0% | 5 |
| 8 | `dwa.4.A.4.a.1.a.5` | Explain technical product or service information to customers. | 20.0% | 3 |
| 9 | `dwa.4.A.2.b.2.h.2` | Develop financial or business plans. | 17.1% | 2 |
| 10 | `dwa.4.A.4.a.2.e.4` | Share sales-related or market information with colleagues. | 15.7% | 1 |
| 11 | `dwa.4.A.4.a.7.b.7` | Negotiate prices or other sales terms. | 15.7% | 3 |
| 12 | `software.2.E.1.l` | Sales and marketing software | 11.4% | 1 |
| 13 | `dwa.4.A.1.a.1.n.2` | Gather customer or product information to determine customer needs. | 5.7% | 3 |
| 14 | `dwa.4.A.4.b.4.j.5` | Coordinate sales campaigns. | 5.7% | 1 |
| 15 | `dwa.4.A.3.b.6.g.1` | Develop proposals for current or prospective customers. | 4.3% | 2 |
| 16 | `dwa.4.A.4.a.6.c.2` | Demonstrate products to consumers. | 4.3% | 1 |
| 17 | `dwa.4.A.1.a.2.h.1` | Monitor market conditions or trends. | 1.4% | 1 |
| 18 | `software.2.E.4.g` | Web page creation and editing software | 1.4% | 1 |

### Q3 — assignment improvement, **honest stop**

Original generic triage on original rules/1 spans reproduces **3 L4 / 67
unassigned**. All 67 lacked a matching current scope/ownership rule. Applying
the expanded AE diagnostic policy to those same 67 original cases categorizes
them as: **34 newly recognized current-scope cases**, **28 still without a
current ownership/scope signal**, **5 conflicting strong-scope cases**. This
separates missing patterns from missing evidence and conflict; it is not a
claim that all 67 should be assigned.

Final rules/2 output: **54/70 assigned (77.1%)**: L2=52, L4=2; L1/L3/L5=0.
The remaining 16 are eight strong conflicts and eight without current scope.

The 40 checked-in AE labels (seed `20261024`, sorted admitted native IDs) give
**33/40 agreement (82.5%)**, below the 85% target. Seven disagreements are
manual L3 labels: six have conflicting strong signals and one has complex-task
wording without an independently clear ownership boundary. I have not overridden
conflicts, treated title/tenure as scope, or added a complex-task-only rule to
get one more agreement. No safe P3-preserving change to reach 85% was established;
**Q3 takes the authorized honest-stop option**, not a target-pass claim.
Independent rubric/label review is the next step, not label tuning.

### Q4, floors and resulting research artifact

Two fresh physical projections, including audit files, are byte-identical:
`/tmp/role-atlas-m7-reviewed-release` and `/tmp/role-atlas-m7-reviewed-release-rebuild`.
Release: **`role_research_37b69249638d55ae`**. Select its `roles/` directory.

The original 70 AE postings/7 employers are retained, largest employer 20%.
Other roles honestly remain empty under the frozen admission cohort; Q1
eligible counts above are **not admitted volume**. All 284 skills are early;
zero core/rising/fading, zero kept edges (2,842 dropped), 80 null sector profiles.
All employer sectors remain unverified. No probability, calibration, historical
window, ledger score or employer-sector evidence was manufactured. Every role
record passes M1 validation.

### Exact verification commands and results

Final full suite (12 new M7 tests; existing V1 test expanded to all three CLI
operations and blocks network connection/DNS attempts in a fresh subprocess):

```sh
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_m7.py > /tmp/role-atlas-m7-new-tests-reviewed.log 2>&1
.venv/bin/python -m unittest discover -s tests -p test_posting_volume.py > /tmp/role-atlas-m7-posting-verified.log 2>&1
.venv/bin/python -m unittest discover -s tests > /tmp/role-atlas-m7-reviewed-tests.log 2>&1
```

Focused: 12 tests/1.258s and 16 tests/19.773s, OK. Final full:
**249 tests in 202.193s, OK (skipped=2)**, concurrent with all browser sweeps;
the same two pre-existing skips, no new skips. Tests are offline. Every added
DWA rule has positive and rejected synthetic cases; tests cover structural
boundaries, legacy integrity before upgrade, frozen cohort, loose nonpromotion,
recipe identity, native 31.0 headers, scope/conflicts, roster failures and
display-only percentage rounding.

```sh
.venv/bin/skills-vector market posting-volume project --snapshot /home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4 --output /tmp/role-atlas-m7-reviewed-release > /tmp/role-atlas-m7-reviewed-release.json
.venv/bin/skills-vector market posting-volume project --snapshot /home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4 --output /tmp/role-atlas-m7-reviewed-release-rebuild > /tmp/role-atlas-m7-reviewed-release-rebuild.json
diff -qr /tmp/role-atlas-m7-reviewed-release /tmp/role-atlas-m7-reviewed-release-rebuild
.venv/bin/python scripts/audit_role_atlas_m7.py --snapshot /home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4 --output /tmp/role-atlas-m7-quality-reviewed.json > /tmp/role-atlas-m7-quality-reviewed.log
```

All exit 0; diff emits nothing. Quality JSON reproduces tables, selection/source
checks, agreement and `honest_stop: true`.

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from skills_vector.market.onet_backbone import derive
print(derive(Path('/home/logani/.local/share/skills-vector-evidence/onet/db_31_0'),
             Path('src/skills_vector/market/role_atlas_mappings/skills.json'),
             Path('/tmp/role-atlas-m7-lookup-verification.json')))
PY
cmp /tmp/role-atlas-m7-lookup-verification.json src/skills_vector/market/role_atlas_mappings/onet31_lookup.json
```

Exit 0; 94 DWAs, 76 categories, byte-identical derived lookup; SHA-256
`91d82384227b8908c4bef8af7b38237409701224efc87bf27e431446a38201e3`.
The source ZIP hash is the supplied/recorded
`6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9`;
native table file hashes were computed locally. The database was not copied.

```sh
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m7-reviewed-fixtures --role-atlas-fixtures --role-atlas-ledger /tmp/role-atlas-m6-fixture-ledger.json > /tmp/role-atlas-m7-reviewed-fixtures.log 2>&1
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m7-reviewed-research --role-atlas-root research/role-atlas/m4/roles --role-atlas-ledger research/role-atlas/m5/ledger.json > /tmp/role-atlas-m7-reviewed-research.log 2>&1
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m7-reviewed-live --role-atlas-root /tmp/role-atlas-m7-reviewed-release/roles > /tmp/role-atlas-m7-reviewed-live.log 2>&1
UV_OFFLINE=1 uv run --with playwright python /tmp/role-atlas-m7-visual.py > /tmp/role-atlas-m7-visual-reviewed.log 2>&1
```

All exit 0, using explicit Chromium
`~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`. Each selection
runs **1280 light, 1280 dark, 390 light**, every role/tab/Connections and negative
probes: fixtures **54**, approved research **72**, live projection **72**
screenshots; every `errors: []`. The fixture ledger is the unchanged M6
temporary fixture ledger (creation recipe in M6 above), not a research ledger.
Scratch live root uses no mismatched old ledger. Viewport screenshots under
`/tmp/role-atlas-m7-visual` were opened: live desktop list and dark graph,
mobile CRM detail and empty FDE state. Checked wrapping, bounded percentage
labels, source attribution, no-forecast treatment and explicit empty admission.

```sh
UV_OFFLINE=1 uv lock --check --offline
bash scripts/assemble_vercel.sh
git diff --check
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector scripts/audit_role_atlas_m7.py scripts/check_role_atlas.py
```

All exit 0: 61 locked packages, 551 assembled files in the script's fixed
`.vercel-out` directory. No deployment. An earlier invocation passed an unused
output argument; the script still used that same fixed directory.

Read-only/protected-input verification was run before and after work: the
334-file receipt is `/tmp/role-atlas-m7-input-receipts.json`. Final comparison
also verifies the exact file set, not only hashes. 334 external input files,
543 protected tracked files and all 533 published files/file set are unchanged.
The protected tracked set includes M4/M5 artifacts, limits, tracked budgets and
research configuration. STANDARDS/BRIEF/design were not edited by this build.
Exact final integrity check (exit 0):

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib,json,subprocess
root=Path.cwd();receipt=json.loads(Path('/tmp/role-atlas-m7-input-receipts.json').read_text())
inputs=[Path('/home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4'),Path('/home/logani/.local/share/skills-vector-evidence/onet/db_31_0')]
assert {str(p) for r in inputs for p in r.rglob('*') if p.is_file()}==set(receipt)
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in receipt.items())
tracked=subprocess.check_output(['git','ls-files'],text=True).splitlines()
protected=[p for p in tracked if p.startswith(('preview/release/','research/role-atlas/m4/','research/role-atlas/m5/')) or p=='src/skills_vector/market/limits.py' or 'budget' in p or ('research' in p and p.startswith('config/'))]
for p in protected:assert (root/p).read_bytes()==subprocess.check_output(['git','show','HEAD:'+p]),p
pub={p for p in tracked if p.startswith('preview/release/')}
assert {str(p.relative_to(root)) for p in (root/'preview/release').rglob('*') if p.is_file()}==pub
print('Read-only input files:',len(receipt),'unchanged; protected tracked files:',len(protected),'unchanged; published files:',len(pub),'unchanged')
PY
```

Offline operator smoke (exit 0, six boards exist, all drafts unreviewed):

```sh
.venv/bin/skills-vector market posting-volume roster-probe --candidates tests/samples/posting-volume/policy.sample.json --payload-root tests/samples/posting-volume --output /tmp/role-atlas-m7-roster-draft.json > /tmp/role-atlas-m7-roster-summary.json
```


### Development failures, corrections, deviations and open risks

- An initial scratch projection accidentally resampled the expanded candidate
  pool (182 AE). It was discarded. Frozen admission was implemented and all
  reported final comparisons use the original 70; no evidence from that initial
  projection is presented as Q4 proof.
- The first full run (247 tests) exposed two old M4 level regressions. Expanded AE
  scope was isolated to rules/2 evidence, and existing expected research
  denominators/unknown levels pass unchanged.
- The first live browser sweep saved all 72 screenshots but failed in an XSS
  negative probe that assumed every role had a first skill. The probe now mutates
  only existing skills; full final sweeps, including the empty roles, pass.
- A strengthened fresh-process test initially replaced the socket class and broke
  stdlib SSL import rather than testing networking. It now blocks connection and
  DNS entrypoints without replacing the class. Capture/project/roster all pass
  the import/network guard. The final 249-test run above supersedes those failures.
- Visual review found a repeating decimal percentage wrapping in the live detail.
  The one-decimal display formatter and regression test fix it; raw evidence
  numbers and physical projection identity are unchanged.
- Final provenance review caught software-taxonomy presence being labeled with
  the Hot Technologies market kind. It now uses standing official O / lagging
  presence, like durable task anchors, with an explicit no-Hot-flag reading and
  regression test. The genuine Hot Technologies importer still requires Y.
  Fresh physical projections and all final gates above were rerun after this fix.
- Q3 is the explicit deviation/authorized honest stop: 82.5% agreement is not
  85%. The data and labels are retained for independent review, not coerced.
- Q1 is this Greenhouse capture only; audits are small, development-set,
  agent-authored checks. Broad heading variants can still miss or include
  unfamiliar structures. Mixed paragraphs are not guaranteed atomic tasks.
- Lexical grouping is incomplete and the audit records a real borderline error;
  vendor/category ambiguity and semantic equivalence need further review.
  There are only 18 grouped nodes, not 25. No embeddings/model fallback was used.
- The 13-board starter roster remains biased. Empty non-AE cohorts require
  operator-reviewed frame expansion, not relaxed caps or backfilled admission.
  Employer aliases/primary sectors still require curation. One quarterly listing
  stock is not new hiring, a trend, or a forecast archive.

M7 is implemented for review with Q1/Q2/Q4 passing and Q3 explicitly stopped at
the honest result. Commit on `feat/role-atlas`, then stop.


## M7 review round 1 replies

Addressed `reviews/M7-r1.md`: **must 1 and both shoulds**. No live retrieval,
paid/model call, secret access, push, deployment or edits to immutable inputs.
All projections below use the original read-only Q4 capture and frozen
70-posting AE cohort, not a new sample.

### Must 1 — discriminating scope, reviewed AE segments and label recheck

Removed the M7 sales-cycle/book/quota/pipeline and complex-selling/GTM heuristics.
Generic full-cycle selling, quota attainment, pipeline management, CRM hygiene,
prospecting and generic strategy work cannot assign an AE level. The reviewer
quote (“Manage the complete sales cycle, from initial contact to close, …
quarterly sales quotas”) is explicitly tested to assign nothing on its own.
Only explicit supervised/organization-wide scope remains as a shared AE fallback;
the generic independent/project/strategy patterns do not vote for AE.

Added `role_atlas_mappings/ae_segments.json` with the reviewed segment-to-level
bindings, source review and a rationale per row:

| Level | Reviewed segment labels |
| --- | --- |
| L2 | SMB, Commercial, Velocity, Select |
| L3 | Mid-Market, Territory |
| L4 | Enterprise, Corporate, State Enterprise, Public Sector Named Account |
| L5 | Strategic, Named, Global, Key |

`role_segments.py` uses those labels from verified native titles or current
owned-customer duties. It does **not** turn generic geographic territory plans,
strategic account plans, enterprise software, key decision-makers, past
experience or qualification statements into customer segments. Senior/Sr./II/Lead
and years remain weak. Management stays a separate track. Segment scope beats
generic duties; two distinct segment labels remain conflicting, even if their
table levels happen to agree. Thus “Territory (Mid-Market)” is conservatively
unassigned, not silently collapsed into one label; likewise Corporate +
Enterprise and Named + Strategic. The more specific compounds State Enterprise
and Public Sector Named Account consume their contained word matches rather
than inventing a second signal. No analogous title inference was added to
other roles.

The role's ladder now contains the reviewed policy, rationales, native-title/
current-duty ownership evidence, and source review in its `scope`/`owns`;
evidence counts remain actual counts. Audit strong signals record segment,
matched text, origin, source ID, native board hash and rationale. Native titles
are checked against board bytes before use, not falsely bound as description
substrings. Mapping and matcher hashes change comparison identities. Method
copy now describes the reviewed role-specific exception instead of claiming
all title text is weak. No contract/floor/budget change was made.

**New AE distribution:** L1=0, **L2=8, L3=11, L4=19, L5=10**, unassigned=22.
That is **48/70 assigned (68.6%)**, versus 52 postings incorrectly placed at L2
before review. The 22 unknowns are **15 distinct-segment conflicts** and **7
without discriminating current scope**.

#### Title-segment × level cross-tab

Rows use segments stated in the native title only; columns use the final
title + current-duty decision. “none” can therefore receive a level from a
duty. Different title segments are displayed together, not hidden. Counts
include all 70 admitted AE postings.

| Native title segment(s) | L1 | L2 | L3 | L4 | L5 | Unassigned | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| commercial | 0 | 4 | 0 | 0 | 0 | 1 | 5 |
| corporate + enterprise | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| enterprise | 0 | 0 | 0 | 16 | 0 | 3 | 19 |
| mid-market | 0 | 0 | 2 | 0 | 0 | 0 | 2 |
| mid-market + select | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| mid-market + territory | 0 | 0 | 0 | 0 | 0 | 6 | 6 |
| none | 0 | 1 | 0 | 0 | 1 | 7 | 9 |
| select | 0 | 1 | 0 | 0 | 0 | 0 | 1 |
| smb | 0 | 2 | 0 | 0 | 0 | 0 | 2 |
| state-enterprise | 0 | 0 | 0 | 3 | 0 | 0 | 3 |
| strategic | 0 | 0 | 0 | 0 | 9 | 1 | 10 |
| territory | 0 | 0 | 9 | 0 | 0 | 0 | 9 |
| velocity | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| **Total** | **0** | **8** | **11** | **19** | **10** | **22** | **70** |

The exact-title cross-tab is also in the quality JSON. The review's named
examples now read: Strategic Account Executive **7 L5**; Strategic Account
Executive (SLED) **2 L5**; Enterprise Account Executive, Growth **7 L4**;
Senior Enterprise Account Executive, Growth **5 L4**; State Enterprise
Account Executive **3 L4**. These are total title counts, not only the former
L2 subset (the review identified 5 and 3 formerly L2 Growth postings).

#### Rechecked 40 labels — every changed label and why

Original `M7-levels.json` is preserved. New `audits/M7-r1-levels.json`
retains the same 40 IDs/seed, prior levels and prior quote hashes, source board
hashes, reviewed title/quote, new level and per-case rationale. All 40 were
rechecked against the reviewer policy **before comparing new predictions**.
34 levels changed; the six unchanged labels also have rechecked rationales.

**40/40 agreement is conformance to this policy, not independent evidence that
the policy estimates real career levels correctly.** The reviewer’s independent
15-posting spot-check remains pending. The original 82.5% self-label agreement
does not establish validity; I accept the review's criticism.

| Native job ID | Title | Label change | Why |
| --- | --- | --- | --- |
| `6230952004` | Account Executive, Federal - State & Local | L3 → L5 | Current duty owns a Named book of State & Local accounts (L5); generic territory planning and strategic account plans are not extra segments. |
| `5104078` | Account Executive - In-Territory (Los Angeles, CA) | L2 → L3 | Native In-Territory role title states Territory (L3), not a full-cycle-selling L2 inference. |
| `7155024` | Strategic Account Executive | L2 → L5 | Native Strategic role title states L5; generic geographic territory and commercial negotiation are not different customer segments. |
| `4134175007` | Account Executive, Territory (Mid-Market) | L2 → unassigned | Native title names both Territory and Mid-Market. Distinct segment labels conflict even though both map to L3; no silent alias decision. |
| `6430945` | Strategic Account Executive (SLED) | L2 → L5 | Native Strategic title is L5; SLED is not a second named customer segment in the reviewed table. |
| `5027198007` | Account Executive, Territory (Mid-Market) | L2 → unassigned | Native title names Territory and Mid-Market: distinct segments conflict. |
| `5156789007` | Enterprise Account Executive (New York City - Muni) | L2 → L4 | Native Enterprise role title states L4; geographic territory planning is not a different customer segment. |
| `6163246` | Mid Market Account Executive | L2 → L3 | Native Mid Market title states L3; selling activities do not supply an L2 vote. |
| `7027846` | Enterprise Account Executive, Growth | L2 → L4 | Enterprise title states L4; strategic territory plans do not establish Strategic customer ownership. |
| `8123027` | Account Executive, Commercial (Grower) | L2 → unassigned | Commercial title states L2 but the current role introduction explicitly expands revenue from existing mid-market customers (L3): conflict. |
| `7179283` | Senior Account Executive - In-Territory (SF/Bay Area, LA, Orange County, San Diego) | L2 → L3 | In-Territory title states L3; Senior does not change it. |
| `8178881` | Account Executive - Enterprise, Growth | L3 → unassigned | Enterprise title states L4 while the current duty cross-sells into a list of strategic customers (L5): conflict. |
| `8238731` | Account Executive, AI Startups - Grower | L2 → unassigned | AI Startups/Grower is not a reviewed segment; contract renewals and commercial negotiations are common selling duties. |
| `8304836` | Account Executive, Velocity Platforms (Hunter) | L2 → unassigned | Velocity title (L2), Named account list (L5) and Enterprise companies (L4) in current owned-account duties conflict. |
| `6522713` | Account Executive - In-Territory (Chicago) | L2 → L3 | In-Territory title states L3, independent of generic quota or full-cycle work. |
| `5152703007` | State Enterprise Account Executive - VA, WV, NC | L2 → L4 | State Enterprise is one compound L4 segment; the contained Enterprise word is not a second segment. |
| `7687359` | Senior Enterprise Account Executive, Growth | L2 → L4 | Enterprise title states L4; Senior is weak and a strategic territory plan is not Strategic-account ownership. |
| `6522747` | Strategic Account Executive | L2 → L5 | Strategic title states L5; generic commercial negotiations and geographic territory do not override it. |
| `7027834` | Enterprise Account Executive, Growth | L2 → L4 | Enterprise title states L4; strategic territory planning is not a second segment. |
| `7137401` | Account Executive - In-Territory (San Diego, CA) | L2 → L3 | In-Territory title states L3; the old L2 label came from generic selling duties. |
| `5426468004` | Account Executive, Enterprise | L3 → L4 | Enterprise title and the current duty creating/managing a pipeline of Enterprise accounts establish L4; strategic account plans are not Strategic-customer scope. |
| `5199808007` | State Enterprise Account Executive - MD, PA | L2 → L4 | State Enterprise title is the reviewed compound L4 segment. |
| `6318505` | Account Executive - In-Territory (SF/Bay Area, CA) | L2 → L3 | In-Territory title states L3; sales cycle duties are not L2 evidence. |
| `8190036` | Account Executive, Product - Local Payment Methods | L3 → unassigned | Product/Local Payment Methods is not a reviewed segment; complex commercial work and territorial strategies do not state a customer segment. |
| `5558737004` | Account Executive, Strategic | L3 → unassigned | Native Strategic title plus a defined list of named strategic accounts supplies distinct Named and Strategic segment labels: conflict under the literal review rule, despite both mapping to L5. |
| `4248006007` | Account Executive, Select, New York City (Mid-Market) | L2 → unassigned | Select (L2) and Mid-Market (L3) are both explicit in the native title: conflict. |
| `8232743` | Enterprise Account Executive, Privy | L3 → L4 | Enterprise native title and pipeline of enterprise prospects establish L4; global growth strategy is not Global-customer ownership. |
| `6522720` | Strategic Account Executive | L2 → L5 | Strategic title states L5; generic geography and commercial negotiations are not different segments. |
| `4135236007` | Account Executive, Territory (Mid-Market) | L2 → unassigned | Territory and Mid-Market are both explicit in the title: distinct-segment conflict. |
| `8267841` | Account Executive, Velocity Platforms (Hunter) | L2 → unassigned | Velocity title, current Named account list and Enterprise companies span conflicting customer segments. |
| `8097302` | Senior Enterprise Account Executive, Growth | L2 → L4 | Enterprise title states L4; Senior and generic strategic territory plans do not alter it. |
| `5041521007` | Account Executive, Territory, Chicago | L2 → L3 | Territory title states L3; no L2 vote comes from common selling activities. |
| `7314667` | Strategic Account Executive (SLED) | L2 → L5 | Strategic native title states L5; SLED and geographic territory do not establish another customer segment. |
| `6755285` | Enterprise Account Executive, Growth | L2 → L4 | Enterprise title states L4; strategic territory plans are common planning context rather than Strategic account scope. |

### Should 1 — fresh held-out section inspection, including the error

Added `audits/M7-r1-sections.json`. Seed **20261107**, frozen before
inspection; eligible successes sorted by native job ID. Excluded **all 70
originally admitted postings** (which include the old level/match examples) and
the original 20-case section audit. Their union is 88 IDs, leaving **317**
successes. Drew 20 new IDs without rerolling the seed. This is a held-out
**manual span inspection**, not a claim that the entire capture was unseen
by development coverage diagnostics.

Reviewed all **129** returned responsibility spans. **19/20 postings had no
wrong span; one wrong span out of 129**. All 20 still contain genuine duties.
The wrong case is Growth Manager native job **`5255724007`**, first span:
“At Verkada, we're known to push traditional boundaries, and our B2B growth
efforts are no exception.” Its full compound span continues company/team
positioning and team-member backgrounds, not current responsibility evidence.
Its saved full-span hash is
`a2cbaa4f8508b6b17b3975165ed9061a46fa4e5b07b6480a9e396545c3b345ca`.
The excerpt, wrong-span index, reason and hashes of all 129 spans are pinned.
The extractor hash is pinned too. **No extraction tuning followed this
held-out inspection**; the unfavorable result is retained as requested.

### Should 2 — top 25 posting-led gaps, no new grouping rules

The projection still has **284 nodes: 18 backbone-linked / 266 posting-led**.
No O*NET/phrase grouping rule or derived lookup was changed. The existing
30-span grouping audit remains **29/30**, not a new independent test.
These are the most frequent posting-led labels, sorted by share descending,
then stable skill ID. All use the frozen 70-posting denominator; the
one-employer concentration is visible. Many are qualifications, preferences
or generic duties, not automatically suitable new backbone skills. This is
a gap review queue, not a promotion into semantic equivalence.

| Rank | Posting-led label | Postings | Share | Employers |
| ---: | --- | ---: | ---: | ---: |
| 1 | fluent in english | 12/70 | 17.1% | 1 |
| 2 | databases, develops and open source technology a plus | 12/70 | 17.1% | 1 |
| 3 | previous sales methodology training (e.g. meddic, spin, challenger sales) | 12/70 | 17.1% | 1 |
| 4 | passionate about growing your career in the largest market in software (database) | 12/70 | 17.1% | 1 |
| 5 | a proven track record of overachievement and hitting sales targets | 12/70 | 17.1% | 1 |
| 6 | act as a trusted advisor and subject matter expert to customers and channel partners | 11/70 | 15.7% | 1 |
| 7 | aggressively exceed goals consisting of outbound phone calls, emails, online demos, and trials every quarter | 10/70 | 14.3% | 1 |
| 8 | experienced in working for an innovative tech company (saas, it infrastructure or similar preferred) | 9/70 | 12.9% | 1 |
| 9 | thrive in a dynamic, competitive, and fast-paced startup environment | 9/70 | 12.9% | 1 |
| 10 | develop a deep comprehension of customer's business | 9/70 | 12.9% | 1 |
| 11 | handle existing customer expectations while expanding reach and depth into assigned territory | 9/70 | 12.9% | 1 |
| 12 | co-selling with cloud service providers (aws, gcp and azure) and large gsis (accenture, kpmg, etc) | 9/70 | 12.9% | 1 |
| 13 | ensure high forecasting accuracy and consistency | 9/70 | 12.9% | 1 |
| 14 | demonstrate resourcefulness when faced with challenges that defy easy solution | 9/70 | 12.9% | 1 |
| 15 | driven and have met/exceeded direct sales goals of 1m+ and operated with an average deal size of $100k+ | 9/70 | 12.9% | 1 |
| 16 | you enjoy entrepreneurial environments and the opportunity to build something meaningful! | 8/70 | 11.4% | 1 |
| 17 | bachelors degree preferred but not required | 8/70 | 11.4% | 1 |
| 18 | excellent interpersonal and communication skills, and demonstrates the ability to listen to gather important information during a sales call and/or meeting | 8/70 | 11.4% | 1 |
| 19 | must live in territory (flexible work model) | 8/70 | 11.4% | 1 |
| 20 | tenacity, drive to learn, and self-motivated | 8/70 | 11.4% | 1 |
| 21 | simplify complicated topics and explain them in terms people can easily understand | 8/70 | 11.4% | 1 |
| 22 | able to apply creative solutions to a variety of issues in a fast-paced environment | 8/70 | 11.4% | 1 |
| 23 | identify robust set of business drivers behind all opportunities | 7/70 | 10.0% | 1 |
| 24 | perform other duties as needed based on department and/or organizational needs | 7/70 | 10.0% | 1 |
| 25 | be the voice of our future customers - suggest innovative ideas to generate sales, cultivate customer pipeline leads; improve overall customer experience and advocate internally for product development | 7/70 | 10.0% | 1 |

### Exact verification commands and results

Fresh physical projections, including audit files:

```sh
.venv/bin/skills-vector market posting-volume project --snapshot /home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4 --output /tmp/role-atlas-m7-r1-release > /tmp/role-atlas-m7-r1-release.json
.venv/bin/skills-vector market posting-volume project --snapshot /home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4 --output /tmp/role-atlas-m7-r1-release-rebuild > /tmp/role-atlas-m7-r1-release-rebuild.json
diff -qr /tmp/role-atlas-m7-r1-release /tmp/role-atlas-m7-r1-release-rebuild
.venv/bin/python scripts/audit_role_atlas_m7.py --snapshot /home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4 --output /tmp/role-atlas-m7-r1-quality-final.json > /tmp/role-atlas-m7-r1-quality-final.log
cmp /tmp/role-atlas-m7-r1-quality.json /tmp/role-atlas-m7-r1-quality-final.json
```

All exit **0**; both comparisons emit nothing. New research release
**`role_research_30f84c0343927598`**. Select
`/tmp/role-atlas-m7-r1-release/roles`. Quality JSON reproduces the segment and
exact-title cross-tabs, all 34 label changes, held-out selection/span bindings
and the 25 posting-led labels above. Source hashes in the physical artifact
were checked against the final producer/matcher/adapter files.

```sh
.venv/bin/python -m unittest discover -s tests -p test_role_atlas_m7.py > /tmp/role-atlas-m7-r1-focused-reviewed.log 2>&1
.venv/bin/python -m unittest discover -s tests -p test_posting_volume.py > /tmp/role-atlas-m7-r1-posting-final.log 2>&1
.venv/bin/python -m unittest discover -s tests > /tmp/role-atlas-m7-r1-reviewed-tests.log 2>&1
```

Focused: **15 tests / 1.221s**, **16 tests / 22.652s**, both OK.
Final full: **252 tests in 154.131s, OK (skipped=2)**, concurrent with browser
sweeps; same two existing skips, no new skips. New tests cover positive and
rejected examples for each segment row, qualification/negation/non-customer
context, generic duties, weak seniority, other-role isolation, compounds,
different/same-level segment conflicts, people-management routing, policy
rationale validation, ladder/hash provenance and retained held-out errors.

One old authored-sample assertion expected 7 unassigned postings and failed
with 17 under the corrected policy. It now asserts the explicit per-role
counts: AE=12, FDE=2, Growth=1, HR=2. The sample AE titles/duties lack a
reviewed segment; the extra 10 unknowns must not be restored by generic rules.
The other-role counts are unchanged. No production extraction/grouping
behavior was altered to satisfy this assertion.

```sh
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m7-r1-reviewed-fixtures --role-atlas-fixtures --role-atlas-ledger /tmp/role-atlas-m6-fixture-ledger.json > /tmp/role-atlas-m7-r1-reviewed-fixtures.log 2>&1
UV_OFFLINE=1 uv run --with playwright python scripts/check_role_atlas.py /tmp/role-atlas-m7-r1-reviewed-research --role-atlas-root /tmp/role-atlas-m7-r1-release/roles > /tmp/role-atlas-m7-r1-reviewed-research.log 2>&1
UV_OFFLINE=1 uv run --with playwright python /tmp/role-atlas-m7-r1-visual.py > /tmp/role-atlas-m7-r1-visual.log 2>&1
```

All exit **0**, explicit Chromium
`~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`.
Both selections run **1280 light, 1280 dark, 390 light**, every role/tab/
Connections and negative probes: fixtures **54** and new research **72**
screenshots, every `errors: []`. The unchanged M6 temporary fixture ledger
is selected only for fixtures; the new research projection does not use an
old-release ledger. Opened desktop-dark and mobile-light level viewport shots:
revised distribution, actual counts, long title/evidence wrapping and the
reviewed policy are visible. The final browser reruns include the corrected
Method wording, not the stale “all title text is weak” text.

```sh
bash scripts/assemble_vercel.sh
UV_OFFLINE=1 uv lock --check --offline
git diff --check
node --check preview/role-atlas.js
.venv/bin/python -m compileall -q src/skills_vector scripts/audit_role_atlas_m7.py
git diff --exit-code -- src/skills_vector/market/posting_sections.py src/skills_vector/market/onet_backbone.py src/skills_vector/market/role_atlas_mappings/dwa_rules.json src/skills_vector/market/role_atlas_mappings/onet31_lookup.json docs/role-atlas/BRIEF.md docs/role-atlas/STANDARDS.md
```

All exit **0**: 551 assembled files, 61 locked packages; no deployment.
Final hash/file-set verification against the existing input receipt and
tracked protected files: **334 read-only input files**, **543 protected tracked
files**, **533 published files/file set**, all unchanged. The exact Python
receipt/protected-file integrity command is the M7 integrity block above,
rerun here; it also rechecked both browser results manifests and the physical
artifact producer hashes. M4/M5 artifacts, original M7 audits and previous
scratch projections remain intact. Independent
posting/model limits and research configurations are unchanged.

### Remaining limitations and stop

All 284 skills remain early in one quarter; zero kept edges, 80 null sector
profiles, no fabricated forecast probabilities/calibration or primary sectors.
Other roles retain zero admitted postings in this frozen capture. The starter
frame remains biased. Generic geography is not a new segment, and compound/
multi-segment titles are deliberately not resolved by guessing a preferred
segment or silently adopting unreviewed aliases. The held-out intro-span error
is still present and recorded; no fix was tuned against that audit. Policy
conformance is not independent validity. Await the reviewer’s 15-posting
spot-check and any alias-policy clarification rather than claiming level
quality approved.

Must 1 and both shoulds are addressed for review. Commit this review fix
separately on `feat/role-atlas`, then stop. No live retrieval was run.
