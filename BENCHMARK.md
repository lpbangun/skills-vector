# BENCHMARK — Skills Vector · Agent Market Reference POC (round-zero freeze)

*Version: benchmark.round-zero.v1 · Frozen: 2026-09-30 by pinned advisor `opencode-go/glm-5.3-flash:max`*

This document is the frozen acceptance benchmark for the round defined by
`/home/logani/oprun-evidence/skills-vector-agent-market-reference/goal.txt` (the contract of record).
It is a hash-pinned freeze: the immutable copy and manifest live at
`/home/logani/oprun-evidence/skills-vector-agent-market-reference/round-zero/` (see §10).
The goal contract, not this summary, prevails on any wording conflict; the seven
categories, the ≥9/10 threshold, the hard gates and the loop contract are unchanged.

Authoring notes (binding for how this file may be used):

- This benchmark was authored **independently** by the pinned advisor. No worker
  scores, worker narratives or persuasion artifacts were available or consumed.
- The goal contract forbids invoking a goal-prompting skill. This benchmark was
  authored without any such invocation.
- Scoring anchors are **high-level outcome anchors**, not implementation choices:
  the product may satisfy them through any coherent architecture. Executable
  acceptance utilities in `acceptance/` collect evidence; they do not prescribe
  that architecture and are not a substitute for advisor judgment.
- No product code may be edited before this freeze is registered; product edits
  happen only inside implementation rounds after freeze (§8).

---

## 1. Runtime pinning and role verification

Verified from runtime records, not self-assertion (recorded at
`/home/logani/oprun-evidence/skills-vector-agent-market-reference/round-zero-sessions/2026-09-30T18-52-24-789Z_01a0f3a9-2955-774c-8628-1be4bb2d180d.jsonl`):

- Session `model_change` record: `model: "opencode-go/glm-5.3-flash"`,
  `resolvedModelIsFallback: false`; `thinking_level_change`: `max`.
- Session `cwd` is this worktree; the session is the authoring context of this
  benchmark. This is the "actual model/provider using runtime records" evidence.

Binding role table (from `goal.txt` + `advisor-role-transition.json`):

| Role | Binding | Notes |
| --- | --- | --- |
| Advisor (benchmark owner, independent reviewer) | `opencode-go/glm-5.3-flash:max` | Verified by runtime record above. The previous `cursor/claude-opus-4-7-high:high` advisor **failed authentication and is prohibited**; infrastructure attempts are exhausted (2 of 2 unchanged retries used, per `MORNING-HANDOFF.md`). |
| Parent orchestrator | `openai-codex/gpt-6.1-sol:medium` | Verifiable in parent runtime records at each verdict. |
| Task implementer (mutating worker) | `opencode-go/deepseek-v4.1-flash:max` | One mutating worker at a time; parallel read-only research allowed. |

Constraints binding all participants:

- No automatic model/provider substitution; no fallback models. The reviewer
  MUST verify actual resolved provider/model in runtime records for every
  verdict; catalog presence alone is not proof.
- The advisor/reviewer works in a **fresh task/review context explicitly pinned
  to the advisor model** for each verdict; passive advisor notes never count as
  acceptance.
- The reviewer may author acceptance artifacts and run tests,**must not edit
  product code**, and must not receive worker self-scores or persuasion content.
- No secret reads/prints/imports; only existing credential injection.
- No paid metered services, no new subscriptions, no raised budget ceilings, no
  purchases without explicit user approval.
- No production promotion, no shared production backend mutation, no merge to
  `main`, no history rewrite. Feature-branch commits/pushes are allowed.
- A denied operation stops its lane; never bypass via another tool or wrapper.
- Scalars from the historical POC (`docs/structured-job-analysis-poc.md`, the
  one-shot live-final DeepInfra permission) are historical only; not authorization.

---

## 2. Candidate definition and immutability

A **candidate** is exactly one repository `HEAD` commit SHA reachable from this
worktree's branch (`poc/agent-market-reference`), plus the deployed preview
deployment built from that commit, plus the runtime artifacts (run receipts,
release publications, deploy receipts, review receipts) produced against it.

Verdict rule: **a single candidate SHA** must simultaneously achieve (a) all
seven category scores ≥9 and (b) all hard gates `PASS` (§5). Read-only evidence
accumulated earlier on the same SHA remains valid. Publication of a *second
release* (research cycle) inside the same candidate is a product operation, not
a new candidate.

## 3. Scoring scale

Each category is scored 0–10 (integers) by the independent advisor:

