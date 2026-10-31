# Verified sectors and structured employer career pages (M9)

## Local SEC import — no retrieval

```sh
.venv/bin/skills-vector market role-atlas sectors import \
  --sec "$HOME/.local/share/skills-vector-evidence/sec" \
  --concordance "$HOME/.local/share/skills-vector-evidence/sec/census/1987_SIC_to_2002_NAICS.csv"
```

This reads only the reviewed employer/registrant index, matching native SEC
submission files and Census concordance. It exports allowlisted classification
fields to `role_atlas_mappings/{sic_sectors,employers}.json`, not submission
objects or transport metadata. The supplied XLS hash is checked when present.
Private employers retain their note. Rejected collisions and subsidiaries are
never mapped; 7389/7340 remain unresolved. Broad 31–33, 44–45 and 48–49 are not
split. ASCII-hyphen and en-dash sector spellings resolve equivalently in the API.

The selected publication is now `research/role-atlas/2026-Q4-union-r3/`, release
`role_research_bad1fb3f89ffda0e` (M10): 495 AE, 37 FDE, 20 Growth and 12 HR
postings from the reviewer-run union capture. The M9 union release `role_research_7333966260cf6538` is retained unselected.
The older sector-only `role_research_71d6040270516002` retains its previously
approved historical pointer; it is not advanced for M10. No published release
file is edited: supersession now lives in the research README. Earlier role,
view, ledger and M8 Q4 bytes remain unchanged.

```sh
.venv/bin/skills-vector market serve \
  --role-atlas-root research/role-atlas/2026-Q4-union-r3/roles \
  --role-atlas-ledger research/role-atlas/2026-Q4-union-r3/ledger.json
```

Deployment remains operator-explicit (the same paths can be selected with
`SKILLS_VECTOR_ROLE_ATLAS_ROOT` / `SKILLS_VECTOR_ROLE_ATLAS_LEDGER`). Vercel bundles
the new runtime publication, not historical audits or the superseded Q4 tree.
No deployment is performed. The deployed API explicitly defaults to this reviewed
publication; any operator selector overrides it, including invalid selectors,
which fail closed rather than falling back. CLI/library selection stays explicit.

## Structured roster policy

The checked-in roster is not silently expanded. The reviewer supplied an external
reviewed union of 71 ATS boards and 28 Workday tenants and ran the selected capture.
The earlier saved single Workday page remains format reference only. Future origin
additions still require employer-ownership and roster review. A `jsonld` board accepts:

```json
{
  "ats": "jsonld",
  "employer": "Authored Employer",
  "origin": "https://careers.example.org",
  "sitemap": "https://careers.example.org/sitemap.xml",
  "note": "Authored example: this career origin belongs to the named employer.",
  "max_job_pages": 600
}
```

The existing policy schema, data-kind label, role title hints, quarterly cadence,
500-posting/role maximum and employer cap still apply. Put an operator-supplied
identifier in the policy's top-level `user_agent`; it is not a code default or
CLI argument. Only its hash enters the saved policy, never the header value.
Use a token without personal details. `employer_aliases` is an optional reviewed
list of additional hiring-organization names. Comparison strips a leading numeric
company code, listed legal suffixes and punctuation, then casefolds. The default
`organization_rule: "name"` requires one of those names; missing or unrelated
names are rejected and counted. There is no fuzzy match.

For reviewed tenants that publish per-posting legal entities instead of the
brand, opt in **per board**:

```json
{
  "ats": "jsonld",
  "employer": "Authored Employer",
  "origin": "https://authored.myworkdayjobs.com",
  "note": "Authored example: this tenant belongs to the named employer.",
  "organization_rule": "tenant_entity"
}
```

`tenant_entity` is valid only on hosts ending in `.myworkdayjobs.com`, or with
an explicit nonblank `tenant_entity_note` documenting the reviewed exception for
another host. The normal ownership `note` is still required; it is **not** the
exception note. Other rule values and unreviewed non-Workday uses are rejected.
The roster origin and ownership note determine the employer, regardless of the
posting organization, including an absent or empty name. The raw name is stored
as `hiring_entity` on the selected native job and captured posting (`null` when
missing/non-string, `""` when explicitly empty), never substituted for the roster
employer or used to infer its sector. US geography and expiry still apply.

A normalized name equal to **another roster employer or its alias**, across any
board kind in the same policy, is rejected as
`organization_other_roster_employer`. Own names/aliases and additional boards for
the same employer do not trigger that guard; an alias collision with another
employer does. Keep all approved ATS and structured boards in the union policy
so this comparison sees the full reviewed frame.

