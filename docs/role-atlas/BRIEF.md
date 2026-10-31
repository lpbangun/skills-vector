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

## M8: Canonical 2026-Q4 research release

Canonical 2026-Q4 capture (reviewer-run, 71 reviewed boards): `~/.local/share/skills-vector-evidence/role-atlas/capture-20261024T001827Z/2026-Q4`. It supersedes the 13-board starter capture as the Q4 frame. The starter stays on disk and is never mixed in.

Reviewer projection with M7 code:

| Role | Admitted | Employers | Max employer | L2/L3/L4/L5/unassigned | Skills (O-linked) |
| --- | ---: | ---: | ---: | --- | ---: |
| AE | 436 | 36 | 87 (20%) | 35/87/85/77/152 | 1489 (25) |
| FDE | 31 | 17 | 6 | 0/0/7/0/24 | 233 (13) |
| Growth Manager | 17 | 11 | 3 | 0/0/4/0/13 | 166 |
| HR Generalist | 5 | 5 | 1 | 0/0/0/0/5 | 58 |

1. **Fix first:** the two items in `reviews/M7-r2.md`.

2. **Tool and term grouping.**
   - Map spans to O*NET 31.0 `Software Skills` *examples* at the token or phrase level (for example "Salesforce", "Python", "SQL", "Snowflake"), so that one span can yield several software nodes. Use exact, case-aware word-boundary matching with an alias table; examples shorter than 3 characters need an explicit allow-list.
   - Add a checked-in, reviewed **emerging-terms lexicon** for terms that O*NET lags on: LLMs, RAG, agents, evals, fine-tuning, MLOps, vector databases, prompt engineering, and so on. Each row carries:
     - a canonical id and aliases;
     - a rationale;
     - `family: "P"`, because it is posting-led and never official.

     These become canonical posting-led nodes, so the same term counts once across employers. They never gain an O link.
   - Report per role:
     - node counts before and after;
     - the top 20 nodes by share, with employer counts;
     - a 30-span random audit of the new matches, with a different seed from M7.

3. **Commit the release.**
   - Commit the projection's role records and audit as `research/role-atlas/2026-Q4/`. Quotes are already single-sentence spans. Do **not** commit raw board payloads or full descriptions.
   - Make it selectable like M4 (`--role-atlas-root research/role-atlas/2026-Q4/roles`). Create its forecast ledger with every forecast `basis: none`.
   - Run the checker against it at all three cases.
   - Review the AE and FDE skill-map screenshots for readability with about 1,000 nodes. If the map has too many rows to read, the page must collapse rows below a stated share or employer floor into an "N more, asked by fewer than K employers" group per status. Nothing is deleted, and the floor is stated on the page.

4. **Honest framing on the page:**
   - "Sample: N postings from M employers on public job boards (Greenhouse, Lever, Ashby); tech-heavy, not a market census."
   - Show this on every role, next to the existing fixture or research notice.

Acceptance (plus G1–G6, P1–P5, V1–V3, W1–W6):
- **T1** Byte-identical double projection.
- **T2** ≥90% judged correct in the 30-span audit of the new tool and term matches.
- **T3** The FDE top 20 contains canonical nodes instead of single-employer phrasings wherever a lexicon or software alias applies.
- **T4** The skill map stays readable at 1280 and 390 with the real AE release.

## M9: Verified sectors and the structured career-page lane

Two gaps remain after M8. The Industries tab is empty because every employer in `role_atlas_mappings/employers.json` is `unverified`. HR Generalist (5 postings) and Growth Manager (17) are thin because most of their employers post on Workday, iCIMS and their own career sites rather than Greenhouse, Lever or Ashby. Both are fixed from free, public, primary sources.

### Part A: employer sectors from SEC EDGAR

