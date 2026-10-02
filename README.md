# Skills Vector · Evidence Atlas

A source-grounded occupational brief for four registered U.S. roles: HR Generalist,
Growth Manager, Account Executive, and provisional Forward Deployed Engineer (FDE).
Published evidence is an immutable sample, not a market census, prevalence estimate,
proficiency measure, hiring outcome, or individual career prediction.

The historical release tree remains immutable and may contain only its original
three roles. FDE is a mission-authorized pilot profile; it is not human-reviewed,
not an official O*NET occupation, and has no findings until admissible source evidence
exists. O*NET 15-1252.00 is only a partial task-level anchor. Solutions/sales engineering,
customer success/implementation, and general software engineering are not equivalent
by title.

## Product surfaces

- **Brief first.** Global evidence search and “Use with your agent” onboarding precede
  role selection. Published roles open with concise cited findings and raw-count
  charts; aggregate tables, underlying observations, and provenance expand on demand.
  Chart tables accept keyboard focus for horizontal scrolling. The global connection
  guide provides a selectable, copyable SDK snippet using the origin serving the page.
  Provisional or unpublished roles remain separate from findings.
- **Refinement and comparison.** Filter admitted postings by work level,
  responsibility band, advertised experience, employer/customer context, and distinct
  expectation dimensions/bases. Unknown remains a first-class filter. Comparisons show
  admitted counts and observed dimensions only—never fabricated coverage percentages
  or personal fit scores.
- **One catalog core.** `CatalogStore` powers the REST reads, preview, CLI, and MCP
  tools. Search/refinement are deterministic reads; they never call a model.
- **Read-only MCP.** The maintained Python MCP SDK serves stdio for local hosts and
  stateless Streamable HTTP at `/api/mcp`. The tools return the same catalog results as
  the API. No public API or MCP tool starts research or publication.
- **Pinned components.** Every supported chart has a deterministic component id,
  definition version, release pin, scoped dates, raw numerator/denominator,
  calculation, underlying observations, source links, and limitations. The copyable
  `component_id@release_id` reference survives subsequent catalog updates.
- **Local operator research.** Only local commands may retrieve public sources or call
  DeepInfra. Research is bounded and budget-reserved; only a separate fresh
  independent adjudication receipt can authorize candidate materialization or local
  publication. No model key is bundled into Vercel.

## Evidence and classification boundaries

Board listings define their retrieved sampling frame. Per-posting detail responses
are separate lineage sources; incomplete or truncated listing responses are never
silently treated as complete. Published material includes rights-safe metadata and
short source-verbatim excerpts only; raw responses and model/tool receipts remain in
the external evidence root.

Candidate allocation (`title-prioritized-employer-bucket-round-robin/3`) applies
canonical-title, other title-hinted, and unhinted tiers globally. Within each tier,
employer/provisional-bucket coverage precedes round-robin remainders; Growth variant
buckets stay distinct. Titles prioritize bounded retrieval only, never admission,
work level, or responsibility. A changed sampling method does not establish a trend.

Each posting distinguishes work level (`individual_contributor`, `people_manager`, or
`unknown`) from responsibility band (`early_career`, `independent_ic`,
`senior_strategic_ic`, `people_management`, or `unknown`). `people_management`
requires source-backed management evidence; senior strategic IC is not management.
Neither dimension is inferred from title or advertised years. Years remain verbatim
experience wording. Context dimensions and expectations (task, capability, tool,
knowledge, experience, contextual expectation, demonstration, credential), evidence
basis, and explicit proficiency wording stay
separate; unknown is preserved rather than dropped. `identity_id` groups the
normalized exact wording within its dimension, independently of basis;
`expectation_id` includes basis, and `relationship_id` identifies its specific
posting/source edge. No synonyms or semantic equivalence are inferred.
Counts refer to the admitted source sample at its recorded date. They are not market
prevalence, importance, proficiency, hires, or employability. No trend claim is made
without comparable retrieval windows; model agreement is not independent evidence.
Producer and publication gates share assertion-shaped honesty rules: unsupported
market percentages, market-wide employer claims, and directional trends are rejected.
An explicit small-sample disclaimer does not become a contradiction merely because
it uses the word “prevalence.”

New artifacts use `market-release/2`: `role_scope.responsibility_scope` replaces the
old forced mid-level `seniority` scope. It describes inclusion, not an inferred
classification. Historical `market-release/1` artifacts remain immutable and readable,
with their original scope; broader bands or contexts are supported only by new
source-backed records, never by relabeling the old sample.

## Install and run locally

Python 3.12 is used for the isolated feature environment. `uv sync` installs the
locked runtime and CLI dependencies.

