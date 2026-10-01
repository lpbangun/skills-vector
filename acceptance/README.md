# Acceptance protocol (completion.v2)

Executable, evidence-first acceptance for `BENCHMARK.md` (round-zero freeze,
`benchmark.round-zero.v1`). It collects and **verifies** evidence for gates
HG01..HG18; it is **not** a scoring engine. Advisor judgment per category (§4
anchors) always prevails; the utilities frame what evidence must exist, re-verify
it (hashes, live HTTP, structural checks), and never auto-pass.

**This is the corrected version of the round-zero protocol** (see
`round-zero-protocol-completion/PROTOCOL-REPORT.md` externally). It supersedes
the original round-zero acceptance executor, whose HG03/HG05/HG06 wiring,
HG10 stub, HG18 list-manifest silent pass, and presence-only receipt trust made
several mandatory gates non-executable or fail-open. Every gate of
`BENCHMARK.md` §5 is preserved unchanged; only the proof machinery is stricter.

## Layout

```
acceptance/
  README.md            <- this file
  config.schema.json   <- JSON Schema for the acceptance config (completion.v2)
  config.json          <- slate config; parent/implementer fills candidate/deploy/evidence before verdicts
  gates.py             <- CLI: `python3 acceptance/gates.py gate HGxx|all|summary`
  check_release.py     <- CLI: `python3 acceptance/check_release.py <release_dir>`; structural HG03/04/05/06 checks
  journeys.py          <- CLI: browser-driving HG10 evidence collector (playwright)
  evidence/            <- per-gate artifacts + receipts (created by runs)
```

## Verdict semantics and exit codes

- Gate verdicts: `PASS | FAIL | BLOCKED | UNKNOWN` (never "pass with caveats").
- Missing/absent evidence ⇒ `UNKNOWN`; evidence present but wrong/tampered ⇒
  `FAIL`; externally unavailable prerequisite configured as
  `deploy.unauthorized=true` ⇒ `BLOCKED` for deployed gates (HG07/08/10/12/13/15).
- `gates.py all` exit codes: **0** all gates PASS · **1** any FAIL/BLOCKED ·
  **2** any UNKNOWN (and no FAIL/BLOCKED) · **2** on config errors.
- `config.json` without `"protocol_version": "completion.v2"` is refused
  (prevents running the stale pre-completion slate against the corrected gates).
- Receipts land at `acceptance/evidence/<GATE_ID>/receipt.json`
  (`HG02-<slug>` per occupation).

## Config contract (what parent/implementer fills)

Full schema: `config.schema.json`. Slate values (`""`/`null`/`[]`) keep gates
UNKNOWN. Highlights:

- `candidate`: `commit_sha` (40-hex, must equal worktree HEAD at verdict time),
  `benchmark_copy_sha256` (sha256 of the frozen `BENCHMARK.md`),
  `acceptance_copy_sha256` (sha256 of `acceptance/gates.py`; must equal the
  frozen corrected copy under `paths.protocol_completion_dir`).
- `deploy`: `preview_base_url`, `deployment_id`, plus `unauthorized` (set only
  when preview authority/linkage is impossible — deployed gates then BLOCKED).
- `paths.dump_dir`: external hash-pinned dump for the round's verdict artifacts
  (HG18 verifies). `paths.protocol_completion_dir`: this protocol's frozen copy.
  `paths.venv_python`: isolated env python for HG16 (baseline suite) and the
  journeys driver fallback.
- `occupations`: pre-filled (HR Generalist; Growth Manager with
  product-growth/growth-marketing/sales-account-executive; Account Executive).

## Artifact schemas (what the evidence producers must emit)

### HG01 — candidate freeze file (`gates.hg01.provenance_receipt`, JSON)

```
{
  "candidate_commit_sha": "<40-hex>",
  "hashes": {"benchmark_copy_sha256": "...", "acceptance_copy_sha256": "..."},
  "deployment": {"preview_base_url": "...", "deployment_id": "...",
                 "deployment_url": "...",
                 "deployment_receipt": {"path": "...", "sha256": "..."}},
  "output": {"release_id": "...", "output_receipt": {"path": "...", "sha256": "..."}}
}
```
The gate re-verifies HEAD, both frozen dumps (hash manifests), and every
referenced file hash. Deployment/output receipt files must exist with matching
sha256.

### HG02 — live research run receipt (per occupation)

```
{
  "occupation": "<slug>",
  "provider": "...", "model": "...", "model_fallback": false,
  "started_at": "...", "finished_at": "...",
  "execution_context": "agent-runtime" | "vps" | "local-runtime",
  "sampling": {"geography": "...", "seniority": "...", "denominators": {...}},
  "artifacts": {"<name>": {"path": "...", "sha256": "..."}},
  "session_records": [{"path": "...", "sha256": "..."}],
  "fixtures_used": false,
  "discovery": [...], "retrieval": [...], "extraction": [...],
  "reconciliation": [...], "challenge": [...]
}
```
Every `{path, sha256}` reference is re-hashed by the gate; each
`session_records` file must actually contain the declared provider and model
strings (runtime-record cross-check). `execution_context` must never be a
long-held Vercel request (HG12 also fails any HG02 receipt declaring
`vercel-request`).