Reviewer-downloaded inputs (the user approved the contact header; never put any email or contact string in the repo, tests or logs):
- `~/.local/share/skills-vector-evidence/sec/employer_sic.json` has 30 roster employers matched to SEC registrants, each with CIK, ticker, SIC code and description, and source URL. The reviewer rejected four name collisions: Linear, Mercury, Sierra and Vanta. Waymo and Zoox are subsidiaries, so they are not mapped (the parent's SIC is not the employer's sector). The raw submission files are in `sec/submissions/`.
- `~/.local/share/skills-vector-evidence/sec/census/1987_SIC_to_2002_NAICS.csv` is the Census concordance, converted from the `.xls` (sha256 of the xls: fcb8ad7e4162a4d95c39ebe51c12e8ec20dfa6a31f2e81bf8fb1de08fbedc937).

Build:
1. An operator command, `market role-atlas sectors import --sec <dir> --concordance <csv>`. It writes a checked-in `role_atlas_mappings/sic_sectors.json` and updates `employers.json`. It reads local files only.
2. SIC → NAICS sector rule. A SIC maps to a 2-digit sector automatically only when every concordance row for it falls in one sector. Otherwise it needs a reviewed decision row with a note. Reviewer decisions:

   | SIC | Sector | Note |
   | --- | --- | --- |
   | 7372 | 51 Information | Software publishing, NAICS 511210. 334611 (mass reproduction) does not describe these registrants. |
   | 7374 | 51 Information | 518210, unambiguous. |
   | 4832 | 51 Information | 5151xx, unambiguous. |
   | 7370 | 54 Professional, Scientific and Technical Services | SEC aggregate code, not in the 1987 concordance. Its children 7371/7373 map to 5415xx. |
   | 7373 | 54 | 541512, unambiguous. |
   | 6141, 6211, 6324 | 52 Finance and Insurance | Every concordance row falls in 52. |
   | 6199 | 52 | SEC code, not in the concordance; the SIC major group 61 (nondepository credit) falls in 52. |
   | 5812 | 72 Accommodation and Food Services | Restaurant rows; dinner theater (711110) does not apply to the registrant. |
   | 3949 | 31–33 Manufacturing | 339920, unambiguous. |
   | 5500 | 44–45 Retail Trade | SEC aggregate code for major group 55; its children map to 441xxx/447xxx. |
   | 7389 | none | Spans 10+ sectors. DoorDash, Lyft and Instacart stay unverified with reason "SIC 7389 is ambiguous across sectors". |
   | 7340 | none | SEC aggregate; children span 56 and 53. Airbnb stays unverified with the same kind of reason. |

   Use sector 31–33 and 44–45 as NAICS writes them, never split by SIC.
3. Each verified employer row carries:
   - `status: "verified"`;
   - `naics` sector;
   - `source: "sec_edgar"` with CIK, SIC and SIC description, and retrieval date;
   - `rule`: `concordance_unambiguous` or `reviewed_decision` with the decision note.

   Private employers keep `unverified` with their existing note. Nothing is inferred from names or job text.
4. Re-project `2026-Q4` with the verified map. Sector profiles appear only where the floor (≥30 postings, ≥8 employers) is met. Others stay `profile: null` with the reason and the counts. The Industries tab must list every sector seen, including below-floor ones with their counts, plus "Unverified employers: N postings from M employers".

### Part B: structured career-page lane (`schema.org/JobPosting` JSON-LD)

Employers publish `JobPosting` JSON-LD on their own job pages so that search engines can index them. Workday tenants, iCIMS and most custom career sites do this. The reviewer checked a live Workday tenant: its robots.txt lists `Sitemap:` lines and `Allow` for the career site; its sitemap lists job URLs; and each job page carries one `JobPosting` with title, hiringOrganization, jobLocation, datePosted, validThrough, employmentType, identifier and a full HTML description (about 10 KB). Saved samples, for format reference only (do not check them in): `~/.local/share/skills-vector-evidence/jsonld-samples/workday-salesforce-{robots.txt,sitemap.xml,job.html}`.

