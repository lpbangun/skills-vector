# Research and publication architecture

Skills Vector now follows the same separation as [OH SHI](https://github.com/lpbangun/oh-shi), without copying Cloudflare, D1, or hosted refresh:

```
permitted sources → durable provenance (SQLite) → query/export → CLI and future Sites static files
```

Jobsss remains the private consumer: it downloads an approved release and compares locally. This repository does not host personal evidence.

## Workflow

`collect → extract → reconcile → challenge → review → release`

| Step | Who | Notes |
| --- | --- | --- |
| collect | ordinary code | Fetch, hash **body**, store URL, publisher, dates, parser version, rights. Failed fetches do not delete prior evidence. |
| extract | model or labeled offline interpreter | Tasks, skills, tools, context, supporting passages. Bounded JSON. |
| reconcile | model or offline interpreter | Terminology mapping. Model agreement ≠ independent corroboration. |
| challenge | model or offline interpreter | Receives **claims plus passages**. |
| review | human | Approve, request changes, or reject. Change-requests resume; rejects are terminal. |
| release | ordinary code | Atomic directory rename. No fixtures, checkpoints, credentials, or private evidence. |

Forecasts are optional (`--include-forecast`) and are not current requirements.

## Interfaces

| Command | Purpose |
| --- | --- |
| `skills-vector research occ_founding_engineer --fixtures` | Offline pilot path |
| `skills-vector research occ_founding_engineer` | Live O*NET fetch; no fixture fallback |
| `skills-vector review RUN approve --reviewer NAME` | Human gate |
| `skills-vector release` | Write `data/releases/<id>/` |
| `skills-vector rollback RELEASE` | Restore previous current pointer |
| `skills-vector assess ...` | Local Jobsss-shaped comparison |
| `skills-vector budget` | Remaining US$ cap |
| `skills-vector schedule-notes` | Computer must be awake; no hosted runner |

Open PR #6 (`codex/vps-roadmap-api-integration`) added a FastAPI always-on seam. This slice reuses its review-persistence and CLI ideas but does **not** merge that PR: publication is static files for a later Sites plugin, and research is not an HTTP service.

## Scheduling

Weekly collection and monthly review are commands you run locally. The computer must be awake. No cron, GitHub Action, or VPS runner is installed.
