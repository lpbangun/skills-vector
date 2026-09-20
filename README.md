# Skills Vector

Evidence-based occupational skills reference and local assessment contract for **U.S. startup workers**, especially people adopting AI.

Initial market: U.S. startups. Three families — engineering/AI, product/design, go-to-market/operations. This repository’s first vertical slice covers three contrasting pilots:

| Occupation ID | Family | O*NET baseline |
| --- | --- | --- |
| `occ_founding_engineer` | engineering/AI | 15-1252.00 Software Developers |
| `occ_product_manager` | product/design | 15-1299.09 IT Project Managers |
| `occ_growth_operator` | go-to-market/operations | 13-1161.00 Market Research Analysts |

Public beta still targets **30 reviewed occupations** (about ten per family). That gate is **not** met by this slice. Individuals are the first audience. [Jobsss](docs/jobsss.md) retains private profiles.

## What is implemented vs not

**Implemented (local, reversible):** occupational model; SQLite catalog; content-hashed retrieval; collect → extract → reconcile → challenge → review → atomic release/rollback; DeepInfra budget ledger (US$10 cap) **before** live calls; labeled offline fixtures; Jobsss-shaped assessment outcomes; static export layout for a later Sites plugin.

**Not implemented / not claimed:** practitioner-reviewed rubrics, two successful *scheduled* hosted update cycles, Sites deployment, live DeepInfra spend, 30-occupation launch, always-on API (see unmerged PR #6), or any hosted runner.

The earlier People Operations investigation loop (`skills_vector.domain`, `operating_loop`) remains in-tree as the previous experiment. It still defaults to deterministic stubs. Production occupational research **never** silently substitutes those stubs or fixtures.

## Local run

Python 3.11+. Copy `.env.example` locally if you later enable DeepInfra; do not commit keys.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests

.venv/bin/python -m skills_vector research occ_founding_engineer --fixtures
.venv/bin/python -m skills_vector review <run_id> approved --reviewer "your-name" --note "provisional offline pilot"
.venv/bin/python -m skills_vector research occ_product_manager --fixtures
.venv/bin/python -m skills_vector research occ_growth_operator --fixtures
.venv/bin/python -m skills_vector release
.venv/bin/python -m skills_vector budget
.venv/bin/python -m skills_vector schedule-notes
```

Omit `--fixtures` only when you intend to fetch live O*NET pages. If retrieval fails, prior evidence stays; fixtures are **not** used as a fallback.

## Design system

UI work follows **Evidence Atlas · Control** in [`DESIGN.md`](DESIGN.md). This slice does not add product screens.

## Docs

- [Architecture](docs/architecture.md)
- [Models and budget](docs/models.md)
- [Jobsss boundary](docs/jobsss.md)
- [Goal loop](docs/goal-loop.md)
