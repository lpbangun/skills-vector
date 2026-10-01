# Published market releases

* `current.json` — atomic pointer to the served release (never edited in place).
* `releases/<release_id>/` — immutable release artifacts: manifest, occupations, sources,
  postings, claims, requirements, extracts, exclusions, mappings, disagreements, lineage.
  See this repository's `acceptance/check_release.py` for the artifact contract.
* `citations/<claim_id>.json` — write-once claim citation documents; a cited URL keeps
  byte-identical content across later releases.

Served by `/api/*` (read-only) and directly as static files. No inference happens here.
