# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the owner of a private local occupational-intelligence workspace. They investigate how work is changing for three People Operations & Talent roles, inspect the evidence and workflow that produced a brief, and make the final approval decision.

## Product Purpose

Skills Vector turns a bounded, source-backed investigation into an inspectable role brief. Success means the owner can trace a material claim through evidence and worker findings, understand uncertainty or failures, and approve only a sufficiently validated private draft.

## Positioning

The product combines a controlled investigation DAG, explicit evidence provenance, persisted run history, and a mandatory human approval gate. Its mechanism is inspectability from run to source, not autonomous publication or opaque prediction.

## Operating Context

The product runs locally with FastAPI, SQLite, deterministic LangGraph handlers, a CLI, an API, and a browser dashboard. It monitors HR Coordinator, Recruiter, and Learning & Development Specialist in the United States. Investigations may continue after recoverable worker failures and may produce visibly partial drafts.

## Capabilities and Constraints

- The default run view is Brief & approval.
- The workflow graph distinguishes logical stages from worker invocations.
- A partial draft is approvable only when every required claim still passes validation; otherwise it remains inspectable but blocked from approval.
- Findings expose structured summaries by default and an explicitly expandable raw payload.
- Evidence is clustered with a deterministic controlled taxonomy and ranked with explainable reason chips.
- The initial evidence-explorer design target is 250 sources per run and its stress fixture is 500.
- Publisher-quality tiers may be manually curated later; they are not inferred or used by the initial ranking.
- The deterministic offline build remains the baseline. External models, APIs, credentials, scraping, scheduling, deployment, and high-scale agents are outside the current build.

## Evidence on Hand

The repository contains a checked, source-backed corpus covering five approved research lenses, deterministic findings for all three roles, persisted run and approval state, and automated tests for evidence gating, graph execution, persistence, HTTP behavior, and three-role acceptance.

## Product Principles

- Preserve a complete trace from run to source.
- Keep uncertainty, disagreement, missing evidence, and failures visible.
- Prefer deterministic, explainable behavior before introducing external intelligence.
- Continue independent work when a branch fails without inventing unsupported material.
- Keep judgment and approval explicitly human-controlled.
