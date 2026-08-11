# Skills Vector

Skills Vector is a private-first occupational intelligence MVP for the U.S. People Operations & Talent domain. The first monitored roles are deliberately fixed to:

- HR Coordinator
- Recruiter
- Learning & Development Specialist

The operating loop persists run events, artifacts, and an evidence backlog; plans a bounded subset of approved research lenses; drafts a private role brief; and pauses before human review. Research and analysis use an injected agent-runtime adapter. Offline work defaults to the deterministic stub; Composer 2.5 through the optional Cursor Python SDK is selected only when the SDK and `CURSOR_API_KEY` are already available.

## Design system

UI work follows **Evidence Atlas · Control** ("The Evidence Desk"), locked in [`DESIGN.md`](DESIGN.md).

| Asset | Role |
| --- | --- |
| [`DESIGN.md`](DESIGN.md) | System of record for agents and humans |
| [`design-concepts/design-system/tokens.css`](design-concepts/design-system/tokens.css) | CSS custom properties |
| [`design-concepts/design-system/components.css`](design-concepts/design-system/components.css) | Shared primitives (buttons, callouts) |
| [`design-concepts/app.html`](design-concepts/app.html) | Canonical product surface |

Future screens must use these tokens and rules (no teal/amber clinical palette, no side-tab accents). Explorations live under `design-concepts/archive/` and are not product UI.

## Local verification

Python 3.11 or newer is required.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
```

LangGraph is a runtime dependency, but the default test and local paths are offline and make no model or network calls. The graph factory accepts narrow, injected handlers, and `AgentRuntime` owns provider selection. Weekly scout ingestion only queues evidence candidates; it cannot write claims or mutate brief privacy.

## Private Recruiter loop

```python
from datetime import date
from skills_vector.operating_loop import run_recruiter_investigation
from skills_vector.persistence import InvestigationStore

with InvestigationStore("investigations.sqlite") as store:
    result = run_recruiter_investigation(store, as_of=date.today())
    assert result.paused_before == "human_review"
    assert result.draft.private
```

Inspect `result.events` and `result.artifacts` for the ordered per-node trail. Reusing the same SQLite store lets later runs skip incorporated fingerprints. No scheduler or publication path is included.

## Current boundary

This repository does not predict an individual's career, publish briefs, operate a job board, call paid APIs, or autonomously change prompts, policy, memory, routing, or code. Those remain outside the current milestone. See [the project goal loop](docs/goal-loop.md) for the adaptive work/stop policy.
