# Skills Vector

Evidence-based occupational skills reference and local assessment contract for **U.S. startup workers**, especially people adopting AI.

Initial market: U.S. startups. Three families — engineering/AI, product/design, go-to-market/operations. This repository’s first vertical slice covers three contrasting pilots:

| Occupation ID | Family | O*NET baseline |
| --- | --- | --- |
| `occ_founding_engineer` | engineering/AI | 15-1252.00 Software Developers |
| `occ_product_manager` | product/design | 15-1299.09 IT Project Managers |
| `occ_growth_operator` | go-to-market/operations | 13-1161.00 Market Research Analysts |

Public beta still targets **30 reviewed occupations** (about ten per family). That gate is **not** met by this slice. Individuals are the first audience. [Jobsss](docs/jobsss.md) retains private profiles.

## Market reference lane (current product surface)

`src/skills_vector/market/` implements the current lane: bounded live research over
public occupational foundations and employer job boards, published as immutable
versioned releases served by a read-only API.

- **Research (local authority only).** `market research run` executes the pipeline
  `discovery → retrieval → candidate selection → admission → reconciliation →
  evidence linking → challenge → release` with the pinned subscription task model,
  fixed retrieval and model-call caps, an allowlist of public source hosts,
  byte-verbatim quote verification and no fallback. Long research never runs
  inside a Vercel request.
- **Quote provenance is the fetched response, not a listing index.** Board
  listing responses are the population records (enumerated postings, board
  denominators). When a listing carries no description text, the per-posting
  detail response becomes its own source record (original detail URL, response
  hash, retrieved_at, byte count, rights, parent board id) and the posting,
  claim verification and published extract cite that response. Detail sources
  never inflate boards attempted/used, sampled postings or employer counts, and
  only short verified excerpts are published — never full job descriptions.
- **Learning priorities cite agent-selected claims.** A separate bounded linking
  pass selects the recorded claim ids (with a short rationale) that support each
  priority; the deterministic validator only enforces identity/role/basis/variant
  discipline, drops unknown, other-role, wrong-basis or cross-variant ids, and
  drops a priority whose links do not survive. Lexical overlap is never treated
  as evidence linking.
- **Scope admission is explicit and fail-closed.** The admission pass must
  classify each posting's work level (`individual_contributor` | `people_manager`
  | `unknown`) with a grounded rationale, and must copy a byte-verbatim
  people-management quote when the posting owns direct reports — including
  manager postings that also mention quota or account ownership. Only literal
  `individual_contributor` decisions with a nonempty rationale and no
  people-management evidence are admitted; manager ownership, `unknown`, and
  missing/malformed decisions are excluded. Reasons are recorded in
  `exclusions.json`, with category counts in run lineage. Failed description
  retrievals cannot be admitted from a title alone. A `Manager` title alone is never treated as
  people-management evidence, and advising or coordinating colleagues is not
  direct-report ownership. Long descriptions are windowed around
  responsibilities/duties headings within the existing 1800-character per-posting
  prompt bound so scope duties are not hidden behind company boilerplate, and
  release validation rejects any admitted posting without a clean, typed
  individual-contributor decision or referring to an excluded source.
- **Candidate caps are allocated fairly.** One candidate per employer per
  provisional bucket first (growth variants before generic buckets), then a
  round-robin remainder, so a large or alphabetically early employer cannot
  monopolize the candidate/detail budget; cap exclusions and per-bucket outcomes
  are reported honestly, and a missing required growth bucket triggers the same
  bounded discovery-feedback pass as low total yield.
  Initial discovery and replacement feedback share the same total board-attempt
  ceiling; exhausted board capacity stops feedback before another model call.
- **Published releases.** Validated slices are merged into `preview/release/releases/<release_id>/`
  and pointed to atomically by `preview/release/current.json`; claim citations are
  write-once. `/api/release` identifies the current live-researched occupations
  and immutable versions; an empty release tree returns an honest `no_release`
  catalog rather than fixtures. Release identities cover supporting evidence
  datasets and verified extracts, including run lineage.
- **Read surfaces.** One catalog/query core backs the frontend, the deployed read
  API (`/api/*`), the CLI (`market browse|query|occupation|claim|evidence|release-info`)
  and the MCP stdio tools. If stored evidence cannot answer a question, the
  response is `insufficient_evidence`/`unsupported_question` plus a bounded
  research handoff plan — never a fabricated answer.
- **Role inspection.** Role pages show the sampling date and scope. The Control
  contents rail moves keyboard focus to the selected section without replacing
  the role route. The skip link focuses main content without losing a role or
  claim route. Evidence lists and source links wrap within the viewport rather
  than widening the page. Run lineage records the bounded agent stages and
  resource counters; full runtime receipts remain in the external evidence root.
  The provenance graph uses stage-specific recorded counts, not coverage:
  board targets, retrieval requests, candidate postings, admissions, mapping
  rows, claims, and challenge findings. Mapping counts and inspector reads are
  pinned to the role page's release; absent counts say `not recorded`, not zero.
  Role filters are initialized before routing, including direct role, claim,
  and query links; a deep link does not silently reduce scoped searches to cross-role.
  Inspector previews state the displayed/total row counts and link to complete
  versioned JSON datasets, source registries with extract paths, and file hashes.
  The extracts tab also exposes the complete bounded role-extract JSON response.

