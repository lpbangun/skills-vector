# Skills Vector

Skills Vector is a private local occupational-intelligence dashboard for the U.S. **People Operations & Talent** domain. It deep-monitors exactly three roles: HR Coordinator, Recruiter, and Learning & Development Specialist.

The runnable MVP uses deterministic, credential-free LangGraph handlers and SQLite. It does not call a model, scrape the web, or publish anything. The owner can launch an investigation, inspect persisted research/validation/analysis/forecast artifacts, review the evidence-linked brief, and explicitly approve it in the browser or CLI.

## Install and initialize

Python 3.11+ is required. From this repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/skills-vector init
```

The last command creates `data/skills_vector.db`, seeds the three roles, and validates normalized source-backed evidence. Set `SKILLS_VECTOR_DB` or pass `--db PATH` before the command to use another database.

## Start the dashboard

```bash
.venv/bin/skills-vector serve --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Choose a role and select **Launch investigation**. The run workspace opens on **Brief & approval** and also provides:

- **Workflow graph** — the 15 logical stages, 14 deterministic worker invocations, status, timing, finding/source counts, failures, and expandable raw payloads.
- **Evidence explorer** — claim clusters, research/analysis filters, explainable source-ranking reasons, connected findings, and paginated source detail.

Every material claim has **Trace support**, which opens the matching evidence cluster. Enter a reviewer name and select **Approve brief** only after inspection. Stop the server with `Ctrl+C`; reopening it uses the same persisted state.

A recoverable branch failure can produce a visible partial draft. A partial draft remains approvable only when every required claim still validates; otherwise it is inspectable with status `partial_blocked` and the approval action is unavailable.

## Run, inspect, and approve from the CLI

Run a real investigation:

```bash
.venv/bin/skills-vector investigate recruiter
```

Inspect the latest private draft in full:

```bash
.venv/bin/skills-vector inspect --latest recruiter
```

The inspected JSON includes the normalized trace summary and stage, invocation, finding, claim, and source relationships. The browser loads evidence progressively through the run trace and evidence API routes rather than flattening every source into the initial view.

Explicitly approve that draft:

```bash
.venv/bin/skills-vector approve --latest recruiter --reviewer "Local Owner" --note "Evidence reviewed"
```

The role values are `hr_coordinator`, `recruiter`, and `learning_and_development_specialist`. A new investigation creates the next brief version and preserves older runs.

## Verification

Run unit, integration, HTTP, graph, persistence, and three-role acceptance checks:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
.venv/bin/skills-vector --db data/acceptance.db acceptance --fresh
```

The final command runs and approves HR Coordinator, Recruiter, then Learning & Development Specialist consecutively and reopens the repository after each case to prove persistence. It uses a dedicated local verification database.

## Evidence and workflow boundaries

Every bundled evidence record retains a real public URL, publication date, approved category, relevant paraphrased claim, provenance, and role connection. The five approved lenses are public labor data, research papers, credible reports, selected job-posting signals, and official policy. Validation rejects incomplete records, unapproved categories, missing references, and unsupported material claims.

The executed graph fans out those five research lenses, merges and validates evidence, fans out demand/task/skill/role-durability/skeptic analysis, aggregates a bounded two-year forecast, drafts the brief, and pauses at a mandatory human gate. The persisted trace contract is `run → stage → invocation → finding → claim → source snapshot`; logical stages remain distinct from worker attempts. UI approval is a separate explicit persisted action; it never publishes.

## Intentional production deferrals

No deployment, Vercel configuration, managed Postgres, VPS worker, scheduler, real-time scraping, credentials, paid APIs, public publishing, job-board features, prediction markets, individual predictions, or self-modifying prompts/policy/router behavior are implemented. The repository boundary is separable so a later production version can replace SQLite with managed Postgres without changing the product contract.
