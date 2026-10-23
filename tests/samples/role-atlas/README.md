# Authored importer samples — NOT official observations

Every row/value is hand-authored, tiny test data. These are **samples**, not
agency downloads or market evidence. Codes illustrate the upstream syntax;
commodity 99999999 and certification 000001 are deliberately synthetic.
Use `--sample`; sample bundles can merge into fixture roles only, never research.

Native column/key formats exercised:

- O*NET database **Technology Skills.txt**, tab-delimited UTF-8:
  O*NET-SOC Code, Example, Commodity Code, Commodity Title, Hot Technology,
  In Demand. Y/N flags; only Hot Technology=Y is admitted.
- O*NET database **Emerging Tasks.txt**, tab-delimited:
  O*NET-SOC Code, Task, Task Type, Incumbents Responding, Date, Domain Source.
  The native MM/YYYY date is retained verbatim, not invented as a precise date.
- BLS National Employment Matrix **occupation** table: occupation title/code,
  industry title/code/type, base/projected employment and percent columns.
  The CSV sample uses the upstream header vocabulary flattened to one header
  row (including the base year); it is not an employment projection estimate.
  The importer also reads scalar-cell XLSX workbooks, ignoring introductory rows
  and trailing footnotes. Employment units remain thousands; native industry
  rows are never silently aggregated into broad sectors.
- CareerOneStop saved Certification Finder JSON response: CertList with CertId,
  CertName, Organization (CertOrg is also accepted), CertType and Url; RecordCount at the envelope.
  The original query SOC/NAICS is operator metadata, **not an invented column**
  in that response. Catalog membership is not an employer requirement.

No tests fetch upstream files or need an API key. Public format references
(for operators to consult separately, never fetched by this code):
https://www.onetcenter.org/database.html
https://www.bls.gov/emp/data/industry-occupation-matrix.htm
https://www.careeronestop.org/Developers/WebAPI/web-api.aspx
