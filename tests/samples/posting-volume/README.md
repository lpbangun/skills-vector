# Authored board samples — not research observations

Six native-format payloads, two employers per ATS, with 48 authored postings
(12 per role). Every payload declares SAMPLE DATA in the company description;
the operator policy is data_kind fixture. No agency/board was contacted.

- Greenhouse public Job Board API: jobs[], numeric id, title, location.name,
  absolute_url, updated_at, departments[], entity-escaped content HTML.
- Lever public postings API: top-level array, id, text, categories,
  description/descriptionPlain, lists[{text,content}], additional, hostedUrl.
  Responsibilities are deliberately in lists, not descriptionPlain.
- Ashby public posting API: jobs[], id, title, location, descriptionHtml,
  descriptionPlain, jobUrl, isListed, publishedAt and employmentType.

Six intentional failures have requirements/preferred sections but **no**
responsibilities heading/list. One other posting has conflicting L2/L4 ownership.
These test honest missing/conflicting scope rather than assigning from titles.
Benefits, company and legal prose are not expectations.

The 300-per-role acceptance test deterministically expands these templates into
ten sample employers × thirty distinct posting ids per role in native payload
formats. It is test data, not a market-volume or sampling-coverage finding.
No generated live-like corpus is committed or relabeled as research.

Capture locally with:
`skills-vector market posting-volume capture --policy tests/samples/posting-volume/policy.sample.json --quarter 2026-Q4 --payload-root tests/samples/posting-volume --retrieved-at 2026-10-15T00:00:00Z --output /tmp/sample-posting-snapshots`.