### Source rights, sampling and learning caveats

- Postings are retrieved from public employer job-board APIs; only rights-safe
  metadata, verified short excerpts, decisions, mappings and lineage are
  published. Full raw responses stay in the external evidence root and are never
  committed or served. Every published quote/excerpt is attributed to the exact
  response that contains it (`sources.json` `retrieval_kind`/`parent_source_id`
  distinguishes a compact listing from the per-posting detail response).
- Counts describe only the postings enumerated in the retrieval window for one
  role and date; boards report their enumerated/total bound in `sources.json`
  when an API pages or caps its listing. Counts are **not** market prevalence,
  and posting frequency is not importance, proficiency, hires or employability.
- Occupational foundations, advertised demand, and recommended learning
  priorities stay distinct. Learning priorities are analyst recommendations
  derived from cited claims, ordered by the reconciliation pass and linked to
  agent-selected recorded claim ids with a stated rationale and explicitly
  bounded uncertainty — not measured importance, not lexical keyword matching,
  and not practitioner validation.
- No trend claims without comparable paired retrieval windows; agent agreement is
  not practitioner validation. No credentials belong on Vercel: the deployment is
  a read-only static release tree plus a stdlib Python function that never calls a
  model.

### Legal/rights note

Official foundation pages are quoted with attribution (O*NET OnLine CC BY 4.0;
BLS public domain). Employer postings are published as short attributed excerpts
only.

## Legacy experiments (historical)

The earlier occupational-slice work (`skills_vector.domain`, `operating_loop`,
SQLite catalog, DeepInfra budget ledger, structured-job-analysis POC) remains
in-tree as the previous experiment; it defaults to deterministic stubs and its
one-shot live permissions are spent. Historical sealed run artifacts under
`outputs/` and `docs/structured-job-analysis-poc.md` stay as history. Production
market research **never** substitutes stubs or fixtures.

## Preview on Vercel (do not merge first)

A PR preview is enough to look at the desk. **Do not merge to `main` just to deploy.**
Keep production untouched until a catalog is reviewed.

1. The preview deployment serves `preview/index.html` plus the read API in
   `api/index.py` (Python serverless function, stdlib only).
2. `vercel.json` runs `bash scripts/assemble_vercel.sh`, which copies the static
   preview and the committed `preview/release/` tree beside it. Vercel ignores
   memory settings for active-CPU billing, so none are configured.
3. `.python-version` pins the minor line (`3.12`); a patch pin (`3.12.3`) can
   select a build Vercel does not offer, while the minor pin still resolves
   portability across machines.
4. Preview deployments may require a per-deployment protection bypass
   (`/aliases/<deployment-id>/protection-bypass`); project-wide or production
   access settings are not changed.
5. The deployed function is read-only: mutation methods return 405, no secrets or
   model calls exist there, and browsing the empty catalog is the honest state
   until a real release is published from the VPS/agent runtime.

## Local run

Python 3.11+. Copy `.env.example` locally only if you add keys for legacy lanes; do not commit keys.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
```

Read surfaces against the local release tree (no models, no network):

```bash
env PYTHONPATH=src python3 -m skills_vector market browse
env PYTHONPATH=src python3 -m skills_vector market query "what does an HR generalist do?"
env PYTHONPATH=src python3 -m skills_vector market occupation hr-generalist
env PYTHONPATH=src python3 -m skills_vector market evidence --section exclusions --occupation hr-generalist
env PYTHONPATH=src python3 -m skills_vector market serve --port 8787
env PYTHONPATH=src python3 -m skills_vector market mcp --print-tools
```

Bounded live research (subscription agent runtime; writes immutable run evidence
outside the repo and only publishes when every precondition passes):

```bash
env PYTHONPATH=src python3 -m skills_vector market research probe \
  --evidence-root ~/.local/share/skills-vector-evidence \
  --overlay <runtime-overlay.yml>

env PYTHONPATH=src python3 -m skills_vector market research run \
  --occupation hr-generalist \
  --evidence-root ~/.local/share/skills-vector-evidence \
  --release-root preview/release \
  --overlay <runtime-overlay.yml>
```

Failed or thin runs write receipts and keep the last good release untouched
(`--no-publish` validates only). One mutating research worker at a time; the run
is not a Vercel request.

## Design system

UI work follows **Evidence Atlas · Control** in [`DESIGN.md`](DESIGN.md). The Vercel frontend and API read validated published JSON; research still runs locally. `design-concepts/app.html` remains the design reference, not a shipped specimen/fixture route.

## Docs

- [Market architecture](docs/architecture.md)
- [Structured Job Analysis POC (historical)](docs/structured-job-analysis-poc.md)
- [Models and budget (legacy lane)](docs/models.md)
- [Jobsss boundary](docs/jobsss.md)
- [Goal loop](docs/goal-loop.md)