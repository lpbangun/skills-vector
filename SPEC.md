# Skills Vector MVP Spec

## Product outcome

Skills Vector is a private, local, U.S.-only occupational intelligence web app for the owner. Its domain is **People Operations & Talent** and it monitors exactly three roles: **HR Coordinator**, **Recruiter**, and **Learning & Development Specialist**.

The owner can open a dashboard, select a role, launch a deterministic investigation, inspect every persisted workflow stage, review a source-linked brief, and explicitly approve it. New investigations create updated briefs without erasing prior runs. Data and approvals survive process and repository restarts.

Every brief visibly contains role context, what is changing, task shifts, skill shifts, durable/meta capabilities, a two-year scenario with uncertainty, evidence records, counter-evidence or disagreement, and private approval state. The executable LangGraph performs parallel category research, evidence merge/validation, demand/task/skill/role-durability analysis, skepticism, forecast-panel aggregation, drafting, and a mandatory human gate.

## Evidence boundary

The bundled corpus is source-backed and credential-free. Each item retains title, public URL, publication date, approved category, relevant excerpt/claim, provenance, and role connection. Approved categories are public labor data, research papers, credible reports, selected job-posting signals, and official policy. Invalid categories, incomplete source records, missing references, and unsupported material claims fail validation.

## Exclusions

No model or paid API calls, credentials, scraping, scheduled runs, deployment, public publishing, prediction markets, job-board behavior, individual career predictions, extra roles, or autonomous prompt/policy/router/memory/code changes. Production topology (Vercel, managed Postgres, durable VPS worker) is deferred.

## Observable done-when criteria

- README gives exact install, start, investigate, inspect, approve, and verification commands.
- A fresh SQLite database contains validated evidence for all three roles and all approved research lenses.
- The browser UI exposes roles, runs, graph artifacts, evidence, every brief section, uncertainty/disagreement, and approval controls.
- Runs, artifacts, briefs, and approval decisions persist across new repository/application instances.
- The real LangGraph pauses at its human gate; only an explicit approval action completes a run.
- Automated tests cover evidence gating, persistence/approval, graph execution, HTTP/UI behavior, and one end-to-end case for each role.
- HR Coordinator, Recruiter, and Learning & Development Specialist acceptance cases pass consecutively in the final verification run.

