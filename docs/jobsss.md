# Jobsss boundary

Jobsss keeps private profiles, evidence, and goals. Skills Vector publishes approved occupational snapshots.

1. Download a release directory (`manifest.json`, `*.jsonl`, `assessment_contract.json`, `AGENT.md`).
2. Do not call the research graph, SQLite catalog, or any model endpoint while browsing that snapshot.
3. POST nothing personal back to Skills Vector.
4. Pin `release_id` + rubric versions on every assessment.
5. Outcomes: `unknown`, `gap`, `meets`, `exceeds`, `potential_transfer`. Missing résumé keywords are never auto-gaps. Embeddings never establish proficiency.

Incremental changes use `changes.jsonl` with stable `change_id` and explicit `added` / `updated` / `superseded` / `removed`.
