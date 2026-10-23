# M6: quarterly posting volume, no-model lane

M7 update: new captures emit `rules/2`; original `rules/1` snapshots remain valid
and immutable. Offline projection upgrades extraction while freezing original
admission. See [real-data quality](REAL_DATA_QUALITY.md) for grouping, audits,
limitations and the operator-only roster probe. The rules/1 descriptions below
record the approved M6 implementation, not the current extraction version.

## Boundaries

`market posting-volume` reuses the existing allowlisted public board parsers,
US-location predicate, dedup key and redirect guard. It imports no provider,
model client, agent, research configuration or budget module, including through
the real `skills-vector market` entrypoint. Legacy CLI imports are now lazy so
choosing this lane does not load the legacy interpreter or environment settings.
The model-backed lane's configuration, budgets and RESEARCH_LIMITS are unchanged.

Independent POSTING_VOLUME_LIMITS: 500 admitted postings per role, 100 configured
boards, 200 logical retrievals (each at most two infrastructure retries), 16 MiB
per response and 20-second transport timeout. No new hosts. Greenhouse, Lever,
Ashby, SmartRecruiters and Workable use the existing host/path allowlist.
Incomplete/paginated listings are recorded and excluded rather than presented
as complete coverage. Greenhouse/SmartRecruiters detail-only rows can use the
existing allowlisted detail endpoints in live mode. No discovery/search API.

## Policy and live operator command — NOT run by Pi

`config/role-atlas-posting-volume.json` is a starter **research** policy:
13 employer/Greenhouse board identities already recorded in published
`rel_20261002_cbdc6e8d5c4f`, with source notes. It is not a representative
market frame or a verified employer-sector map. Review the roster, canonical
employer names (consolidate aliases), and literal role-title hints before use.
A posting matching multiple roles is excluded. Titles select roles, never levels.

The live command for the operator/reviewer (no key needed):

```sh
QUARTER=$(.venv/bin/python -c 'from datetime import datetime,UTC; d=datetime.now(UTC); print(f"{d.year}-Q{(d.month-1)//3+1}")')
CAPTURE="$HOME/.local/share/skills-vector-evidence/role-atlas/capture-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/skills-vector market posting-volume capture --policy config/role-atlas-posting-volume.json --quarter "$QUARTER" --live --output "$CAPTURE"
```

Only the operator runs this. Pi did not contact a live job board. Live responses
record their actual retrieval timestamps; crossing a quarter boundary fails
rather than backdating data. Choose one canonical capture per quarter for a
comparison. A new capture belongs in a new output root; existing snapshots are
immutable. Repeated releases in one quarter are not independent windows.

## Offline capture

Authored native-format samples are labeled fixture in the policy and content,
with documented failures in `tests/samples/posting-volume/README.md`. Never
relabel them as research. Sample-named payloads are refused by research policies.

```sh
.venv/bin/skills-vector market posting-volume capture --policy tests/samples/posting-volume/policy.sample.json --quarter 2026-Q3 --payload-root tests/samples/posting-volume --retrieved-at 2026-09-01T00:00:00Z --output /tmp/rules-snapshots
.venv/bin/skills-vector market posting-volume capture --policy tests/samples/posting-volume/policy.sample.json --quarter 2026-Q4 --payload-root tests/samples/posting-volume --retrieved-at 2026-10-15T00:00:00Z --output /tmp/rules-snapshots
```

For real previously downloaded payloads, use a research policy whose board rows
include a relative `payload` path and the actual recorded retrieval timestamp.
The local payload root is read-only; path escapes are rejected.

## Exact evidence, conservative admission

- rules/1 recognizes headings and list structure for responsibilities,
  requirements and preferred qualifications. Company, benefits, legal, unknown
  headings and recognizable EEO/benefits boilerplate do not become expectations.
  No inferred section is created merely because text contains a scope word.
- Lever lists are included, not lost behind descriptionPlain. Greenhouse's
  entity-escaped content and Ashby's HTML/plain descriptions are handled.
- Full native board bytes and reconstructed native description are saved with
  separate hashes. Visible text is decoded without paraphrasing and saved as
  the posting's `description`. Every expectation is an **exact contiguous span**
  of that saved description, with start/end offsets and source_sha256.
  Hashes bind raw native bytes → native description → visible description →
  verbatim quote. HTML tags/entities are not claimed to be literal visible text.
- Projection checks every indexed file hash and replays extraction from the
  native row. It rejects forged quotes even after a postings file is rehashed.
- Every posting records extraction_method rules/1, board/native identity, US
  location, original job URL, retrieval time and employer source note.
- Dedup precedes allocation. The largest feasible deterministic round-robin
  sample satisfies max_postings_per_role and **no employer above 20%**.
  With fewer than five employers, a nonempty sample is impossible: admit zero,
  report the shortfall, never pad. This cap concerns operator-declared employer
  identities, not automatically inferred corporate ownership.
- Coverage counts include eligible role/US postings with missing sections, not
  just successful extractions. Failures stay visible in section_checks and the
  per-role responsibility_failures list.

Full raw responses/descriptions are external operator evidence, **not public
release extracts**. Do not copy real corpora into the repository or serve them.
Only authored sample payloads are checked in. Outputs cannot overlap source
trees, fixtures or preview/release; no immutable release or pointer is changed.

## M4 projection and comparable quarters

```sh
.venv/bin/skills-vector market posting-volume project --snapshot /tmp/rules-snapshots/2026-Q3 --snapshot /tmp/rules-snapshots/2026-Q4 --output /tmp/rules-role-projection
```

For real captures substitute their actual paths. The published release and
checked-in M4 mappings supply only the local official foundation/anchors.
No network is used by projection. The latest quarter is the role's demand window;
skill measurements retain independent earlier quarters. Stable sampling groups
hash the planned board roster, complete-board set, role/admission policy,
extraction/normalization recipe and mapping definition. Changed methods,
incomplete frames, changed rosters or duplicate quarters cannot manufacture a
trend. Empty denominators are not measured negative outcomes.

Scope triage sees **all current responsibility spans**, not qualification text.
The existing P3 scope/ownership patterns and conflict/management rules are
unchanged; whitespace is normalized only for matching, retaining original quotes
in strong-signal audit rows. Titles/tenure never decide levels.

Only this rules lane applies phrase-rules/1 identity normalization: lowercase,
strip explicit experience-with/in, ability-to, knowledge-of and related prefixes,
and is-a-plus/preferred suffixes. Original quote, source, expectation id and
normalized phrase remain in audit links. No stemming, negation removal, synonym
or skill-equivalence model. Ambiguous or loose backbone alignments stay posting-led.
The approved M4 records are not rewritten or relabeled.

New employers default to explicit **unverified/null NAICS** rows with source
notes. `project --employer-map /local/reviewed-employers.json` can provide reviewed
rows with employer, status, naics, source_id, quote, source_note and (for verified
rows) basis primary_employer_sector. Local quotes must belong to that employer.
Customers/job functions are not sector evidence. The map must be separately
reviewed; its declaration is not an automated semantic verification.

Every output validates as role/2. Core/rising/fading, edge and sector floors are
reused unchanged. Posting-only increases cannot become rising without an upward
O/M signal. Two quarters are not a four-quarter forecast backtest; forecasts
remain basis none (or absent for core). Fixture inputs produce fixture output;
real research policies produce research output. The existing M5 ledger is
selected separately and no numeric forecast or score is manufactured here.