### Release directory (HG03/04/05/06 — `check_release.py`)

Files may be `.json` (array/object) or `.jsonl`. Contract and required fields
are in the `check_release.py` module docstring: manifest (release_id,
generated_at, agent_attribution, role_scope, frequency_caveat, skip_rules,
sampling_scope), occupations (role_scope bounds, growth variants, dedup stats
with denominators), sources (url, retrieved_at, sha256, rights, inclusion,
exclusion_reason, role_scope, optional extract_path+extract_sha256), postings
(occupation_slug, source_id, employer, url, dedup_key), claims (id, statement,
source_ids, optional quote — verified byte-verbatim against the cited source's
stored extract), requirements (rationale, uncertainty, evidence_claim_ids that
must exist, search_terms that must appear in recorded evidence). Dedup counts
declared in `stats` are recomputed from postings and must match exactly.
Findings are printed with gate tags; `gates.py` maps a tagged finding to FAIL
for that gate and an advisory to UNKNOWN (never pass).

### HG07 — inspector + advisor receipt (`evidence/HG07/advisor-inspection.json`)

Configured endpoint must return the configured required_sections keys (default
extracts/admissions/exclusions/mappings/disagreements/lineage); advisor
receipt: `{inspected_url, verdict:"PASS", sections_checked:[...], artifacts:[{path,sha256}]}`.

### HG08 — live API

`gates.hg08.endpoints[]`: `{path, kind: "json"|"html", expect_status,
must_contain: [...], must_not_contain: [...]}`. Bodies are fetched live;
JSON endpoints must parse; declared markers verified (case-insensitive);
response sha256 recorded in the receipt. Use honest markers for negative-path
endpoints (e.g. `unsupported_question`, `insufficient_evidence`) with
`must_not_contain: ["fixture"]`.

### HG09 — independent agent consumption (`gates.hg09.receipt_path`)

```
{
  "consumer": {"identity": "...", "kind": "agent", "session_record": {path, sha256}},
  "cli": {"command": "...", "exit_code": 0, "output": {path, sha256}},   # output must parse as JSON
  "mcp": {"tools": [{"tool": "...", "exit_code": 0, "result": {path, sha256}}]},  # results must parse as JSON
  "verdict": "PASS"
}
```
`consumer.identity` must not match `excluded_identities` (parent/implementer/
reviewer models — parent orchestrator, task role, and advisor are pre-listed in
the slate).

### HG10 — browser journeys (`journeys.py` + gate)

```
python3 acceptance/journeys.py --config acceptance/config.json \
    [--base <deployed-url>] [--out acceptance/evidence/HG10] [--journey NAME]
```
Exit 0 journeys PASS · 1 step failure · 2 config error · 3 no usable driver
(receipt names the exact prerequisite; never fabricates journeys). Config:
`gates.hg10.journeys[]` = `{name, steps[]}` with step types
`goto|click|fill|press|wait|assert_text|source`, each step carrying its semantic
`stage` (`browse|query|finding|inspect|source`); every journey must cover all
five stages in order. The driver records per-step screenshots (hashed), a HAR,
and `journey-receipts.json` (verdict, step URLs, artifacts, sha256s).
The gate then verifies: receipts exist; each journey's `base_url` equals the
configured `deploy.preview_base_url` (stale/other-surface evidence fails);
driver verdict PASS; five-stage chain in order; every artifact exists with
matching sha256, ≥1024 bytes, valid PNG magic; HAR parses; the `source` stage
lands OFF the deployed host. Finally `evidence/HG10/advisor-inspection.json`
(`{inspected_url, verdict:"PASS", journeys_checked:[...], artifacts:[{path,sha256}]}`)
is required — absent ⇒ UNKNOWN.

### HG11 — citation survival through second update

`gates.hg11`: second-update receipt (HG02 schema plus `first_release_id`,
`second_release_id`, which must differ), `cited_claim_url`,
`expected_claim_id`, and `pre_update_content_sha256` — the sha256 of the cited
claim body recorded BEFORE the second update. The gate re-fetches the URL live
and requires byte-identical content (sha256 equality), so the citation must
survive the update, not merely the URL.

### HG12 — mutations/inputs/bounds (executed live)

`gates.hg12.mutation_endpoints[]`: each receives an unauthenticated POST
`{"op":"declare-pass"}`; any status < 400 fails. `validation_negatives[]`:
`{endpoint, payload | payload_over_bytes, expect_status(>=400)}` — oversized
bodies are generated from `max_payload_bytes`. Any HG02 receipt declaring
`execution_context: "vercel-request"` also fails.

### HG13 — honesty suite

