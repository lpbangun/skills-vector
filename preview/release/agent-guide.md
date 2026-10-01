# Skills Vector market reference — agent guide

Read-only occupational evidence for three roles (HR Generalist, Growth Manager with
product-growth/growth-marketing/sales-account-executive variants, Account Executive).
Foundations, advertised demand, and learning priorities are published as separate,
clearly labeled datasets. No market-prevalence or trend claims are made; the sample
scope and denominators are always explicit. Every published quote/excerpt is
attributed to the exact retrieved response that contains it: `sources.json` marks
`job-board` listing records (`retrieval_kind: listing`) that carry the sampling
denominators, and per-posting `job-posting-detail` records (`retrieval_kind: detail`,
`parent_source_id`) that back posting text and quotes. Learning priorities cite
agent-selected recorded claim ids (`evidence_claim_ids`) with a short rationale.
Admitted postings carry the agent's scope decision (`work_level`, `work_level_reason`):
only literal `individual_contributor` postings with a grounded rationale and no
people-management evidence are admitted, and every scope rejection is recorded in
`exclusions.json`.

Base URL: `<deployment base url>`

## HTTP

- `GET /api/health`
- `GET /api/release`
- `GET /api/occupations`
- `GET /api/occupation?slug=hr-generalist`
- `GET /api/query?q=onboarding&occupation=hr-generalist&limit=5`
- `GET /api/claim?id=<claim_id>`
- `GET /api/evidence?section=extracts|admissions|exclusions|mappings|disagreements|lineage`
- `GET /api/research-plan?q=<question>` (bounded local handoff; never launches anything)
- `GET /api/schema`, `GET /api/agent-guide`

Verified citations: `/release/citations/<claim_id>.json` (write-once; a claim URL
keeps byte-identical content across later releases). The current release pointer is
`/release/current.json`; immutable release directories live under
`/release/releases/<release_id>/`.

## MCP (stdio JSON-RPC)

Run `skills-vector market mcp --base-url <deployment base url>` (or `--release-root <dir>` locally).
Supported tools: `release_info`, `list_occupations`, `get_occupation`, `search_evidence`,
`get_claim`, `inspect_evidence`, `research_plan`. Tools call the same core as the API,
so claim ids, counts, and citation URLs are identical across surfaces.

## Honest answers

`search_evidence` returns one of:

- `cited_evidence` — stored, quote-verified claims match; the answer text is a selection
  of stored statements with citation URLs.
- `insufficient_evidence` — in scope, but no stored claim clears the citation threshold.
- `unsupported_question` — outside the covered occupational scope.

The latter two include a `handoff` plan: a bounded, local-only research request an
operator may run with the subscription agent runtime. Public surfaces never launch
research, never mutate state, and never substitute fixture content.

## Operator commands (local only)

```sh
env PYTHONPATH=src python3 -m skills_vector market browse --json
env PYTHONPATH=src python3 -m skills_vector market query "onboarding" --occupation hr-generalist --json
env PYTHONPATH=src python3 -m skills_vector market research probe \
    --evidence-root /path/to/evidence --overlay /path/to/runtime-overlay.yml
env PYTHONPATH=src python3 -m skills_vector market research run --occupation hr-generalist \
    --evidence-root /path/to/evidence --overlay /path/to/runtime-overlay.yml
env PYTHONPATH=src python3 -m skills_vector market publish --slice <slice-dir> --release-root preview/release
env PYTHONPATH=src python3 -m skills_vector market serve --port 8787
env PYTHONPATH=src python3 -m skills_vector market mcp --base-url <deployment base url>
```

Research runs are bounded and serialized: at most 60 allowlisted retrievals, 12 model calls,
2 MiB per response, and at most two infrastructure retries. The runtime overlay is supplied
externally (fallback disabled); every call's session record must resolve the pinned provider/model
with ``resolvedModelIsFallback: false`` or the run fails closed and the last good release stays served.