Prior art to port from (both MIT, same author; attribute in the module docstring the same way `general-jobs-index` does for oh-shi):
- `/home/logani/projects/general-jobs-index/src/lib/structured-career-page.ts` and `src/lib/public-web.ts`: robots parsing, sitemap discovery, same-site checks, bounded fetch;
- `/home/logani/projects/jobsss/src/discovery.js` `jobPostingJsonLd`: `@graph` and array handling.

Build it as a new board kind `jsonld` in the M6 posting-volume lane, so admission, the employer cap, sections, extraction, quotes and projection are all reused unchanged:
1. **Roster, not the open web.** Each `jsonld` board in `config/role-atlas-posting-volume.json` names an employer, its career-site origin, an optional sitemap URL, and a `note` on why that host belongs to that employer (employer domain, or its ATS tenant subdomain). Aggregator and job-board hosts are rejected by a checked-in denylist: indeed, linkedin, glassdoor, ziprecruiter, monster, simplyhired, talent.com, jooble, adzuna, careerbuilder, dice, builtin, wellfound and google. This rejection is tested. Postings must be employer-published; reposts are not.
2. **Politeness, enforced in code and tested:**
   - fetch robots.txt first and obey Disallow for the lane's own User-Agent and `*`;
   - discover job URLs only from sitemaps (robots `Sitemap:` lines or the configured one), with sitemap-index recursion depth ≤2;
   - at most one request per second per host;
   - at most 600 job pages per board per run;
   - a 2 MB response cap, HTTPS only, and redirects only to the same site;
   - a User-Agent from config (the operator supplies it; it is not hard-coded).
3. **Parsing.** Read `JobPosting` from `application/ld+json`, handling `@graph`, arrays and `@type` as a list. Skip postings past `validThrough` relative to the window, or with no description. The description is normalized exactly like ATS descriptions, so quotes stay exact substrings (V3). Record `extraction_method` as now, plus `source_kind: "jsonld"` and the page URL as provenance. Geography uses `jobLocation` and `applicantLocationRequirements` under the existing US rule.
4. **Title prefilter before fetching pages.** Workday-style job URLs carry the title slug. Use the role title hints on the URL slug to avoid fetching thousands of unrelated pages. Report fetched against skipped.
5. **Dedup** across board kinds by employer + normalized title + location + identifier where present.
6. **roster-probe** gains `jsonld`: for a candidate origin, report robots allow or deny, sitemap found, job URL count, title-hint matches per role, and whether a sampled page has a parseable `JobPosting`. Probing is read-only and budgeted like the existing probe.
7. **Tests:** offline only, with small synthetic fixtures in the real formats (robots with Disallow, sitemap index, a job page with `@graph`, a page with no JSON-LD, an expired `validThrough`, and an aggregator host). Cover each rule passing and rejected (G2), and that the lane imports no model or HTTP client beyond the existing fetch layer (V1).

The reviewer then proposes Workday and iCIMS candidates weighted toward HR Generalist and Growth Manager employers, runs the probe and a live capture, and the Q4 release is re-projected from the merged capture as a new release id. The superseded release stays on disk, and the API selects the new one.

Acceptance (plus G1–G6, P1–P5, V1–V3, W1–W6):
- **I1** Every verified employer row traces to an SEC submission file and either an unambiguous concordance row set or a reviewed decision. A test fails if a sector is set without one.
- **I2** No sector is assigned to Waymo, Zoox, the four rejected collisions, or the 7389/7340 registrants.
- **I3** The Industries tab renders verified sectors with honest below-floor counts at 1280 light, 1280 dark and 390, with no console errors (W3 checker extended).
- **J1** Robots Disallow, the per-host rate, the page cap, the size cap, the same-site redirect and the aggregator denylist each have a rejecting test.
- **J2** Byte-identical projection when the same capture is projected twice, with `jsonld` boards included.
- **J3** PROGRESS.md documents the exact operator commands for the probe and the capture. Pi does not run live retrieval.

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