- **0–3** core substance absent or wrong;
- **4–6** partial, material gaps;
- **7** works but with named material reservations;
- **8** complete with minor residual risk;
- **9** complete, negligible residual risk — **threshold**;
- **10** exemplary; no material residual risk identified.

Missing evidence is `UNKNOWN`, never pass. A category cannot score ≥9 while a
hard gate feeding that category is `BLOCKED`/`UNKNOWN`. The threshold is not
relaxable without explicit user authorization in a new round zero.

## 4. Categories and ≥9 anchors

### C1. research/evidence quality
≥9 requires: live agentic research runs for all three occupations
(HR Generalist, Growth Manager [product-growth/growth-marketing/sales-AE
variants kept distinct], Account Executive) executed on the agent runtime, with
run receipts recording discovery→retrieval→extraction→reconciliation→challenge
lineage, actual provider/model, sampling, timestamps, hashes; real public
occupational sources + employer postings (URLs, dates, hashes, rights, parser
version); publishable supporting data (extracts, admissions/exclusions,
mappings, disagreements, run/release lineage) complete and coherent; every
published quote byte-verifiable; agent-authored synthesis clearly attributed
(not presented as practitioner-validated); no synthetic/fixture content in
product releases (fixtures only in labeled negative/edge tests).

### C2. market-intelligence usefulness
≥9 requires: explicitly bounded geography + seniority; separate
product-growth vs growth-marketing vs sales-AE variants without title
conflation; deduplicated posting/employer counts with sampling scope and
denominators; honest insufficiency statements where evidence is thin; no
prevalence claims on insufficient evidence, no trend claims without comparable
periods, explicit "posting frequency ≠ importance/proficiency/hire" labeling;
insights usable by the target persona (what to watch, what skills recur,
where the demand concentrates) without retreat to genericities.

### C3. learning-priority usefulness
≥9 requires: every recommended learning priority links to specific cited
evidence from the product's own release (no invented topic); the priority
ordering carries an explicit rationale and is labeled as evidence-based
priority, not measured importance/proficiency; keeps occupational foundations,
advertised demand, and learning recommendations **distinct**; supports the
persona's "what to learn next" question with honestly bounded confidence.

### C4. frontend/inspection UX and design fidelity
≥9 requires: extends the existing Evidence Atlas · Control surface (DESIGN.md,
tokens.css, components.css, canonical `design-concepts/app.html`), no redesign
or replacement; occupation pages, cross-role query/search, cited answers,
evidence-linked learning priorities, and an evidence inspector exposing all
publishable supporting data, exclusions, disagreements, mappings and run
lineage; keyboard accessibility, reduced motion honored, responsive behavior,
visible focus, skip link, no side-tab accent borders, semantic color rules
(no teal/amber), no fake coverage percentages; visual inspection done by the
advisor on the deployed surface (screenshots and/or live browser checks), not
local-only.

### C5. search/API/agent interoperability
≥9 requires: one catalog/query core backs frontend, deployed read API,
CLI/JSON, and agent tools (MCP); identical claim/release identifiers and
content across surfaces; MCP tools realistically consumable by an *independent
agent context* (not the parent, not the implementation worker, not the
reviewer); a machine-readable query API with stable output shape; negative
paths (unsupported question, insufficient evidence) return honest structured
responses, not fixtures or fabricated fallbacks.

### C6. deployed e2e functionality
≥9 requires: a real, queryable, deployed API on the Vercel preview of the
existing project (not a static-only site and not a local-only smoke) exercised
end-to-end during this run's journey: browse → query → finding → inspect
evidence → original source, each step functional on the deployed URL; publish
a second update and verify stable claim/release citation URLs survive with
their content (citation survival through second update, including links and
citable content); last good release remains served if research/update fails.
Preview authority/linkage that cannot be established marks that gate
`BLOCKED`, never pass.

### C7. resilience/security/provenance
≥9 requires: mutation endpoints protected (no unauthenticated mutations on the
deployed preview), inputs validated, resources bounded (no long-held Vercel
request for research, bounded payloads), secrets never exposed (no printed
key material, shipped artifact scan clean); evidence-availability honesty:
unsupported question → declared-honest response; retrieval failure → explicit
failure without fabrication/fallback; runtime outage → explicit error not
silent fixture substitution; last-good release preserved; provenance recorded
for candidate/input/config/output/deployment at each verdict and preserved in
external dump (outside repo).

## 5. Hard gates (mandatory, executable)

