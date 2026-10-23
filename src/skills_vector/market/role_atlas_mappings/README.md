# Offline Role Atlas mappings

These files are evidence inputs, not fixture data or an official taxonomy export.
They are pinned to `rel_20261002_cbdc6e8d5c4f`; another release requires another
locally inspected mapping revision. No URLs are fetched by the producer.

- `employers.json`: all 13 employers in the selected admitted sample. Every row
  has a source note and a published posting source reference. **All are
  unverified**: the local excerpts do not establish authoritative primary-employer
  NAICS classifications. Company descriptions, customer industries and job
  functions are not sufficient. Unknown rows have `naics: null` and contribute to
  no sector profile. A future verified row must name a supported broad sector,
  cite an exact local quote tied to that employer, and explain the verification;
  reusing customer-industry evidence is rejected. No inference from company names.
- `skills.json`: four role/anchor decisions, the broad NAICS vocabulary, 11 local
  O*NET task nodes and 21 exact phrase alignments. Notes identify agent-authored
  alignments, not human adjudication or official synonym equivalence. Each node
  records its exact published O*NET excerpt, source id and extract hash. The
  producer verifies all of these locally. Phrases match complete normalized
  source wording, never a substring/embedding/LLM guess.

## Backbone identity and limits

The published O*NET summary excerpts contain SOC occupation codes and task text,
but **not official task database element IDs**. `onet:<SOC>:local-task:<key>` is
therefore a project natural key anchored to an actual O*NET task, explicitly not
an invented official task id. Original O*NET text is attributed to O*NET OnLine,
U.S. Department of Labor, CC BY 4.0, per the published source rights notices. No
local ESCO concept snapshot is available, so there are no asserted ESCO links.
Mapped mentions count at most once per posting/node. Unmapped task, capability,
tool and knowledge phrases remain exact posting-led identities, not guessed O*NET
matches. Experience, credentials, demonstrations and contextual phrases are not
silently promoted into skills; audit counts retain their exclusions. Literal
credentials/knowledge can appear in a sector profile only after its sample floor.

## Scope and ownership

`role_pipeline.py` declares a conservative five-level scope rubric. Current
source-verified ownership/duty quotes vote; title and advertised tenure are audit
fields only. Past-experience wording does not establish current ownership.
Conflicting strong levels and people-management scope are `unassigned`, not
promotions to a convenient level. Names describe the rubric, not observed career
progression or validated role-specific hiring standards.
