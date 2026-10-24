# Authored JSON-LD fixtures

All employers, descriptions and reserved-domain origins are synthetic. No saved
real Workday page or whole real posting is copied here. Five employers each
publish an HR and Growth page; an unrelated sitemap URL has no response and
must be skipped before fetching. The robots file, namespaced sitemap indexes
and URL sets, nested graph and array nodes exercise the native formats.

`legal-name.sample.html` also reproduces a numeric company code and legal suffix,
without copying the real Workday page. The policy names its `robots_agent` product
token separately from its header.

The sample policy is explicitly `data_kind: fixture`. Native descriptions and
all resulting quotes remain source-bound. Tests construct the disallowed,
expired, malformed, oversized, foreign-site, organization/geography mismatch,
page-limit and dedup cases in memory; no network access is required.