## M10: Extraction that lists skills, not company text

Why: on the union release, HR's top ten nodes are Unum company history, pay disclaimers and "may perform other duties as assigned". Growth's top ten are sentences from one Brex posting pair. AE ranks 10–19 are single-employer sentences. The map must list what employers ask a person to do or know.

Build, offline and deterministic (no model, no new hosts):

1. **Non-skill filter (`rules/3`).** Drop a candidate bullet or sentence before it becomes a node when it is:
   - about the company (founding, headcount, offices, headquarters, brands, "we are", "our mission", "at <employer>");
   - pay, benefits, plan terms, bonus or equity disclaimers;
   - EEO, accommodation, background-check, visa or legal text;
   - catch-alls ("other duties as assigned", "this list is not exhaustive");
   - a heading echo or a label line ("company:", "location:").
   Use a versioned, reviewed phrase table in `role_atlas_mappings/`, not inline regexes. Count every drop by reason per role, and keep the dropped text in the out-of-tree audit only.
2. **Split compound requirements.** A requirements line with several clauses ("high school diploma, ged … at least 7 years of …") becomes separate candidates for education, years of experience and domain. Years and education become structured fields on the posting (`min_years`, `education`), not skill nodes.
3. **Employer-weighted ranking.** Rank and overview order use the number of distinct employers first, then the employer-weighted share (each employer counts once, split across its postings). Posting share stays visible as a second number. Status thresholds and floors do not change.
4. **Derived sample note.** The sample descriptor comes from the admitted employers' verified sectors (e.g. "mostly Information and Finance"). Without verified sectors, it says "sector mix unverified". Drop the fixed "tech-heavy".

Acceptance:
- **X1** On the union capture, no HR, Growth or AE top-20 node (by the new rank) matches the non-skill table. The report lists each role's new top 20.
- **X2** The drop counts by reason are reported per role. No admitted posting loses all its expectations to the filter without being counted.
- **X3** Golden tests from real shapes: the Unum HR lines are dropped; the compound education line splits; a real duty ("Interpret and explain HR policies") survives.
- **X4** Re-publish as a new release `2026-Q4-union-r3`, selected, with double projection byte-identical, budgets held and W3 passing. The previous releases are not edited (supersession goes in the README).
- G1–G6 and P1–P5 still hold. One commit per milestone part is fine; PROGRESS.md updated; stop and report.

## M11: Group HR and Growth sentences into shared skills

Why: after M10, HR and Growth nodes are single-employer sentences, so the map shows nothing even where employers ask for the same work in different words (performance management, org design, employee relations; lifecycle, experimentation, attribution). AE works because its sentences map to O*NET DWAs.

Build, offline and deterministic:
1. Extend the reviewed DWA rule table to HR Generalist (O*NET 13-1071.00 and 11-3121.00 DWAs) and Growth Manager (11-2021.00, 13-1161.00 DWAs), with the same lexical-rule, provenance and fit rules used for AE. Every rule cites the DWA id and O*NET 31.0.
2. Add a small reviewed `emerging_terms` set for Growth and HR terms with no DWA (e.g. experimentation / A/B testing, lifecycle marketing, attribution, HRIS, people analytics). Same contract as the existing emerging terms.
3. Fix the `ba` leak: bare degree tokens (BA, BS, MBA, bachelor's, master's) become `education`, never nodes. Test it.
4. Re-publish as `2026-Q4-union-r4`, selected; earlier releases untouched; double projection identical; budgets and W3 hold.

Acceptance:
- **Y1** HR and Growth each have at least 8 grouped nodes with ≥ 3 employers, or the report shows, per candidate group, why not (list the matched sentences and employers).
- **Y2** Every grouped node links to its exact source quotes; no rule matches company, pay or legal text (the M10 table still runs first).
- **Y3** AE and FDE top 20 do not regress (report both before/after).
- **Y4** Report each role's new top 20.
