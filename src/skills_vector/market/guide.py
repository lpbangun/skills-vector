"""Agent guide and machine-readable API schema for the market reference core."""

from __future__ import annotations

from typing import Any

from . import API_SCHEMA_VERSION, CORE_VERSION, RELEASE_SCHEMA_VERSION
from .core import HANDOFF_STAGES, INSPECTOR_SECTIONS
from .limits import RESEARCH_LIMITS
from .hosts import ALLOWED_SOURCE_HOSTS


def api_schema(*, base_url: str = "") -> dict[str, Any]:
    base = base_url.rstrip("/")
    return {
        "schema_version": API_SCHEMA_VERSION,
        "core_version": CORE_VERSION,
        "release_schema": RELEASE_SCHEMA_VERSION,
        "base_url": base or None,
        "read_only": True,
        "mutation_methods": "rejected (405) on every /api path",
        "bounds": {
            "max_query_length": 400,
            "max_limit": 200,
            "max_body_bytes": 8192,
            "no_inference": "queries select stored claims; no model calls happen in the API",
        },
        "endpoints": [
            {"path": "/api/health", "returns": "release pointer status and occupation slugs"},
            {"path": "/api/release", "params": ["release?"], "returns": "release metadata, scope, versions"},
            {"path": "/api/occupations", "returns": "role list with stats and scope"},
            {"path": "/api/occupation", "params": ["slug"], "returns": "role bundle: foundations, demand, learning priorities, claims, lineage"},
            {"path": "/api/query", "params": ["q", "occupation?", "limit?", "release?"], "returns": "answer envelope with status cited_evidence|insufficient_evidence|unsupported_question"},
            {"path": "/api/claim", "params": ["id", "release?"], "returns": "claim, quote verification, sources, citation url"},
            {"path": "/api/citation", "params": ["id"], "returns": "immutable citation document"},
            {"path": "/api/evidence", "params": ["section", "occupation?", "limit?", "no_text?"], "returns": "inspector rows", "sections": [*INSPECTOR_SECTIONS, "all"]},
            {"path": "/api/inspector", "params": ["occupation?", "limit?", "no_text?"], "returns": "all inspector sections in one document"},
            {"path": "/api/research-plan", "params": ["q", "occupation?"], "returns": "bounded local research handoff plan (never launches research)"},
            {"path": "/api/schema", "returns": "this document"},
            {"path": "/api/agent-guide", "returns": "agent guide markdown"},
        ],
        "query_status_semantics": {
            "cited_evidence": "one or more stored, quote-verified claims match the question; answer text is selected stored statements",
            "insufficient_evidence": "the question is in scope but no stored claim clears the citation threshold",
            "unsupported_question": "the question falls outside the covered occupational scope",
        },
        "citation_path_pattern": "/release/citations/<claim_id>.json",
        "release_path_pattern": "/release/releases/<release_id>/manifest.json",
        "research": {
            "launched_by_public_api": False,
            "operator_only": True,
            "handoff_kind": "bounded_research_request",
            "handoff_stages": list(HANDOFF_STAGES),
            "limits": {
                "max_retrievals": RESEARCH_LIMITS["max_retrievals"],
                "max_model_calls": RESEARCH_LIMITS["max_model_calls"],
                "max_response_bytes": RESEARCH_LIMITS["max_response_bytes"],
                "serialized_calls": RESEARCH_LIMITS["serialized_calls"],
            },
            "allowlisted_hosts": list(ALLOWED_SOURCE_HOSTS),
        },
    }


def agent_guide_markdown(*, base_url: str = "") -> str:
    base = (base_url.rstrip("/") if base_url else "") or "<deployment base url>"
    return f"""# Skills Vector market reference — agent guide

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

Base URL: `{base}`

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

Run `skills-vector market mcp --base-url {base}` (or `--release-root <dir>` locally).
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
env PYTHONPATH=src python3 -m skills_vector market research probe \\
    --evidence-root /path/to/evidence --overlay /path/to/runtime-overlay.yml
env PYTHONPATH=src python3 -m skills_vector market research run --occupation hr-generalist \\
    --evidence-root /path/to/evidence --overlay /path/to/runtime-overlay.yml
env PYTHONPATH=src python3 -m skills_vector market publish --slice <slice-dir> --release-root preview/release
env PYTHONPATH=src python3 -m skills_vector market serve --port 8787
env PYTHONPATH=src python3 -m skills_vector market mcp --base-url {base}
```

Research runs are bounded and serialized: at most 60 allowlisted retrievals, 12 model calls,
2 MiB per response, and at most two infrastructure retries. The runtime overlay is supplied
externally (fallback disabled); every call's session record must resolve the pinned provider/model
with ``resolvedModelIsFallback: false`` or the run fails closed and the last good release stays served.
"""


__all__ = ["agent_guide_markdown", "api_schema"]