All gates below are measured on the same frozen candidate via the executable
acceptance protocol in `acceptance/` (`README.md`, `gates.py`, `config.json`,
`config.schema.json`). Gate verdicts are `PASS | FAIL | BLOCKED | UNKNOWN`
(never "pass with caveats"). Any `FAIL`/`BLOCKED`/`UNKNOWN` hard gate blocks a
passing overall verdict.

| ID | Gate | What proves it | Executable evidence (scoring) |
| --- | --- | --- | --- |
| HG01 | Candidate freeze | Provenance freeze file exists for the verdict: worktree commit SHA, decorated input/config/output/deployment hashes, and benchmark/frozen-copy hashes. | `acceptance/gates.py` gate `HG01`; artifacts under `acceptance/evidence/HG01/`. |
| HG02 | Research executed live for all three occupations | Run receipts for the three occupations exist with runtime-record provider/model/sampling/timestamp/hash fields; no fixture substitution; no silent fallback. | gates `HG02a/HG02b/HG02c`; artifacts under `acceptance/evidence/HG02*/`. |
| HG03 | Source/evidence provenance preserved | For each occupation release, every recorded source has URL, retrieval date, content hash, rights marker, inclusion/exclusion and role-scope record. | `acceptance/check_release.py`. |
| HG04 | Growth variants distinct | Evidence distinguishes product-growth, growth-marketing and sales-AE; geography + seniority bounded on role page. | `acceptance/gates.py` gate `HG04`. |
| HG05 | Denominator-labeled median demand counts | Deduplicated postings/employers and denominators/sampling scope reported; prevalence claims only when evidence threshold met; no banned prevalence/trend violations. | `acceptance/check_release.py` + `gates.py` `HG05`. |
| HG06 | Learning priorities evidence-linked | Every learning priority maps to ≥1 recorded claim; no goal-invented search terms; honest uncertain skip rules. | `acceptance/check_release.py`. |
| HG07 | Evidence inspector complete | Inspector surfaces all publishable supporting data (extracts, admissions/exclusions, mappings, disagreements, run/release lineage) via deployed endpoint or artifact bundle. | `gates.py` `HG07` + advisor visual check. |
| HG08 | Deployed live API | Real, queryable deployed read API exercised (not static-only, not local-only), via config-declared endpoint(s); negative/insufficient paths honest. | `gates.py` `HG08` (requires `deploy.preview_base_url` set). |
| HG09 | CLI/JSON + MCP consumption by independent agent | CLI JSON output and MCP tool set are machine-consumable. An independent agent context (neither parent, implementer, nor reviewer) consumes CLI/API via MCP successfully; receipt recorded. | `gates.py` `HG09`; receipts at `acceptance/evidence/HG09/`. |
| HG10 | Deployed browser journeys | For each of the four journeys: browse → query → finding → inspect evidence → original source; completion recorded as artifact (screenshots/trace/HAR) from the deployed URL. | `gates.py` `HG10` + advisor browser inspection. |
| HG11 | Citation survival through second update | A second research release is published for the same occupation; a claim cited in the first release remains resolvable at a stable URL with identical claim content; the receiving consumer can dereference the citation without the first release still being "current" (must survive an update). | `gates.py` `HG11`; artifacts under `acceptance/evidence/HG11/`. |
| HG12 | Mutation endpoints protected; inputs validated; resources bounded | Unauthenticated mutation on deployed endpoint is rejected; validated/bounded payloads enforced (negative tests executed); no long-held Vercel request used for research. | `gates.py` `HG12`; receipts at `acceptance/evidence/HG12/`. |
| HG13 | Failed-retrieval honesty & outage resilience | Evidence-availability negative suite passes: unsupported question → honest answer (no fixtures), retrieval failure path → explicit failure not fabricated content, runtime outage path → explicit error not silent fixture substitution, last-good release preserved after failure. | `gates.py` `HG13`. |
| HG14 | Secrets and safety | No secret material in shipped artifacts or traffic; code/tooling has no secret-print path (verified by scan receipts); no production mutation performed. | `gates.py` `HG14`. |
| HG15 | A11y/fidelity on deployed surface (visual gate, advisor) | Keyboard navigation, visible focus, reduced motion, responsive behavior, skip-link, and design-fidelity anchors verified by advisor against deployed URL; recording artifacts retained. | `gates.py` `HG15` (static checks) + advisor manual verdict recorded in review receipt. |
| HG16 | Baseline test suite tolerant | `python -m unittest discover -s tests -v` (repo baseline) passes on the candidate (existing tests must remain green; new tests may coexist but not be reduced). | `gates.py` `HG16` or equivalent run receipt. |
| HG17 | Advisor verdict artifact | An independent advisor review exists at the pinned advisor model for this candidate; verdict includes per-category scores, executed commands and exit codes, and inspected browser output; receipt is protected from persuasion (no worker scores fed). | `gates.py` `HG17` + external receipt path from config. |
| HG18 | Provenance of verdicts | All verdict artifacts (receipts, browser recordings, deploy id, config hashes, candidate SHA) are recorded and hash-pinned in the external dump; match the frozen benchmark copy. | `gates.py` `HG18`. |

