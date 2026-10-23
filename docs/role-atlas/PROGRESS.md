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
