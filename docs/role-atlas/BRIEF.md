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
- **Window length:** quarterly (settled 2026-10-23). It stays a config value.

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

## M6: Posting volume (no-model extraction lane)

Why: the current release admits 5 postings per role because model-backed extraction is capped (12 DeepInfra calls a run, `max_postings: 5`). The same run excluded 5,898 public postings at that cap. The Role Atlas needs hundreds of postings per role per quarter before core, rising, edges, sector profiles or scored forecasts can exist.

Build a deterministic, model-free lane that turns public job-board postings into role/2 evidence:

- **Retrieval:** reuse the existing allowlisted public job-board parsers (Greenhouse, Lever, Ashby and the others in `market/sources.py`), the US geography rule and dedup. No paid or metered search, and no new hosts without a note in PROGRESS.md.
- **Extraction without a model:**
  - split each posting's description into sections (responsibilities, requirements, preferred, about the company, benefits) using headings and list structure;
  - keep each bullet as a verbatim quote with its source hash, exactly like today's source-verified excerpts;
  - drop company, benefits and legal sections;
  - record a per-posting `extraction_method: "rules/1"`.
- **Admission:** a new operator policy with `max_postings_per_role` up to 500, with fair allocation per employer: no employer above 20% of a role's sample. It must sit under a new hard ceiling in `market/limits.py` that only this lane uses. The model-backed lane's limits stay unchanged.
- **Output:** a snapshot directory per quarter (quarterly is settled) outside `preview/release/releases/`, with a manifest, hashes and per-posting provenance. Existing immutable releases stay untouched.
- **M4 pipeline:**
  - accept these snapshots as input, so that two quarterly snapshots give two comparable windows;
  - scope and ownership triage now sees full responsibility sections, and should assign far more levels, with P3 rules unchanged;
  - the phrase map gains rules-based normalization (lowercase, strip boilerplate such as "experience with", "ability to", "is a plus"). Labels stay traceable to their quotes.
- **Tests:** offline only, using checked-in sample board payloads in the real upstream formats. Cover section splitting, boilerplate dropping, the employer cap, determinism, and that no model client is imported in this lane.
- **Operator run:** document the exact command. Pi does not run live retrieval. The reviewer or user runs it.

Acceptance (also G1–G6, P1–P5):

- **V1** No model or provider import anywhere in the lane, enforced by a test.
- **V2** For 300 sample postings per role, the employer cap holds and output is byte-identical across runs.
- **V3** Every emitted expectation quote is an exact substring of the retrieved description (hash-checked).
- **V4** At least 80% of the checked-in sample postings get a responsibilities section. The report lists the failures.

## M7: Real-data quality (from the first live capture)

First live capture: `/home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4` (2026-Q4, 13 boards, read-only). Projected through the M4/M6 pipeline:

| Role | Eligible | Admitted | Employers | Responsibilities found | Levels assigned | Distinct skills | Edges kept |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Account Executive | 333 | 70 | 7 | 101/333 (30%) | 3/70 | 403 | 0/3970 |
| Forward Deployed Engineer | 62 | 0 | 0 | 8/62 | – | – | – |
| Growth Manager | 8 | 0 | 0 | 7/8 | – | – | – |
| HR Generalist | 2 | 0 | 0 | 0/2 | – | – | – |

The authored samples overfit: V4 passed at 87.5% on samples and reached 30% on real boards. M7 makes the lane work on real text. Use the capture above as a read-only diagnostic corpus. Never copy whole real postings into the repo. Tests use short, synthetic reproductions of the structures you observe, with no more than one real sentence per case.

1. **Section detection on real boards.**
   - Diagnose the 232 AE and 54 FDE failures by ATS and by structure: for example headings in `<strong>`/`<p>` rather than `<h*>`, "What you'll do"/"In this role" phrasing, colon-led paragraphs, or no heading at all.
   - Raise responsibilities detection to ≥80% of eligible postings per ATS on the live capture, with no new false sections. Spot-check 20 random successes and list any wrong spans.
   - Report a per-ATS before/after table.

2. **Skill grouping onto O*NET 31.0** (local download: `/home/logani/.local/share/skills-vector-evidence/onet/db_31_0`; source zip sha256 `6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9`, CC BY 4.0).
   - Deterministically map each expectation span to:
     - a Detailed Work Activity (`Tasks to DWAs`, `GWAs to IWAs to DWAs`);
     - and/or a `Software Skills` commodity or example.
   - The role's skill-map nodes become those backbone ids, so the same skill worded differently counts once.
   - Spans that match nothing stay posting-led, after phrase-rules/1.
   - Lexical and rule-based only; no embeddings and no models. Each match records method, matched text and confidence (exact, close or loose). Only exact and close matches group.
   - Check in the derived lookup tables you need, with a source note, a hash of the source zip and the attribution. Do not check in the whole database.
   - Update the M5 Hot Technologies importer, if needed, for the 31.0 file names (`Software Skills.txt`).
   - Report the AE skill count before and after grouping, the top 25 grouped nodes with posting share and employer count, and a 30-span random audit of matches you judge right or wrong.

3. **Level triage on real text.**
   - Tabulate the reasons for the 67/70 unassigned AE postings.
   - Add scope and ownership patterns only where they are current-duty statements (segment owned, quota or territory scope, deal size, team leadership without direct reports, and so on). Keep P3: titles and years stay weak, and conflicts stay unassigned.
   - Hand-label 40 AE postings against the rubric. Put the labels in a checked-in review file with a quote per label, and report agreement between the rules and your labels.
   - Target: ≥50% assigned with ≥85% agreement on the labeled set. If that is impossible without breaking P3, say so and stop at the honest number.

4. **Roster tooling (no live calls by Pi).** Add `posting-volume roster-probe --candidates <file>`:
   - an operator command that checks candidate ATS tokens on the existing allowlisted hosts;
   - it records, per token, whether the board exists, its postings total and matched titles per role;
   - it writes a reviewed-roster draft with source notes.
   - Tests are offline with sample payloads. The reviewer runs it live.

Acceptance (plus G1–G6, P1–P5, V1–V3):
- **Q1** ≥80% responsibilities per ATS on the live capture.
- **Q2** AE distinct skill nodes fall substantially (report the number), with a ≥80% judged-correct rate in the 30-span audit.
- **Q3** The level target as stated, or an honest stop.
- **Q4** Deterministic: re-projecting the live capture twice is byte-identical.

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