`BLOCKED` semantics: a gate whose required prerequisite is externally
unavailable (e.g. Vercel preview authority/linkage, subscription capability
denial) is marked `BLOCKED` with the exact boundary and prerequisite; it is
never marked passed. `UNKNOWN` semantics: evidence was not produced, retained,
or is ambiguous; gate counts as not passed. Missing evidence is never
interpreted as pass.

Gates are mapped to scoring anchors in `acceptance/README.md` per gate. High-
level anchors (§4) always apply; the executable protocol is evidence
collection supporting those anchors, not a replacement.

## 6. Simple scoring rubric per category

Every category is scored against its §4 anchors using only artifacts that
survive the adjudication freeze. Typical observations:

- All ≥9 anchors demonstrably satisfied + no material finding → **9**.
- All ≥9 anchors satisfied plus exemplary provenance/UX/usability depth ->
  consider **10**.
- One named material reservation that doesn't break a hard gate → cap **8**.
- More than one named material reservation → cap **7**.
- Any fixture substitution, fabricated quote, silent fallback, prevalence or
  trend violation, or conflated variant → **0–4** for the affected
  categories regardless of polish elsewhere.
- Missing evidence → `UNKNOWN` (never interpolated upward).

## 7. Verdict independence

- Advisor works in a fresh pinned context per verdict; consumes no worker
  self-scores, no worker persuasion, no narrative claims as evidence.
- Reviewer may author acceptance artifacts and run tests; must not edit
  product code.
- Reviewer grounds findings only in executed commands, exit codes, receipts,
  and inspected deployed output.
- Parent supplies no category scores of its own and may not influence scoring
  beyond orchestration facts (what was run, what failed at infrastructure
  level).

## 8. Loop contract (unchanged from goal)

- Maximum **five implementation/review rounds**; ordinary test/fix cycles
  allowed within a round; stop at first all-≥9 + all-gates-pass.
- Two infrastructure retries per failing gate on unchanged code; no counter
  resets and no benchmark relaxation.
- Preserved resumable checkpoints and external immutable evidence at
  `/home/logani/oprun-evidence/skills-vector-agent-market-reference/`.
- No benchmark change except a new round-zero freeze authorized by the user.
- Round 0 (this round): freeze artifacts only. Implementation rounds 1..5
  may edit product code; each verdict is on one frozen candidate SHA.

## 9. Required artifact and repository rules (round zero binding)

- Repo-root `BENCHMARK.md` (this file) is the in-repo canonical copy; the frozen
  authoritative copy lives outside the repo (§10).
- `acceptance/` contains the executable protocol: `README.md`,
  `config.schema.json`, `config.json` (slate for future fills),
  `gates.py`, `check_release.py`.
- No product code/CI/infra/doc edits were made in round zero.
- `acceptance/evidence/<GATE_ID>/` is the destination for future artifacts;
  the external dump under `round-zero/` is hash-pinned afterward by the advisor
  of each round.
- Existing baseline suite command remains authoritative:
  `python -m unittest discover -s tests -v`.

## 10. Frozen authoritative copies and hashes

- Authoritative benchmark copy: `/home/logani/oprun-evidence/skills-vector-agent-market-reference/round-zero/BENCHMARK.md`.
- Authoritative acceptance protocol copy: `/home/logani/oprun-evidence/skills-vector-agent-market-reference/round-zero/acceptance/`.
- Manifest with SHA-256 per file: `/home/logani/oprun-evidence/skills-vector-agent-market-reference/round-zero/manifest.json` (see PROVENANCE file for details).
- Round-zero input evidence: goal.txt, advisor-role-transition.json,
  round-zero-current-advisor.md, round-zero-advisor-prompt.md,
  MORNING-HANDOFF.md and runtime session record are copied under
  `round-zero/input/` for immutability.
