# Skills Vector agent usage

This directory is an approved occupational snapshot. Browsing and downloading it must not trigger model calls.

- Occupations, tasks, and skills use stable IDs. Treat `change_id` as the incremental cursor key.
- `state` is explicit: provisional, reviewed, superseded, or removed.
- Job advertisements in provenance are stated demand, not proof of work.
- Jobsss should download a release and compare locally. Do not send private profiles here.
- Assessment input/output: see assessment_contract.json. Outcomes are unknown, gap, meets, exceeds, or potential_transfer.
- Missing résumé keywords are never automatically a gap. Embedding similarity never establishes proficiency.
- Forecast files, if present, are optional and are not current requirements.
