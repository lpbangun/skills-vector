# Role Atlas standards (the bar)

A milestone passes only when every item applicable to it is true. Reviews quote item IDs. Severity in reviews: `must` blocks the milestone; `should` is fixed in the same milestone unless the reply gives a reason the reviewer accepts; `note` is optional.

## G: global, every milestone

- **G1** The full suite passes: `.venv/bin/python -m unittest discover -s tests`. No newly skipped tests, no network access in tests.
- **G2** New behavior has tests. Every rule has at least one test that passes and one that is rejected. Tests assert values, not just "no exception".
- **G3** No paid or model calls, no secrets touched, no edits to existing immutable releases, no changes outside the milestone's scope without a note in PROGRESS.md.
- **G4** Code reads like the surrounding code: dataclasses and stdlib first, no new heavy dependency without a reason in PROGRESS.md, no dead code, no commented-out blocks.
- **G5** PROGRESS.md entry with the exact commands run and their real output summary. Claims there must be reproducible by the reviewer.
- **G6** One commit per milestone (plus fix-up commits after review) on `feat/role-atlas`.

## M1 contract

- **C1** `skills-vector.role/2` JSON Schema checked in. Python models round-trip: model → JSON → schema-valid → model, with no field loss.
- **C2** The record carries:
  - id, label, status, publication (`standard` | `experimental`), data_kind (`fixture` | `research`), release_id, anchors;
  - foundation, with a timing class per source;
  - demand_window;
  - ladder: levels with titles, scope, owns, step_up, evidence counts and expectations;
  - skill_map: skills, evidence, forecasts, asked_together, top_level_adds;
  - industries;
  - limits.
- **C3** Status derivation is a pure function of window measurements plus evidence families. It reproduces all of BRIEF's rules exactly, including the boundary cases: exactly 25%, exactly 5 points, 7 vs 8 employers, one employer at 21%, and rising without an O/M family becoming early.
- **C4** Validation rejects:
  - a non-core skill without a forecast;
  - a forecast whose horizon is not 4 quarters, or with no base rate, or with probability outside 0–1;
  - edges below the threshold;
  - a sector profile below the floor (it must instead be `profile: null` with a reason);
  - an `emerging` role not marked experimental, or a non-emerging role marked experimental;
  - a `rising` status whose only non-posting signals do not point up (`direction` must be `up`; presence in a map is not confirmation of a rise);
  - any signal without a timing class.
- **C5** The fixture catalog covers the three prototype roles, with numbers matching `design-concepts/role-atlas.html`. Every record says `data_kind: "fixture"`. Nothing fixture is written under `preview/release/`.

## M2 read surfaces

- **R1** Every endpoint in BRIEF exists. Unknown role, level or sector returns a structured 404, matching the existing `_error` conventions.
- **R2** REST and MCP parity: a test calls each pair and asserts identical JSON.
- **R3** Responses are pinned to `release_id`, and the citation id `{id}@{release}` resolves.
- **R4** The agent guide documents the new endpoints and tools, the fixture caveat, and the experimental flag.

## M3 web app

- **W1** Visual fidelity to `design-concepts/role-atlas.html`. The same structure, components and vocabulary (DESIGN.md "Role Atlas surfaces"). The reviewer compares screenshots side by side.
- **W2** The page reads only from the role/2 API (or its static JSON export). No data is hard-coded in the page.
- **W3** `scripts/check_role_atlas.py` (Playwright, using the local Chromium under `~/.cache/ms-playwright`) loads the page at 1280px light, 1280px dark and 390px light. It visits every tab and role and exits non-zero on any console error, page error, or `scrollWidth > viewport`. It saves screenshots to a path given as an argument.
- **W4** Accessibility:
  - tabs follow the WAI-ARIA tab pattern;
  - map rows are buttons with full `aria-label`s;
  - visible focus, 44px targets;
  - colour is never the only carrier of meaning.
- **W5** The fixture callout shows whenever `data_kind` is `fixture`. The experimental caveat shows for experimental roles.
- **W6** The existing preview page and its tests still work, and `scripts/assemble_vercel.sh` includes the new page.

## M4 evidence pipeline

- **P1** Deterministic. The same inputs produce byte-identical role/2 output, proven by a test.
- **P2** Inputs are the published release files plus checked-in mapping files: the employer → NAICS sector map, with a source note per row, and the skill phrase → backbone id map. No network.
- **P3** The level triage uses scope and ownership signals first, and title and years only as weak signals. Conflicting strong signals leave the posting `unassigned` and count it.
- **P4** Floors are applied, never padded. With current volumes most statuses will be `early` or the profile `null`, and that is the correct output.
- **P5** Records produced from real data say `data_kind: "research"` and pass the M1 validator.

## M5 signals and ledger

- **S1** Importers are operator CLI commands that read downloaded files from a given path. Tests use tiny checked-in samples.
- **S2** Each imported signal records its source, retrieval date, timing class and evidence family.
- **S3** The ledger creates forecasts with id, claim, probability, base rate, created, resolves, and scored_against. It resolves them against a later window, and computes the Brier score and calibration bins. The math is tested against hand-computed values.
- **S4** The backtest interface exists and states "not run" plus the reason when there is no archive. It never emits made-up scores.