```sh
uv sync
uv run skills-vector market serve --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/`. The local server uses the same ASGI REST/MCP app as the
Vercel function. Useful local reads:

```sh
uv run skills-vector market browse
uv run skills-vector market query "employee relations" --occupation hr-generalist
uv run skills-vector market search "employee relations" --occupation hr-generalist
uv run skills-vector market refine --occupation hr-generalist --responsibility-band people_management
# Set IDENTITY_ID to an observed identity_id from refinement or comparison output.
uv run skills-vector market refine --occupation hr-generalist --expectation-identity "$IDENTITY_ID"
uv run skills-vector market compare hr-generalist account-executive
uv run skills-vector market occupation hr-generalist
# Set these values from occupation output's component_refs.
uv run skills-vector market component "$COMPONENT_ID" --release "$RELEASE_ID"
uv run skills-vector market evidence --section admissions --occupation hr-generalist
uv run skills-vector market mcp --release-root preview/release
```

REST search/refinement endpoints:

```sh
curl 'http://127.0.0.1:8787/api/brief'
curl 'http://127.0.0.1:8787/api/search?q=employee+relations'
curl 'http://127.0.0.1:8787/api/refine?occupation=hr-generalist&work_level=people_manager&responsibility_band=people_management'
# Set IDENTITY_ID to an observed identity_id from refinement or comparison output.
curl --get 'http://127.0.0.1:8787/api/refine' \
  --data-urlencode 'occupation=hr-generalist' \
  --data-urlencode "expectation_identity=$IDENTITY_ID"
curl 'http://127.0.0.1:8787/api/compare?occupation=hr-generalist&occupation=account-executive'
```

MCP clients can use stdio with `uv run skills-vector market mcp --release-root
preview/release`, or connect directly to `http://127.0.0.1:8787/api/mcp` with the
maintained `mcp` SDK. MCP is stateless Streamable HTTP and read-only.

The stable guide is `/api/agent-guide`, also linked globally above role selection.
It renders the originating host's actual connection address and a runnable Python
`mcp` 2.x session script (tested with 2.2.0). The UI's copy action includes its
`asyncio.run(main())` entry point. `get_occupation` returns `component_refs`; MCP `get_component`
accepts `component_id` and optional `release_id`. HTTP reads use
`/api/component?id=<returned-id>&release=<returned-release>`. MCP evidence tools
accept bounded `release_id` pins; REST uses `release` where documented. Omitting a
pin selects current, not the release of a previously saved reference. Personal
assessment stays in the user's own harness: absent personal evidence means “not
demonstrated,” never “lacks skill.”

## Research, refresh, and local publication

All operator configuration, credentials, price receipts, durable mission-budget data,
raw responses, and receipts live outside this worktree. The external JSON config
contains the exact DeepInfra model pins and price-receipt hash, never an API key. Supply
`DEEPINFRA_API_KEY` through the operator environment only after the authoritative
ledger shows the mission reservation. Missing credentials or reservations must stop
research; there is no OMP provider or fixture fallback.

```sh
export SV_RESEARCH_CONFIG=/path/outside/worktree/research-config.json
export SV_EVIDENCE_ROOT=/path/outside/worktree/evidence
export SV_REFRESH_POLICY=/path/outside/worktree/refresh-policy.json
export SV_STAGE_RETENTION=/path/outside/worktree/evidence/stage-retention.json
export SV_REFRESH_CHECKPOINT=/path/outside/worktree/refresh/checkpoint.json

uv run skills-vector market research budget --config "$SV_RESEARCH_CONFIG"
uv run skills-vector market research probe --config "$SV_RESEARCH_CONFIG" \
  --evidence-root "$SV_EVIDENCE_ROOT" --model-role primary
uv run skills-vector market research probe --config "$SV_RESEARCH_CONFIG" \
  --evidence-root "$SV_EVIDENCE_ROOT" --model-role challenger
uv run skills-vector market research probe --config "$SV_RESEARCH_CONFIG" \
  --evidence-root "$SV_EVIDENCE_ROOT" --model-role escalation

# Reconcile only when an original provider response is already retained for this exact attempt.
uv run skills-vector market research reconcile --config "$SV_RESEARCH_CONFIG" \
  --evidence-root "$SV_EVIDENCE_ROOT" --run-id "$SV_RUN_ID" \
  --attempt-id "$SV_ATTEMPT_ID" --response "$SV_RETAINED_RESPONSE"

# Experimental comparisons freeze both A/B arms; no publication occurs here.
uv run skills-vector market research run --config "$SV_RESEARCH_CONFIG" \
  --occupation hr-generalist --evidence-root "$SV_EVIDENCE_ROOT" \
  --min-postings 3 --max-postings 5 --release-root preview/release

# Routine refresh consumes the fresh independent four-role retention decision,
# uses the same reserved mission budget, and installs no recurring job.
uv run skills-vector market research refresh --once --config "$SV_RESEARCH_CONFIG" \
  --policy "$SV_REFRESH_POLICY" --retention-record "$SV_STAGE_RETENTION" \
  --evidence-root "$SV_EVIDENCE_ROOT" --checkpoint "$SV_REFRESH_CHECKPOINT" \
  --release-root preview/release

# `refresh-policy.json` is external and has exactly these bounded controls:
# {"schema_version":"skills-vector-operator-refresh/1","cadence_hours":24,
#  "min_postings":3,"max_postings":5,"max_boards":16}
# The retention record schema is skills-vector-four-role-stage-retention/1 and
# binds each role's comparison/adjudication paths, hashes, reviewer and A/B metrics.

# These require an independent, fresh adjudication of the exact comparison hash.
uv run skills-vector market candidate --comparison <comparison.json> \
  --adjudication <independent-adjudication.json> --out <candidate-dir>
uv run skills-vector market publish --comparison <comparison.json> \
  --adjudication <independent-adjudication.json> --candidate-out <candidate-dir> \
  --release-root preview/release
uv run skills-vector market validate <candidate-dir>
uv run skills-vector market research budget --config "$SV_RESEARCH_CONFIG"
uv run skills-vector market research settle --config "$SV_RESEARCH_CONFIG"
```