`unsupported`/`insufficient`: `{path, honest_markers}` — probed live (HTTP 200,
honest marker present, no `fixture` substring). `retrieval_failure`/`outage`:
`{receipt_path, must_contain}` with receipts
`{scenario, trigger, observed:{http_status?, body:{path,sha256}, fixture_used:false}, honest:true}`;
the referenced failure body must exist (hash-verified) and contain the declared
explicit-failure markers. `last_good`: `{url, content_sha256}` — re-fetched
live; after a failure the last-good release must still serve byte-identical
content.

### HG14 — secrets/safety

Heuristic token scan over the candidate tree (ignore dirs in the tool). Optional
`production_host_patterns[]`: `preview_base_url` host and any recorded HG12
mutation traffic must not match. Required attestation
`evidence/HG14/no-production-mutation.json`:
`{"declared_by": "...", "production_mutations_performed": 0, "checked_at": "..."}`;
absent ⇒ UNKNOWN.

### HG15 — a11y/fidelity

Static part live on the deployed HTML: `required_html_markers` (default
`["<html lang", "skip"]`). Manual part `evidence/HG15/advisor-manual-verdict.json`:

```
{"inspected_url": "...", "verdict": "PASS",
 "dimensions": {"keyboard_navigation": {"checked": true, "passed": true, "notes": "..."},
                "visible_focus": {...}, "reduced_motion": {...},
                "responsive_behavior": {...}, "skip_link": {...},
                "design_fidelity": {...}},
 "recording_artifacts": [{"path": "...", "sha256": "..."}]}
```
All six dimensions must be explicitly checked and passed; recordings
hash-verified.

### HG16 — baseline suite (executed live by the gate)

Runs `python -m unittest discover -s tests -v` in the worktree via
`paths.venv_python` (venv prerequisite: `uv venv .venv && uv pip install
--python .venv/bin/python -e .`), parses test counts, records a receipt.
`gates.hg16.minimum_tests` sets a count floor carried from the previous verdict;
`allow_removed_tests` + `removed_incidental_tests[{path, reason}]` permits
deleting incidental wording/wiring tests under developer rules without repinning
fixture IDs — the declared removal count must match the list, and behavioral
regression coverage must stay above the floor.

### HG17 — advisor verdict artifact

`gates.hg17.verdict_artifact_paths[]`; each must be

```
{"role": "advisor", "advisor_pin": "<same as review.advisor_pin>",
 "candidate_commit_sha": "<candidate sha>",
 "categories": {<all seven category keys>: <int 0..10>},
 "executed_commands": [{"command": "...", "exit_code": 0}],
 "browser_output_artifacts": [{path, sha256}],
 "session_records": [{path, sha256}],   # runtime records proving the pinned model
 "verdict": "..."}
```
Category keys: research_evidence_quality, market_intelligence_usefulness,
learning_priority_usefulness, frontend_inspection_ux,
search_api_agent_interoperability, deployed_e2e_functionality,
resilience_security_provenance. `review.fresh_context_session_ids` must list the
fresh pinned advisor session(s); runtime records must contain the pinned model
(and thinking level marker). No worker self-scores may be fed to the advisor
(BENCHMARK §7; procedural — the parent must not attach them).

### HG18 — verdict provenance dump

`paths.dump_dir` manifest verified strictly: accepts
`files: [{path, sha256}, ...]` (frozen round-zero shape) and
`files: {rel: sha256}`; zero entries, missing files, or any hash mismatch fail.
The dump's `BENCHMARK.md` entry must match `candidate.benchmark_copy_sha256`;
`gates.hg18.required_dump_entries` (default `["provenance"]`) must appear among
dump paths once verdict artifacts are frozen (absent ⇒ UNKNOWN until then).

## Runnable command sequence (per verdict)

```bash
cd <worktree>
# 1. fill acceptance/config.json (candidate/deploy/evidence paths), then:
python3 acceptance/gates.py all                    # drives every gate; exit 0/1/2
python3 acceptance/gates.py gate HG02              # single gate
python3 acceptance/check_release.py <release_dir>  # standalone structural view
python3 acceptance/journeys.py --config acceptance/config.json   # HG10 driver
python3 acceptance/gates.py summary                # read receipts only (exit 0)
```

## Principles

- Executable protocol collects and re-verifies evidence; it never relaxes or
  auto-passes. High-level anchors in BENCHMARK.md §4 prevail; gates that need
  inspection (HG07, HG10, HG15, HG17) additionally require the advisor
  inspection/verdict receipts and stay UNKNOWN without them.
- `acceptance/` and `BENCHMARK.md` are not product code; `BENCHMARK.md` is
  frozen and immutable. This corrected protocol is frozen (hash-pinned) at
  `round-zero-protocol-completion/` externally; never overwrite the historical
  `round-zero/` frozen copy.
- Reviewer never edits product code. One mutating worker at a time.
- Where BENCHMARK.md wording conflicts, BENCHMARK.md prevails.
