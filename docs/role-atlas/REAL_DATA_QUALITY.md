# M7: read-only real-data quality workflow

M7 processes the existing capture offline. It does **not** retrieve boards, change the admission cohort, relax floors, populate sectors, or supply forecast probabilities. The original rules/1 capture and approved M4/M5 artifacts remain immutable.

## Extraction and grouping

`posting-volume capture` now emits `extraction_method: rules/2`. The structural detector handles heading elements, bold paragraphs, short colon-led paragraphs, generic heading variants and explicit current-duty prose. Unheaded bare lists are not guessed. Company, benefits, legal and unrecognized bold-section boundaries stop extraction. Exact saved-description offsets and hashes remain mandatory.

`posting-volume project` validates the original snapshot, native rows and original extraction first. For rules/1 inputs it re-extracts complete saved board payloads in memory and preserves **only the original admitted posting identities**. This is an offline upgrade, not a new sample. Sampling/extraction/grouping/level-recipe identities form comparison groups; incompatible recipes are not historical corroboration.

The checked-in `onet31_lookup.json` is a derived subset: 94 role-scoped DWA records and 76 software categories, not the database. It carries native file hashes, ZIP SHA-256 and O*NET CC BY 4.0 attribution. `dwa_rules.json` records qualitative match confidence and rationale. Official DWA IDs are distinct from the old project-local task natural keys. Some lexical DWA candidates are explicitly outside anchor-SOC task membership: matching their activity text does **not** assert that O*NET assigns that task to the role.

A span contributes at most one primary DWA plus its explicitly named software examples/categories. Exact/close matches group onto stable IDs; loose proposals do not group. Unmatched spans retain phrase-rules/1 labels and original quotes. Taxonomy presence is standing official O / lagging evidence under the existing durable O*NET policy, not Hot Technologies market evidence. A named software example does not assert the Hot Technology flag. It is not an upward market signal, scope assessment, or proficiency measurement. Matching is deliberately incomplete and can be wrong; see the checked-in audit rather than treating lexical confidence as calibrated accuracy.

M7-r1 supersedes the original AE level policy. The reviewed `ae_segments.json` table applies only to rules/2 AE evidence: named customer segments in verified native titles or current owned-customer duties are strong scope. Generic full-cycle/quota/pipeline/CRM/prospecting work, geographic territory plans and strategic account plans are not segment scope. Seniority and years remain weak. Distinct segment labels conflict even if they share a level (for example Territory + Mid-Market); overlapping compound State Enterprise/Public Sector Named Account consumes its contained token. No segment-title policy is inferred for other roles. The ladder records the table/rationales and native-title/current-duty evidence; audits bind titles to native board hashes. Original labels remain in `audits/M7-levels.json`; `M7-r1-levels.json` preserves the recheck and every changed label. Agreement is policy conformance, **not independent validity**; the reviewer’s 15-posting spot-check remains pending.

A fresh frozen 20-success section check (`M7-r1-sections.json`, seed 20261107) excludes the admitted cohort and original development audit. It records one wrong company/team introduction span out of 129 spans, in one of 20 postings. No extraction or grouping rule was tuned against this held-out check. The quality script also reports the 25 most frequent posting-led labels for the next grouping review.

## Reproduce offline

```sh
SNAPSHOT=/home/logani/.local/share/skills-vector-evidence/role-atlas/capture-20261023T214312Z/2026-Q4
.venv/bin/skills-vector market posting-volume project --snapshot "$SNAPSHOT" --output /tmp/role-atlas-quality-a
.venv/bin/skills-vector market posting-volume project --snapshot "$SNAPSHOT" --output /tmp/role-atlas-quality-b
diff -qr /tmp/role-atlas-quality-a /tmp/role-atlas-quality-b
.venv/bin/python scripts/audit_role_atlas_m7.py --snapshot "$SNAPSHOT" --output /tmp/role-atlas-quality-report.json
```

Outputs must be fresh external paths. Audit cases pin the original snapshot, random seeds, IDs and source excerpts/hashes. The script checks that current evidence still matches those cases. It reports the original 67 unknown levels under generic triage, their categories under the expanded AE policy on the original spans, final agreement, coverage and grouped nodes. It does not generate new manual judgments. `--no-upgrade --no-grouping` on `project` reproduces the original extraction/phrase-map route; expanded AE scope does not apply to rules/1 records.

Rebuild the derived lookup from the **read-only** local database:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from skills_vector.market.onet_backbone import derive
print(derive(Path('/home/logani/.local/share/skills-vector-evidence/onet/db_31_0'),
             Path('src/skills_vector/market/role_atlas_mappings/skills.json'),
             Path('/tmp/onet31-lookup-rebuilt.json')))
PY
cmp /tmp/onet31-lookup-rebuilt.json src/skills_vector/market/role_atlas_mappings/onet31_lookup.json
```

The existing `market signals import hot-technologies` command also accepts 31.0 `Software Skills.txt` columns (`Workplace Example`, `Element ID`, `Element Name`). This is separate from taxonomy grouping and does not merge official signals into a research catalog automatically.

## Roster probe — operator only

`--candidates` uses the posting-volume policy schema: explicit employer identities, allowlisted ATS kind/token, role title hints and source notes. The existing starter policy is a starting point, **not a representative frame**. Add proposed candidates to a separate policy file and review their employer identities before probing.

Offline sample:

```sh
.venv/bin/skills-vector market posting-volume roster-probe \
  --candidates tests/samples/posting-volume/policy.sample.json \
  --payload-root tests/samples/posting-volume \
  --output /tmp/role-atlas-roster-draft.json
```

**Live operator command; not run by Pi:**

```sh
.venv/bin/skills-vector market posting-volume roster-probe \
  --candidates /path/to/candidates.json --live \
  --output "$HOME/.local/share/skills-vector-evidence/role-atlas/roster-draft.json"
```

The bounded probe uses only existing Greenhouse, Lever and Ashby public hosts, independent posting-lane limits and no provider/model client. Every token records HTTP/existence state, observed/native board totals, matched-title counts, response hash and source note. Unknown/failed retrievals have unknown counts, not invented zeros. Counts from incomplete listings are partial diagnostics, not admission guarantees. The result and each board are explicitly `reviewed: false`; manually verify it before promoting any entries into a reviewed capture policy. A successful board/token probe does not establish employer sector, US-eligible volume, fair admission, or representativeness.