Origins must be public HTTPS DNS hosts with an ownership note. The checked-in
aggregator denylist applies to subdomains/country suffixes as well. Scope is
**exact hostname** (stricter than the prior art's related-domain test); changing
ATS tenants or following cross-site redirects is forbidden. Sitemap URLs and
all redirects are rechecked, with robots enforced at each redirected path.

Robots is fetched first. Set top-level `robots_agent` (or a per-board override)
to the operator-selected product token, e.g. `RoleAtlasOperator`. Matching uses
that token, never the first browser/User-Agent header token. If omitted or
unmatched, only wildcard groups apply. Named groups take precedence, with
longest-path and Allow-on-tie semantics. Discovery uses only configured or
robots-advertised sitemaps, at depth at most 2 and at most 64 sitemap documents.
Only matching role-title URL slugs are fetched as job pages. There is no HTML
link crawling, search or semantic HTML fallback.

The fetch layer enforces at least one second between request starts per host
(and larger declared crawl delays), including redirects/retries; 2,000,000 bytes
per response; at most 600 job pages/board and five redirect hops. The existing
**600-request run ceiling (raised from 200 for the union roster) remains in force** and can bind before the page cap.
Incomplete discovery/cap/transport failures do not become complete comparison
frames. Reports count fetched pages, title/cap skips, expiry, missing JSON-LD,
organization/geography rejections and robots denials. Every structured capture
board summary (`boards[].structured`) and probe board reports
`hiring_entity_counts: [{"hiring_entity": "...", "count": 1}]`, sorted by the raw
name, with a separate `null` bucket before names. These count **observed parsed
JobPosting nodes before admission and dedup**, including rejected entities, not
unique admitted postings. Empty and missing names remain distinct buckets.
A probe samples at most one matching job page; its draft also includes the first
node's raw `hiring_entity` (nullable), even if rejected. For multi-node pages,
inspect the counts as well. All drafts remain `reviewed: false`. Rate waits are
unnecessary for local fixture-file reads.

`JobPosting` arrays, nested `@graph` and list-valued `@type` are supported.
Expired or description-less postings are skipped. Expiry is judged at the
capture's timestamp inside its declared quarter, never the machine clock during
reprojection. Explicit country fields disambiguate Canada from the US state
abbreviation; US admission still uses the existing geography rule.

Native selected fields retain the **full unchanged description** in the
out-of-tree capture, its normal ATS-style visible text, page URL, original
response hash and identifier. Employer recruiting/accommodation details do not
reject postings or rewrite descriptions. At quote emission, every board kind
omits only contact-bearing sentences and records `contact_spans_omitted` plus
hash/offset receipts (without copying the omitted text). Contact-only postings
remain admitted; their empty expectation lists do not fabricate skill evidence.
Config, URL and repository-publication metadata guards still apply. The
configured header value is never stored. Raw HTML/robots/transport documents
are not saved by the structured lane; this is not a complete raw-page archive.

Across board kinds, dedup uses employer + normalized title + normalized location,
ignoring incompatible cross-system identifier namespaces and retaining the ATS
row over its structured duplicate. Within one kind, identifiers remain distinct.
All fair sampling, exact quote offsets/hashes, levels, skills and projection
still reuse the existing lane.

## Skill-only projection (M10)

Projection now defaults to `rules/3` in both CLI and Python snapshot adapters.
It validates native extraction first, then filters exact sentence candidates
through the reviewed phrase table, splits education/experience into processed
posting fields and only then groups retained domains/duties/tools. The external
audit alone holds dropped text and qualification receipts; the public manifest
contains reason counts and empty-posting counts. Native capture files never change.
`--legacy-skills` (or Python `skills_only=False`) is an explicit historical
projection diagnostic; combine it with `--legacy-grouping` when replaying M7.

Rank is distinct employers, then the average of each admitted employer\'s
within-employer posting fraction. Posting fractions and all thresholds remain
unchanged. The sample descriptor is a verified-sector subset, with the verified
employer denominator printed; missing sector evidence is not guessed.

## Offline commands — executed with authored samples

```sh
.venv/bin/skills-vector market posting-volume roster-probe \
  --candidates tests/samples/jsonld/policy.sample.json \
  --payload-root tests/samples/jsonld --output /tmp/jsonld-probe.json
.venv/bin/skills-vector market posting-volume capture \
  --policy tests/samples/jsonld/policy.sample.json --quarter 2026-Q4 \
  --payload-root tests/samples/jsonld --retrieved-at 2026-10-15T00:00:00Z \
  --output /tmp/jsonld-sample-capture
```

Five explicitly synthetic employers, two role pages each, one unrelated URL each;
10 admitted fixture postings. These are not research volume or market findings.

## Operator-only live commands — NOT executed by the agent

After independently reviewing candidate employer origins and their ownership
notes, the operator supplies an external candidates policy and runs:

```sh
EVIDENCE="$HOME/.local/share/skills-vector-evidence/role-atlas"
.venv/bin/skills-vector market posting-volume roster-probe \
  --candidates "$EVIDENCE/jsonld-candidates.reviewed.json" --live \
  --output "$EVIDENCE/jsonld-roster-draft.json"
```

Every draft stays `reviewed: false`. Review per-path robots/discovery results,
`hiring_entity`, the per-entity counts (including rejected/missing names), and
the sampled node. Explicitly review any `tenant_entity` setting or non-Workday
`tenant_entity_note`; a successful probe does not approve either. Then create **one union policy**
containing the approved existing ATS boards and new structured boards (at most
100 total), not two separate same-quarter projection inputs:

```sh
QUARTER=$(.venv/bin/python -c 'from datetime import datetime,UTC; d=datetime.now(UTC); print(f"{d.year}-Q{(d.month-1)//3+1}")')
CAPTURE="$EVIDENCE/capture-jsonld-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/skills-vector market posting-volume capture \
  --policy "$EVIDENCE/roster-jsonld-reviewed.json" --quarter "$QUARTER" --live \
  --output "$CAPTURE"
.venv/bin/skills-vector market posting-volume project \
  --snapshot "$CAPTURE/$QUARTER" --output /tmp/role-atlas-expanded-new
.venv/bin/skills-vector market forecast-ledger create \
  --roles-root /tmp/role-atlas-expanded-new/roles \
  --ledger /tmp/role-atlas-expanded-new/ledger.json
```

Actual retrieval dates must match the quarter; never backdate. This produces a
new release id, with its full audit only in the operator evidence store. Keep the
superseded publication, and explicitly select the new root/ledger. Repeated
captures/releases in a quarter remain one cross-sectional window, not history.
