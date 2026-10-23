# Role Atlas build brief

You are building the Role Atlas: Skills Vector's per-role reference for people and agents. The approved render is `design-concepts/role-atlas.html`; open it and read its data model before writing code. The design rules are in `DESIGN.md` (see "Role Atlas surfaces"). The bar you must meet is in `docs/role-atlas/STANDARDS.md`. A reviewer checks every milestone against that file and writes findings to `docs/role-atlas/reviews/`.

## What it is

For any role, it answers:

1. Which skills are needed, at which level, and in which broad industry sector?
2. Which of those skills are changing?

It also shows the evidence behind each answer.

- **Skill map:** each skill has one of four statuses (`core`, `rising`, `early`, `fading`), level coverage, evidence families (P/O/M), and, unless it is `core`, a dated forecast.
- **Levels:** a role-specific ladder on a shared L1–L5 scale, built from scope and ownership evidence.
- **Industries:** broad NAICS sectors (2-digit), each with its duties, certifications, domain knowledge and skill shares. A sector below the floor has no profile.
- **Your gap:** a local-only comparison of the reader's skills against a target level.
- **For agents:** the same record as JSON (`skills-vector.role/2`), served from REST and MCP.
- **Method:** source timing, status rules and the forecast track record.

## Settled product decisions (do not reopen)

- Industries use broad NAICS sectors. The largest sub-industries are named inside each sector.
- "Top level adds" compares the top level with the core level (`mid`) using posting shares. O*NET level ratings are the long-run bar, labeled `lagging`.
- Every signal carries a timing class: `leading`, `coincident`, `lagging` or `long-term` (mapping in `DESIGN.md`).
- **Status rules:**
  - `core`: ≥25% of postings at its levels, in ≥2 windows, from ≥10 employers.
  - `rising`: up ≥5 points across 2 comparable windows, ≥8 employers, no employer >20%, AND ≥1 non-posting evidence family (O or M) agreeing.
  - `early`: posting evidence only, or only one window.
  - `fading`: down ≥5 points across 2 windows with the same employer floor.
  - `disputed` is a flag, not a status.
- **Forecasts:** horizon is 4 quarters. Each forecast states a claim, probability, base rate, resolve window, and what it is scored against. People see the percentage.
- **Asked-together edges:** lift ≥1.5, ≥25 postings together, ≥8 employers.
- **Sector profile floor:** ≥30 postings from ≥8 employers in the window.
- **Emerging roles:** a role whose status is `emerging` (no O*NET occupation matches the title) is published with `publication: "experimental"` and shown as experimental. An established role with a partial anchor (for example Account Executive) stays `standard`; its anchor shows `fit: partial`.
- **Levels:** role-specific names on a shared L1–L5 scale. Management is a linked role, not a level.
- **Still open:** window length (monthly vs quarterly). Keep it a config value; default to quarterly.

## Milestones (build in order; stop after each and report)

1. **M1 Contract.** Build the `skills-vector.role/2` schema: a JSON Schema file plus typed Python models, next to the existing market core.
   - Rule engine: status derivation, the edge threshold, the sector floor, forecast validity and experimental publication. It must reject records that break any rule.
   - A fixture catalog built from the prototype's illustrative data. Every record is marked `"data_kind": "fixture"`, and this catalog is never written into `preview/release/`.
   - Read methods on the existing `CatalogStore` (or a sibling store with the same conventions).
2. **M2 Read surfaces.**
   - REST: `GET /v1/roles`, `/v1/roles/{role}`, `/v1/roles/{role}/levels/{L}`, `/v1/roles/{role}/graph`, `/v1/roles/{role}/industries/{naics}`, `/v1/shifts?since=`, `/v1/expectations/{id}@{release}`.
   - MCP tools that return identical payloads.
   - Wire both through the existing `api/index.py`, `market/api.py` and `market/mcp.py` patterns, and update the agent guide.
3. **M3 Web app.** Build the Role Atlas page from the role/2 API, faithful to `design-concepts/role-atlas.html` and `DESIGN.md`.
   - It ships in the Vercel assembly (`scripts/assemble_vercel.sh`) next to the existing preview, without breaking it.
   - Add the browser check script described in STANDARDS.
4. **M4 Evidence pipeline (offline, deterministic).** Produce role/2 records from the real published release data:
   - per-posting level triage;
   - employer → NAICS sector tagging from a reviewed mapping file;
   - skill mapping onto an O*NET/ESCO backbone from a local snapshot;
   - windows, statuses and co-occurrence lift.

   Thin data must come out honestly thin (floors applied, no padding).
5. **M5 Official signals and forecast ledger.**
   - Operator-run importers for O*NET Hot Technologies, Emerging Tasks, BLS occupation × industry and CareerOneStop certifications. The certifications importer accepts a local file, since its API needs a key we do not have.
   - A forecast ledger with create, resolve and score (Brier score plus calibration bins).
   - A backtest harness interface that reports "not run: no historical posting archive" until data exists.

## Guardrails (hard)

- Work only in this worktree, on branch `feat/role-atlas`. Commit at the end of each milestone with a clear message. Never push, never touch `main`, and never rewrite history.
- Never call paid or metered services, DeepInfra, or any model API from product code or tests. Tests are offline.
- Never read, print or copy secrets or `.env` files.
- Never modify existing immutable releases under `preview/release/releases/` or `preview/release/current.json`.
- Fixture or illustrative data is always labeled as such in the data, the API and the UI.
- Keep the existing test suite green: `.venv/bin/python -m unittest discover -s tests`. If `.venv` is missing in this worktree, create it with `uv sync`.
- Match the surrounding code style: dataclasses, stdlib-first, the existing naming conventions, and comments only where the code is not obvious.

## Reporting

At the end of each milestone, append to `docs/role-atlas/PROGRESS.md`:

- what you built;
- the exact verification commands and their results;
- any deviation from this brief, and why;
- open risks.

Then stop and wait for review. When a review file exists for your milestone, fix every finding marked `must`, reply to each finding in PROGRESS.md, and stop again.
