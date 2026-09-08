# Skills Vector Roadmap

This is the handoff plan for continuing Skills Vector from a VPS. The project is a private-first occupational intelligence application for U.S. People Operations & Talent roles.

## Current state

Implemented in the local integration branch:

- Typed domain rules for HR Coordinator, Recruiter, and Learning & Development Specialist.
- LangGraph investigation loop: scope → plan → research → validate → analyze → forecast → draft → human gate.
- SQLite runs, artifacts, events, evidence backlog, fingerprint deduplication, and durable human-review decisions.
- Deterministic offline runtime and an optional Cursor Composer adapter.
- Generic role runner, FastAPI API, CLI, same-origin Evidence Atlas controls, and API/browser tests.
- 29 automated tests pass; CLI, packaging, desktop browser, and 390px mobile checks pass.

The local API integration is currently uncommitted relative to `origin/main`. No VPS, ChatGPT Site, or other public deployment has been made.

## Next: preserve and publish the local slice

1. Review the scoped integration diff and keep pre-existing `data/`, design exploration assets, `.claude/`, and local databases out of the application commit unless they are intentionally needed.
2. Merge the integration branch through a pull request.
3. On the VPS, clone the branch/merge result, create a fresh Python 3.11+ environment, and install with `uv pip install -e '.[test]'`.

## Phase 1 — VPS foundation

- Add a deployment unit: systemd service or Docker Compose, plus Nginx/Caddy TLS termination.
- Bind Uvicorn internally; expose only the reverse proxy publicly.
- Configure persistent `SKILLS_VECTOR_DATABASE` storage and scheduled SQLite backups.
- Set `SKILLS_VECTOR_ENV=production` and an explicit runtime (`stub` for a dry run or `composer` for live work).
- Keep `CURSOR_API_KEY` server-side; never place it in `.env` committed to Git or in browser code.
- Add `/health` monitoring and a documented restore procedure.

Acceptance: the VPS serves `/`, `/docs`, and `/health`; a private run survives a process restart; no secrets appear in Git or browser responses.

## Phase 2 — Production API hardening

- Add authentication and authorization before exposing the API beyond localhost.
- Add request limits, structured logging, correlation IDs, and safe error responses.
- Add schema migrations/versioning instead of relying only on additive table creation.
- Move long investigations to a durable background-job queue; return a run ID immediately.
- Add concurrency/retry handling for SQLite or migrate to Postgres when multiple workers are needed.

Acceptance: unauthorized callers cannot read briefs or submit reviews; duplicate submissions are safe; failed jobs are observable and recoverable.

## Phase 3 — Real evidence and provider integration

- Install and explicitly configure the Cursor SDK only on the server.
- Replace fixture evidence with approved public retrieval connectors.
- Enforce source freshness, role-lens coverage, URL retention, and provenance on every live run.
- Add provider tests for malformed JSON, timeouts, partial evidence, invalid categories, and rate limits.

Acceptance: a live run can be reproduced from stored source metadata and never silently falls back to fixture data in production.

## Phase 4 — Complete product surface

- Populate the full Evidence Atlas from stored brief/artifact data rather than reference copy.
- Add true review continuation for `changes_requested` and `rejected` decisions.
- Render event progress, claims, scenarios, disagreement notes, and source ledger from the API.
- Add accessibility, keyboard, responsive, and browser E2E coverage for launch → inspect → review → reload.

Acceptance: every visible claim is traceable to the selected run; a reviewer can resume after a restart; the UI never presents reference content as live evidence.

## Phase 5 — Operating discipline

- Add CI for unit/API/browser tests, compile checks, packaging, and security scanning.
- Add retention/export rules for private briefs and review audit trails.
- Document weekly scout operation, backup rotation, upgrades, and rollback.
- Decide whether the MVP stays SQLite/single-node or moves to Postgres plus a worker service.

## VPS launch checklist

- [ ] Integration branch merged and tagged.
- [ ] VPS firewall exposes only SSH and HTTPS.
- [ ] Reverse proxy and TLS configured.
- [ ] Production environment variables installed outside the repository.
- [ ] Persistent database path and backup job verified.
- [ ] Authentication enabled before public access.
- [ ] `SKILLS_VECTOR_RUNTIME` explicitly selected.
- [ ] Health check, logs, restart, and rollback tested.
- [ ] First live run reviewed privately before any broader use.
