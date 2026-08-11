# Skills Vector

Skills Vector is a private-first occupational intelligence MVP for the U.S. People Operations & Talent domain. The first monitored roles are deliberately fixed to:

- HR Coordinator
- Recruiter
- Learning & Development Specialist

The initial foundation encodes the evidence, scope, uncertainty, and human-review rules for one updatable role brief. It also defines the controlled LangGraph workflow boundary without configuring model providers, credentials, schedulers, storage, deployment, or a public UI.

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

LangGraph is a runtime dependency, but the contract tests are offline and do not make model or network calls. The graph factory accepts narrow, injected handlers so later tool access stays controlled by the workflow.

## Current boundary

This repository does not predict an individual's career, publish briefs, operate a job board, call paid APIs, or autonomously change prompts, policy, memory, routing, or code. Those remain outside the current milestone. See [the project goal loop](docs/goal-loop.md) for the adaptive work/stop policy.