`research run` freezes primary-only and primary-plus-challenge arms over the same
passages and does not publish. The fresh stage-retention record makes the frozen
four-role decision (primary-only or primary-plus-challenge) from independently
adjudicated per-role receipts; refresh follows that stage rather than making a new
per-role stage choice. A primary-only refresh executes no phantom challenger pass.
Routine refresh uses one shared mission budget and bounded caps for all four fixed
roles. It re-fetches job-board listings and may reuse only recent allowlisted official
foundation responses while retaining their original retrieval time and hash. Existing
role identity and material evidence definitions must remain unchanged; citations,
rights, schema, source quotes, known provider costs and contradiction gates must all
pass before an automatic release pointer update. New registrations, identity/taxonomy
changes, major unresolved contradictions, thin candidates, or unknown cost leave the
last-good release serving. Automatic refresh is labeled not human-reviewed; FDE pilot
registration is not a human review. A currently unresolved challenge finding shipped
under a retained challenger stage remains explicitly labeled unresolved.

The command requires `--once`; it creates no cron, gateway, deployment, or public
mutation path. Use the cadence checkpoint to decide when an operator may run it again.
Never reset the durable budget to retry.

Each new publication includes immutable `changes.json` beside its manifest. It
records the previous release, added/removed/updated claim, posting, and source IDs
and counts, plus the recorded sampling and expectation-method definitions before
and after the update. The release API and header expose this artifact only when it
exists; historical releases are not backfilled or rewritten.

Automatic-release metadata records the actual frozen policy fingerprint, cadence,
posting minimum/maximum and board cap. Its minimum-posting rule describes the
all-or-nothing guard: any insufficient role blocks the candidate and preserves the
last-good catalog. The policy comes from the selected immutable release, not from
the latest pointer when a consumer asks for an older release.

## Vercel preview assembly

```sh
bash scripts/assemble_vercel.sh
```

This assembles `.vercel-out` for review. It is not a deployment or promotion command.
The Vercel function exposes the REST and `/api/mcp` reads over one ASGI app and the
bundled immutable release. Deployment, aliasing, access changes, and production
promotion are owned separately; do not run `vercel deploy` from this implementation
workflow.

The authorized four-role mission preview is
<https://skills-vector-four-role-atlas-preview-wiredwriters-projects.vercel.app/>.
Its catalog-wide connection is `/api/mcp`; the stable guide is `/api/agent-guide`.
This dedicated preview alias can move between explicitly authorized preview
deployments while release-pinned citations and components retain their original
data. It is not a production promotion or a recurring refresh service.

## Design system and project history

Every product UI follows **Evidence Atlas · Control** in [`DESIGN.md`](DESIGN.md),
with tokens/primitives in `design-concepts/design-system/` and the reference in
`design-concepts/app.html`. Historical archived explorations are not product UI.

Earlier occupational-slice and structured-job-analysis experiments remain documented
as history; they are not active market inference or a fallback path.

- [Market architecture](docs/architecture.md)
- [Current model and budget notes](docs/models.md)
- [Structured Job Analysis POC (historical)](docs/structured-job-analysis-poc.md)
- [Jobsss boundary](docs/jobsss.md)
