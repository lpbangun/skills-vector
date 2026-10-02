"""Immutable release artifacts: schema, loading, validation, content hashing.

A release directory is the publishable unit described by ``acceptance/check_release.py``
(and by ``BENCHMARK.md`` HG03..HG06). It contains:

    manifest.json          release metadata + role scope + caveats + skip rules
    occupations.json       role rows with explicit scope and denominator stats
    sources.json           every retrieved source with url/date/sha256/rights/inclusion
    postings.json          deduplicated admitted postings (employer, url, variant,
                           individual-contributor work-level decision)
    claims.json            agent-authored, quote-backed statements
    requirements.json      ordered learning priorities linked to claims
    extracts/<id>.txt      rights-safe published extracts (verified quote spans)

Only agent-validated rows ever reach ``preview/release``; validation here is
fail-closed and mirrors the structural rules enforced by the acceptance
protocol, plus provenance/rights policy checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hosts import url_problem

RELEASE_STEMS = ("occupations", "sources", "postings", "claims", "requirements")

BAD_PATTERN_HINTS = (
    r"\bprevalence\b.*\d+%",
    r"\b\d+(?:\.\d+)?%\s*(?:of (?:the )?(?:market|demand|hiring))",
    r"\btrend\s+(?:is\s+)?(?:upward|downward|up|down)\b",
    r"\bmost\s+(?:employers|postings|companies)\b",
)

FIXTURE_MARKERS = ("fixture",)

REQUIRED_VARIANTS = ("product-growth", "growth-marketing", "sales-account-executive")

KNOWN_CLAIM_TYPES = ("foundation", "advertised_demand", "scope")

KNOWN_CONFIDENCE = ("bounded", "low", "none")

VALID_WORK_LEVELS = ("individual_contributor", "people_manager", "unknown")
VALID_RESPONSIBILITY_BANDS = ("early_career", "independent_ic", "senior_strategic_ic", "people_management", "unknown")
VALID_EXPECTATION_DIMENSIONS = ("task", "capability", "tool", "knowledge", "experience", "contextual_expectation", "demonstration", "credential", "unknown")
VALID_EXPECTATION_BASES = ("employer_requirement", "employer_preference", "emergent_signal", "unknown")
MANAGEMENT_QUOTE_RE = re.compile(
    r"\b(?:direct reports?|(?:hire|hiring|coach(?:ing)?|evaluat(?:e|ing))\s+(?:your|own)\s*(?:team|staff|employees)|"
    r"supervis(?:e|ing)\s+(?:a|the|your|own)?\s*(?:team|staff|employees)|"
    r"manag(?:e|ing)\s+(?:a|the|your|own)?\s*(?:team|staff|employees))\b",
    re.IGNORECASE,
)
IC_QUOTE_RE = re.compile(
    r"\b(?:individual contributors?|non[- ]manager(?:ial)?|no direct reports?|"
    r"does not manage (?:a )?team|does not manage people|not a people manager|"
    r"non[- ]supervisory|IC role)\b",
    re.IGNORECASE,
)
PROFICIENCY_QUOTE_RE = re.compile(
    r"\b(?:proficient|proficiency|expert|advanced|intermediate|beginner|novice)\b",
    re.IGNORECASE,
)

RESPONSIBILITY_SIGNAL_RE = {
    "early_career": re.compile(r"\b(?:with guidance|under supervision|closely supervised|receive training|be mentored)\b", re.IGNORECASE),
    "independent_ic": re.compile(r"\b(?:independently|autonomously|without supervision|end.to.end ownership|own(?:s|ed)? the)\b", re.IGNORECASE),
    "senior_strategic_ic": re.compile(
        r"(?:\b(?:shape|set|define|develop|drive|lead(?:s|ing)?|own(?:s|ed|ing)?|architect|influence|establish)\b"
        r".{0,80}\b(?:strategy|strategic (?:priorities|direction|initiatives?)|roadmap|architecture|"
        r"(?:product|technical|organizational) direction|priorities|decisions)\b"
        r"|\b(?:own(?:s|ed|ing)?|solve(?:s|d|ing)?|tackle(?:s|d|ing)?|address(?:es|ed|ing)?|"
        r"lead(?:s|ing)?|drive(?:s|n|ing)?|work through)\b.{0,50}\b(?:ambiguous|complex|ill[- ]defined)\b"
        r"|\b(?:lead(?:s|ing)?|drive(?:s|n|ing)?|influence|shape|own(?:s|ed|ing)?)\b.{0,60}\b"
        r"(?:cross[- ]functional|stakeholders?|organizational decisions|company-wide direction)\b)",
        re.IGNORECASE,
    ),
}
ADVERTISED_YEARS_RE = re.compile(r"\b(?:\d+\+?\s*(?:[-–]\s*\d+\s*)?years?|years? of experience)\b", re.IGNORECASE)


def management_quote_supported(quote: str) -> bool:
    """Require staff ownership, not recruiting activity or advertised experience."""
    return bool(
        quote and not IC_QUOTE_RE.search(quote)
        and not ADVERTISED_YEARS_RE.search(quote)
        and MANAGEMENT_QUOTE_RE.search(quote)
    )


def responsibility_quote_supported(band: str, quote: str, work_level: str) -> bool:
    """Require worker-duty evidence, not a customer tier or title."""
    if not quote or ADVERTISED_YEARS_RE.search(quote):
        return False
    if band == "people_management":
        return work_level == "people_manager" and management_quote_supported(quote)
    if work_level == "people_manager" and band in ("independent_ic", "senior_strategic_ic"):
        return False
    signal = RESPONSIBILITY_SIGNAL_RE.get(band)
    return bool(signal and signal.search(quote))

SALES_SEGMENT_TERM = r"(?:enterprise|mid[- ]market|midmarket|SMB)"
SALES_SEGMENT_LABEL_RE = re.compile(r"\b" + SALES_SEGMENT_TERM + r"\b", re.IGNORECASE)
SALES_SEGMENT_CONTEXT_RE = re.compile(
    r"\b(" + SALES_SEGMENT_TERM + r")\s+\b"
    r"(?:customers?|accounts?|clients?|business(?:es)?|buyers?)\b"
    r"|\b(" + SALES_SEGMENT_TERM + r")\s+(?:sales\s+)?(?:market|segment)\b"
    r"|\b(?:market|segment)\s+(?:for|of)\s+(" + SALES_SEGMENT_TERM + r")\b",
    re.IGNORECASE,
)


def sales_segment_quote_supported(value: str, quote: str) -> bool:
    """Require the selected tier marker to attach to a buyer or sales market."""
    selected = SALES_SEGMENT_LABEL_RE.fullmatch(value)
    if selected is None:
        return False
    selected_marker = selected.group(0).casefold()
    for context in SALES_SEGMENT_CONTEXT_RE.finditer(quote):
        for marker in context.groups():
            if marker and marker.casefold() == selected_marker:
                return True
    return False



SUPPORTING_DATASETS = ("exclusions.json", "mappings.json", "disagreements.json", "lineage.json")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def source_literal_identity(dimension: str, wording: str) -> str:
    """Identity of a typed exact source mention, never synonym equivalence."""
    normalized = " ".join(wording.split()).casefold()
    return "idn_" + sha256_text(f"source-literal-identity/1|{dimension}|{normalized}")[:20]


def expectation_relationship_id(
    occupation: str, posting_id: str, source_id: str, expectation_id: str,
) -> str:
    """Version-specific role/posting/source relationship to an expectation."""
    return "edge_" + sha256_text(
        f"posting-source-expectation/1|{occupation}|{posting_id}|{source_id}|{expectation_id}"
    )[:20]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Load a release dataset as ``.json`` array/object or ``.jsonl`` rows."""

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return rows
    value = json.loads(text)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def load_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _dataset_path(root: Path, stem: str) -> Path:
    jsonl = root / f"{stem}.jsonl"
    if jsonl.exists():
        return jsonl
    return root / f"{stem}.json"


@dataclass
class ReleaseData:
    """A fully loaded release directory."""

    root: Path
    manifest: dict[str, Any]
    occupations: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    postings: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    extracts: dict[str, str] = field(default_factory=dict)

    @property
    def release_id(self) -> str:
        return str(self.manifest.get("release_id") or "")

    def rows(self, stem: str) -> list[dict[str, Any]]:
        return list(getattr(self, stem))

    def source_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("id")): row for row in self.sources}

    def claim_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("id")): row for row in self.claims}

    def occupation_by_slug(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("slug")): row for row in self.occupations}

    def postings_by_occupation(self, slug: str) -> list[dict[str, Any]]:
        return [row for row in self.postings if str(row.get("occupation_slug")) == slug]

    def claims_by_occupation(self, slug: str) -> list[dict[str, Any]]:
        return [row for row in self.claims if str(row.get("occupation_slug")) == slug]

    def requirements_by_occupation(self, slug: str) -> list[dict[str, Any]]:
        return [row for row in self.requirements if str(row.get("occupation_slug")) == slug]

    def content_hash(self) -> str:
        """Deterministic hash of the publishable content (used for release ids)."""

        payload = {
            "occupations": self.occupations,
            "sources": self.sources,
            "postings": self.postings,
            "claims": self.claims,
            "requirements": self.requirements,
            "extracts": {name: sha256_text(text) for name, text in sorted(self.extracts.items())},
            "supporting_datasets": {name: load_rows(self.root / name) for name in SUPPORTING_DATASETS},
        }
        return sha256_text(canonical_json(payload))[:16]


def load_release(root: Path | str, *, with_extracts: bool = True) -> ReleaseData:
    root = Path(root)
    manifest = load_json_dict(root / "manifest.json") or {}
    data = ReleaseData(
        root=root,
        manifest=manifest,
        occupations=load_rows(_dataset_path(root, "occupations")),
        sources=load_rows(_dataset_path(root, "sources")),
        postings=load_rows(_dataset_path(root, "postings")),
        claims=load_rows(_dataset_path(root, "claims")),
        requirements=load_rows(_dataset_path(root, "requirements")),
    )
    if with_extracts:
        for source in data.sources:
            extract_path = str(source.get("extract_path") or "").strip()
            if not extract_path:
                continue
            path = (root / extract_path).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                continue
            if path.is_file():
                data.extracts[str(source.get("id"))] = path.read_text(encoding="utf-8", errors="replace")
    return data


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def safe_https_url(url: str) -> bool:
    """True when the URL is a plain https link (no javascript:/data:, no userinfo/port)."""

    from urllib.parse import urlsplit

    if not isinstance(url, str) or not url.strip():
        return False
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname:
        return False
    if parts.username or parts.password or parts.port is not None:
        return False
    return True


def _problems_for_role_scope(scope: Any, what: str) -> list[str]:
    if not isinstance(scope, dict) or not str(scope.get("geography") or "").strip() or not str(scope.get("responsibility_scope") or "").strip():
        return [f"{what}: role_scope needs explicit geography and responsibility_scope"]
    return []


def validate_release(root: Path | str, *, min_postings: dict[str, int] | None = None) -> list[str]:
    """Return every structural/policy problem. Empty list means publishable."""

    root = Path(root)
    problems: list[str] = []
    if not (root / "manifest.json").is_file():
        return [f"release directory has no manifest.json: {root}"]

    data = load_release(root)
    manifest = data.manifest
    for field in ("release_id", "generated_at", "agent_attribution"):
        if not str(manifest.get(field) or "").strip():
            problems.append(f"manifest missing {field}")
    problems += _problems_for_role_scope(manifest.get("role_scope"), "manifest")
    caveat = manifest.get("frequency_caveat")
    if not (caveat is True or str(caveat or "").strip()):
        problems.append("manifest missing frequency_caveat")
    if not (isinstance(manifest.get("skip_rules"), (dict, list)) and len(manifest.get("skip_rules") or []) > 0):
        problems.append("manifest missing skip_rules")
    if not (isinstance(manifest.get("sampling_scope"), (dict, list)) and len(manifest.get("sampling_scope") or []) > 0):
        problems.append("manifest missing sampling_scope")

    if not data.occupations:
        problems.append("no occupations published")
    if not data.sources:
        problems.append("no sources published")
    if not data.postings:
        problems.append("no postings published")
    if not data.claims:
        problems.append("no claims published")
    if not data.requirements:
        problems.append("no learning priorities published")

    occ_slugs = {str(o.get("slug") or "") for o in data.occupations}
    source_ids = {str(s.get("id") or "") for s in data.sources}
    claim_ids = {str(c.get("id") or "") for c in data.claims}

    for occ in data.occupations:
        slug = str(occ.get("slug") or "").strip()
        if not slug or not str(occ.get("label") or "").strip():
            problems.append(f"occupation row missing slug/label: {occ}")
            continue
        problems += _problems_for_role_scope(occ.get("role_scope"), f"occupation {slug}")
        variants = occ.get("growth_variants") or []
        if "growth" in slug:
            missing = [v for v in REQUIRED_VARIANTS if v not in variants]
            if missing:
                problems.append(f"occupation {slug}: growth variants incomplete: missing {missing}")
            for variant in variants:
                if not any(
                    str(p.get("variant") or "") == variant and str(p.get("occupation_slug") or "") == slug
                    for p in data.postings
                ):
                    coverage = occ.get("coverage") if isinstance(occ.get("coverage"), dict) else {}
                    unsupported = coverage.get("unsupported_variants") or {}
                    record = unsupported.get(variant) if isinstance(unsupported, dict) else None
                    attempted_ids = {
                        str(source.get("id")) for source in data.sources if isinstance(source, dict)
                        and source.get("source_type") == "job-board" and source.get("occupation_slug") == slug
                    }
                    recorded_ids = record.get("attempt_source_ids") if isinstance(record, dict) else None
                    queries = record.get("query_terms") if isinstance(record, dict) else None
                    if not (
                        isinstance(record, dict) and record.get("status") == "unavailable"
                        and isinstance(recorded_ids, list) and recorded_ids
                        and all(str(value) in attempted_ids for value in recorded_ids)
                        and isinstance(queries, list) and any(isinstance(value, str) and value.strip() for value in queries)
                    ):
                        problems.append(f"occupation {slug}: variant {variant} has no postings rows or recorded attempted unavailable coverage")
        stats = occ.get("stats")
        if not isinstance(stats, dict):
            problems.append(f"occupation {slug}: missing stats")
        else:
            for field_name in ("postings_dedup", "employers_dedup", "sampled", "total_seen"):
                value = stats.get(field_name)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    problems.append(f"occupation {slug}: stats.{field_name} not a non-negative integer")

    extracts_checked: dict[str, str] = {}
    source_by_id = data.source_by_id()
    for source in data.sources:
        sid = str(source.get("id") or "") or "<no-id>"
        for field_name in ("url", "retrieved_at", "sha256", "rights"):
            if not str(source.get(field_name) or "").strip():
                problems.append(f"source {sid}: missing {field_name}")
        if "inclusion" not in source:
            problems.append(f"source {sid}: missing inclusion record")
        elif source.get("inclusion") is False and not str(source.get("exclusion_reason") or "").strip():
            problems.append(f"source {sid}: excluded without exclusion_reason")
        problems += _problems_for_role_scope(source.get("role_scope"), f"source {sid}")
        url = str(source.get("url") or "")
        if url:
            reason = url_problem(url)
            if reason:
                problems.append(f"source {sid}: url refused by allowlist ({reason})")
        retrieval_kind = str(source.get("retrieval_kind") or "")
        if retrieval_kind and retrieval_kind not in ("listing", "detail"):
            problems.append(f"source {sid}: unknown retrieval_kind {retrieval_kind!r}")
        if str(source.get("source_type") or "") == "job-posting-detail" or retrieval_kind == "detail":
            if retrieval_kind != "detail":
                problems.append(f"source {sid}: job-posting detail source must declare retrieval_kind=detail")
            parent = str(source.get("parent_source_id") or "").strip()
            if not parent:
                problems.append(f"source {sid}: detail source missing parent_source_id")
            elif parent not in source_ids:
                problems.append(f"source {sid}: dangling parent_source_id {parent!r}")
            elif str(source_by_id[parent].get("source_type") or "") != "job-board":
                problems.append(f"source {sid}: parent_source_id {parent!r} is not a job-board listing source")
        extract_path = str(source.get("extract_path") or "").strip()
        if extract_path:
            extract_file = root / extract_path
            if not extract_file.is_file():
                problems.append(f"source {sid}: extract_path {extract_path} not found")
            else:
                extracts_checked[sid] = extract_file.read_text(encoding="utf-8", errors="replace")
                recorded = str(source.get("extract_sha256") or "").strip()
                if not recorded:
                    problems.append(f"source {sid}: extract_sha256 required when extract_path present")
                elif recorded != sha256_file(extract_file):
                    problems.append(f"source {sid}: extract_sha256 mismatch for {extract_path}")

    for claim in data.claims:
        cid = str(claim.get("id") or "") or "<no-id>"
        sids = claim.get("source_ids")
        if not isinstance(sids, list) or not sids:
            problems.append(f"claim {cid}: no source_ids")
            continue
        dangling = [source_id for source_id in sids if str(source_id) not in source_ids]
        if dangling:
            problems.append(f"claim {cid}: dangling source_ids {dangling}")
        slug = str(claim.get("occupation_slug") or "")
        if slug not in occ_slugs:
            problems.append(f"claim {cid}: unknown occupation_slug")
        quote = claim.get("quote")
        if quote is not None:
            if not isinstance(quote, str) or not quote.strip():
                problems.append(f"claim {cid}: empty quote field")
            elif not any(quote in extracts_checked.get(str(source_id), "") for source_id in sids):
                problems.append(f"claim {cid}: quote not byte-verbatim in any cited source extract")
        claim_type = claim.get("claim_type")
        if claim_type is not None and str(claim_type) not in KNOWN_CLAIM_TYPES:
            problems.append(f"claim {cid}: unknown claim_type {claim_type!r}")
        dimension = claim.get("expectation_dimension")
        basis = claim.get("evidence_basis")
        if dimension is not None and dimension not in VALID_EXPECTATION_DIMENSIONS:
            problems.append(f"claim {cid}: invalid expectation_dimension {dimension!r}")
        if claim_type == "foundation" and basis not in (None, "official_foundation"):
            problems.append(f"claim {cid}: foundation evidence_basis must be official_foundation")
        if claim_type == "advertised_demand" and basis is not None and basis not in VALID_EXPECTATION_BASES:
            problems.append(f"claim {cid}: invalid advertised evidence_basis {basis!r}")
        expectation_ids = claim.get("expectation_ids")
        if expectation_ids is not None:
            if not isinstance(expectation_ids, list) or len(expectation_ids) != len(set(map(str, expectation_ids))):
                problems.append(f"claim {cid}: expectation_ids must be a unique array")
                expectation_ids = []
            known_expectation_ids = {
                str(expected.get("expectation_id"))
                for posting in data.postings
                if str(posting.get("occupation_slug") or "") == slug
                for expected in (posting.get("expectations") or [])
                if isinstance(expected, dict)
            }
            if any(str(value) not in known_expectation_ids for value in expectation_ids):
                problems.append(f"claim {cid}: expectation_ids reference rows outside this role's admitted sample")
            evidence = claim.get("evidence") if isinstance(claim.get("evidence"), dict) else {}
            evidence_ids = evidence.get("expectation_ids")
            if evidence_ids is not None:
                if not isinstance(evidence_ids, list):
                    problems.append(f"claim {cid}: evidence expectation_ids must be an array")
                elif set(map(str, evidence_ids)) != set(map(str, expectation_ids)):
                    problems.append(f"claim {cid}: expectation id lineage differs between claim and evidence")
        evidence = claim.get("evidence") if isinstance(claim.get("evidence"), dict) else {}
        posting_ids = evidence.get("posting_ids")
        if posting_ids is not None:
            if not isinstance(posting_ids, list) or len(posting_ids) != len(set(map(str, posting_ids))):
                problems.append(f"claim {cid}: evidence posting_ids must be a unique array")
                continue
            role_rows = [
                posting for posting in data.postings
                if str(posting.get("occupation_slug") or "") == slug
            ]
            variant = str(claim.get("variant") or "")
            if slug == "growth-manager" and variant:
                population = [posting for posting in role_rows if str(posting.get("variant") or "") == variant]
            else:
                population = role_rows
                if slug == "growth-manager" and claim_type == "advertised_demand":
                    problems.append(f"claim {cid}: growth demand claim must identify exactly one variant")
            population_by_key = {
                str(posting.get("dedup_key") or posting.get("url") or ""): posting for posting in population
            }
            matched = [population_by_key[str(value)] for value in posting_ids if str(value) in population_by_key]
            if len(matched) != len(posting_ids):
                problems.append(f"claim {cid}: evidence posting_ids fall outside its role/variant sample")
            actual_employer_ids = sorted({str(posting.get("employer") or "") for posting in matched})
            actual_sources = {str(posting.get("source_id") or "") for posting in matched}
            if not actual_sources.issubset(set(map(str, sids))):
                problems.append(f"claim {cid}: a counted posting's source is absent from source_ids")
            employer_ids = evidence.get("employer_ids")
            if employer_ids is not None:
                if not isinstance(employer_ids, list) or sorted(map(str, employer_ids)) != actual_employer_ids:
                    problems.append(f"claim {cid}: evidence employer_ids do not match counted postings")
            expected_counts = {
                "postings_considered": len(population),
                "postings_matched": len(matched),
                "employers_considered": len({str(posting.get("employer") or "") for posting in population}),
                "employers_matched": len(actual_employer_ids),
            }
            for key, expected_count in expected_counts.items():
                actual_count = evidence.get(key)
                if isinstance(actual_count, bool) or not isinstance(actual_count, int) or actual_count != expected_count:
                    problems.append(f"claim {cid}: evidence {key} {actual_count!r} != computed {expected_count}")

    for posting in data.postings:
        slug = str(posting.get("occupation_slug") or "")
        if slug not in occ_slugs:
            problems.append(f"posting row references unknown occupation_slug {slug!r}")
        sid = str(posting.get("source_id") or "")
        if sid not in source_ids:
            problems.append(f"posting row references unknown source_id {sid!r}")
        elif source_by_id[sid].get("inclusion") is not True:
            problems.append(f"posting row references excluded source {sid!r}")
        for field_name in ("employer", "url"):
            if not str(posting.get(field_name) or "").strip():
                problems.append(f"posting row missing {field_name}")
        if posting.get("variant") is not None and str(posting.get("variant")) not in REQUIRED_VARIANTS:
            problems.append(f"posting row unknown variant {posting.get('variant')!r}")
        posting_url = str(posting.get("url") or "")
        if posting_url and not safe_https_url(posting_url):
            problems.append(f"posting row url is not a plain https link: {posting_url[:80]!r}")

        work_level = posting.get("work_level")
        work_reason = posting.get("work_level_reason")
        if work_level not in VALID_WORK_LEVELS:
            problems.append(f"posting row work_level {work_level!r} is not a supported explicit or unknown value")
        if not isinstance(work_reason, str) or not work_reason.strip():
            problems.append("posting row missing work_level_reason")
        work_evidence = posting.get("work_level_evidence")
        if isinstance(work_evidence, dict):
            quote = str(work_evidence.get("quote") or "")
            evidence_source = str(work_evidence.get("source_id") or "")
            if evidence_source != sid:
                problems.append("posting row work-level evidence must cite its exact posting source")
            if quote and quote not in extracts_checked.get(sid, ""):
                problems.append("posting row work-level quote is absent from its source extract")
            if work_level == "people_manager" and not management_quote_supported(quote):
                problems.append("people-manager posting lacks explicit direct-report wording")
            if work_level == "individual_contributor" and not (quote and IC_QUOTE_RE.search(quote)):
                problems.append("individual-contributor posting lacks explicit non-manager wording")
            if work_level == "unknown" and quote:
                problems.append("unknown work-level posting must not carry a contradictory classification quote")
        legacy_management_quote = posting.get("people_management_quote")
        if legacy_management_quote is not None:
            if not isinstance(legacy_management_quote, str):
                problems.append("posting row people_management_quote must be a string when present")
            elif legacy_management_quote.strip():
                if work_level != "people_manager" or not management_quote_supported(legacy_management_quote):
                    problems.append("posting row carries unsupported people-management evidence")

        band = posting.get("responsibility_band")
        if band is not None and band not in VALID_RESPONSIBILITY_BANDS:
            problems.append(f"posting row responsibility_band {band!r} is invalid")
        band_evidence = posting.get("responsibility_evidence")
        if isinstance(band_evidence, dict):
            band_quote = str(band_evidence.get("quote") or "")
            if str(band_evidence.get("source_id") or "") != sid:
                problems.append("posting row responsibility evidence must cite its exact posting source")
            if band == "unknown" and band_quote:
                problems.append("unknown responsibility band must not carry a classification quote")
            if band not in (None, "unknown") and (
                not band_quote or band_quote not in extracts_checked.get(sid, "")
                or not responsibility_quote_supported(str(band), band_quote, str(work_level))
            ):
                problems.append("classified responsibility band lacks a verified source-extract quote")

        contexts = posting.get("context_dimensions")
        if contexts is not None and not isinstance(contexts, dict):
            problems.append("posting row context_dimensions must be an object")
        elif isinstance(contexts, dict):
            for field_name, context in contexts.items():
                if not isinstance(context, dict):
                    problems.append(f"posting context {field_name!r} must be an object")
                    continue
                status = context.get("status")
                value = context.get("value")
                quote = str(context.get("quote") or "")
                if status == "present":
                    if not isinstance(value, str) or not value.strip() or not quote:
                        problems.append(f"posting context {field_name!r} is present without a value and quote")
                    elif str(context.get("source_id") or "") != sid:
                        problems.append(f"posting context {field_name!r} cites a different source")
                    elif quote not in extracts_checked.get(sid, "") or value.casefold() not in quote.casefold():
                        problems.append(f"posting context {field_name!r} value is not literal in its source quote")
                    elif field_name == "sales_segment" and not sales_segment_quote_supported(str(value), quote):
                        problems.append("posting sales_segment lacks verified customer, account, client, business, or sales-market context")
                elif status == "unknown":
                    if value is not None or quote:
                        problems.append(f"unknown posting context {field_name!r} must keep value and quote empty")
                else:
                    problems.append(f"posting context {field_name!r} has invalid status {status!r}")

        experience = posting.get("advertised_experience")
        if isinstance(experience, dict):
            values = experience.get("value")
            quotes = experience.get("quotes")
            if not isinstance(values, list) or not isinstance(quotes, list) or values != quotes:
                problems.append("advertised experience wording must preserve its exact quote list")
            elif any(not isinstance(value, str) or value not in extracts_checked.get(sid, "") for value in quotes):
                problems.append("advertised experience contains wording absent from its source extract")
            if values and experience.get("status") != "present":
                problems.append("advertised experience with exact wording must have present status")
            if not values and experience.get("status") != "unknown":
                problems.append("unstated advertised experience must remain unknown")

        expectations = posting.get("expectations")
        if expectations is not None and not isinstance(expectations, list):
            problems.append("posting expectations must be an array")
        elif isinstance(expectations, list):
            seen_expectation_ids: set[str] = set()
            for expected in expectations:
                if not isinstance(expected, dict):
                    problems.append("posting expectation row must be an object")
                    continue
                expectation_id = str(expected.get("expectation_id") or "")
                phrase = str(expected.get("source_wording") or "")
                dimension = expected.get("dimension")
                basis = expected.get("basis")
                if not expectation_id or expectation_id in seen_expectation_ids:
                    problems.append("posting expectation ids must be present and unique within the posting")
                seen_expectation_ids.add(expectation_id)
                if dimension not in VALID_EXPECTATION_DIMENSIONS or basis not in VALID_EXPECTATION_BASES:
                    problems.append(f"posting expectation {expectation_id!r} has invalid dimension or basis")
                if not phrase or len(phrase) > 240 or phrase not in extracts_checked.get(sid, ""):
                    problems.append(f"posting expectation {expectation_id!r} lacks a short source-verbatim phrase")
                normalized = " ".join(phrase.split()).casefold()
                expected_id = "exp_" + sha256_text(
                    f"expectation/2|{dimension}|{basis}|{normalized}"
                )[:20]
                if expectation_id and expectation_id != expected_id:
                    problems.append(f"posting expectation {expectation_id!r} does not match its stable normalized id")
                if expected.get("normalized_label") != normalized or expected.get("mapping_method") != "exact-normalized-label/2":
                    problems.append(f"posting expectation {expectation_id!r} has inconsistent normalization provenance")
                if str(expected.get("source_id") or "") != sid:
                    problems.append(f"posting expectation {expectation_id!r} cites a different source")
                if "identity_id" in expected or "relationship_id" in expected:
                    if (
                        expected.get("identity_id") != source_literal_identity(str(dimension), phrase)
                        or expected.get("identity_method") != "source-literal-identity/1"
                        or expected.get("kind") != dimension
                    ):
                        problems.append(f"posting expectation {expectation_id!r} has unverified source-literal identity")
                    if (
                        expected.get("relationship_id") != expectation_relationship_id(
                            slug, str(posting.get("dedup_key") or ""), sid, expectation_id,
                        )
                        or expected.get("relationship_method") != "posting-source-expectation/1"
                    ):
                        problems.append(f"posting expectation {expectation_id!r} has inconsistent role/posting/source relationship")
                proficiency = expected.get("proficiency")
                proficiency_quote = str(expected.get("proficiency_quote") or "")
                if proficiency == "explicitly_stated":
                    if not proficiency_quote or not PROFICIENCY_QUOTE_RE.search(proficiency_quote):
                        problems.append(f"posting expectation {expectation_id!r} lacks explicit proficiency wording")
                    elif proficiency_quote not in extracts_checked.get(sid, ""):
                        problems.append(f"posting expectation {expectation_id!r} proficiency quote is absent from its source")
                elif proficiency != "not_stated" or proficiency_quote:
                    problems.append(f"posting expectation {expectation_id!r} has unsupported proficiency status")
    for occ in data.occupations:
        slug = str(occ.get("slug") or "")
        if slug == "forward-deployed-engineer" and occ.get("provisional") is not True:
            problems.append("forward-deployed-engineer must remain explicitly provisional")
        if occ.get("provisional") is True:
            if occ.get("registration_status") != "mission-authorized-pilot":
                problems.append(f"occupation {slug}: provisional registration must say mission-authorized-pilot")
            if occ.get("human_review_status") != "not_reviewed":
                problems.append(f"occupation {slug}: no human review record exists for provisional registration")
            if occ.get("publication_status") != "no_fde_findings_without_admissible_source_evidence":
                problems.append(f"occupation {slug}: provisional publication status is missing its evidence gate")
            anchor = occ.get("official_anchor") if isinstance(occ.get("official_anchor"), dict) else {}
            if (
                anchor.get("code") != "15-1252.00"
                or anchor.get("status") != "partial_taxonomy_anchor_only"
                or not safe_https_url(str(anchor.get("url") or ""))
                or "not an official FDE code" not in str(anchor.get("basis") or "")
            ):
                problems.append(f"occupation {slug}: official taxonomy reference must remain a partial anchor only")
            decisions = occ.get("alias_decisions")
            if not isinstance(decisions, list) or len(decisions) < 2:
                problems.append(f"occupation {slug}: at least two explicit alias decisions are required")
            else:
                decision_by_label = {
                    str(row.get("label") or "").casefold(): str(row.get("decision") or "")
                    for row in decisions if isinstance(row, dict)
                }
                for label_fragment in ("solutions engineer", "customer success", "software engineer"):
                    matches = [
                        decision for label, decision in decision_by_label.items() if label_fragment in label
                    ]
                    if not matches or any(decision != "not_equivalent_by_title" for decision in matches):
                        problems.append(
                            f"occupation {slug}: {label_fragment} must not be treated as equivalent by title"
                        )
        stats = occ.get("stats") if isinstance(occ.get("stats"), dict) else {}
        rows = [p for p in data.postings if str(p.get("occupation_slug") or "") == slug]

        def dedup_key(posting: dict[str, Any]) -> str:
            return str(posting.get("dedup_key") or "") or str(posting.get("url") or "")

        computed_postings = len({dedup_key(p) for p in rows})
        computed_employers = len({str(p.get("employer") or "") for p in rows})
        declared_postings = stats.get("postings_dedup")
        declared_employers = stats.get("employers_dedup")
        if isinstance(declared_postings, int) and declared_postings != computed_postings:
            problems.append(f"occupation {slug}: stats.postings_dedup {declared_postings} != computed {computed_postings}")
        if isinstance(declared_employers, int) and declared_employers != computed_employers:
            problems.append(f"occupation {slug}: stats.employers_dedup {declared_employers} != computed {computed_employers}")
        sampled = stats.get("sampled")
        total_seen = stats.get("total_seen")
        if isinstance(sampled, int) and computed_postings > sampled:
            problems.append(f"occupation {slug}: postings {computed_postings} exceed sampled {sampled}")
        if isinstance(sampled, int) and isinstance(total_seen, int) and sampled > total_seen:
            problems.append(f"occupation {slug}: sampled {sampled} exceeds total_seen {total_seen}")
        if min_postings and slug in min_postings and computed_postings < min_postings[slug]:
            problems.append(f"occupation {slug}: {computed_postings} deduplicated postings < required minimum {min_postings[slug]}")

        declared_boards_attempted = stats.get("boards_attempted")
        board_sources = [
            str(source.get("id"))
            for source in data.sources
            if str(source.get("source_type") or "") == "job-board" and str(source.get("occupation_slug") or "") == slug
        ]
        if isinstance(declared_boards_attempted, int) and declared_boards_attempted != len(board_sources):
            problems.append(
                f"occupation {slug}: stats.boards_attempted {declared_boards_attempted} != computed {len(board_sources)} "
                "(per-posting detail sources must not inflate board denominators)"
            )
        declared_boards_used = stats.get("boards_used")
        parent_of = {str(source.get("id")): str(source.get("parent_source_id") or "") for source in data.sources}
        used_boards = {
            parent_of.get(str(posting.get("source_id") or "")) or str(posting.get("source_id") or "")
            for posting in rows
        }
        if isinstance(declared_boards_used, int) and declared_boards_used != len(used_boards):
            problems.append(f"occupation {slug}: stats.boards_used {declared_boards_used} != computed {len(used_boards)}")

        def count_values(values: list[str]) -> dict[str, int]:
            result: dict[str, int] = {}
            for value in values:
                result[value] = result.get(value, 0) + 1
            return dict(sorted(result.items()))

        for field_name, computed in (
            ("work_level_counts", count_values([str(row.get("work_level") or "unknown") for row in rows])),
            (
                "responsibility_band_counts",
                count_values([str(row.get("responsibility_band") or "unknown") for row in rows]),
            ),
            (
                "expectation_dimension_counts",
                count_values(
                    [
                        str(expected.get("dimension") or "unknown")
                        for row in rows
                        for expected in (row.get("expectations") or [])
                        if isinstance(expected, dict)
                    ]
                ),
            ),
            (
                "expectation_basis_counts",
                count_values(
                    [
                        str(expected.get("basis") or "unknown")
                        for row in rows
                        for expected in (row.get("expectations") or [])
                        if isinstance(expected, dict)
                    ]
                ),
            ),
        ):
            if field_name in stats and stats[field_name] != computed:
                problems.append(f"occupation {slug}: stats.{field_name} does not match admitted rows")
        context_counts = stats.get("context_value_counts")
        if isinstance(context_counts, dict):
            for field_name, reported in context_counts.items():
                computed = count_values(
                    [
                        str(context.get("value"))
                        for row in rows
                        if isinstance((context := (row.get("context_dimensions") or {}).get(field_name)), dict)
                        and context.get("status") == "present"
                        and context.get("value")
                    ]
                )
                if reported != computed:
                    problems.append(f"occupation {slug}: stats.context_value_counts.{field_name} does not match admitted rows")

    evidence_text = canonical_json(
        {
            "claims": data.claims,
            "sources": data.sources,
            "postings": data.postings,
            "occupations": data.occupations,
        }
    ).lower() + " " + " ".join(extracts_checked.values()).lower()

    for requirement in data.requirements:
        rid = str(requirement.get("id") or "") or "<no-id>"
        for field_name in ("rationale", "uncertainty", "label", "learning_outcome"):
            if not str(requirement.get(field_name) or "").strip():
                problems.append(f"requirement {rid}: missing {field_name}")
        cids = requirement.get("evidence_claim_ids")
        if not isinstance(cids, list) or not cids:
            problems.append(f"requirement {rid}: no evidence_claim_ids")
        else:
            dangling = [c for c in cids if str(c) not in claim_ids]
            if dangling:
                problems.append(f"requirement {rid}: dangling evidence_claim_ids {dangling}")
        if str(requirement.get("occupation_slug") or "") not in occ_slugs:
            problems.append(f"requirement {rid}: unknown occupation_slug")
        terms = requirement.get("search_terms")
        if isinstance(terms, list):
            for term in terms:
                normalized = re.sub(r"\s+", " ", str(term)).strip().lower()
                if normalized and normalized not in evidence_text:
                    problems.append(
                        f"requirement {rid}: search term {term!r} not backed by recorded evidence"
                    )
        confidence = requirement.get("confidence")
        if confidence is not None and str(confidence) not in KNOWN_CONFIDENCE:
            problems.append(f"requirement {rid}: unknown confidence {confidence!r}")

    # fixture leakage and prevalence-shaped text
    for name, rows in (
        ("occupations", data.occupations),
        ("sources", data.sources),
        ("postings", data.postings),
        ("claims", data.claims),
        ("requirements", data.requirements),
    ):
        for row in rows:
            blob = canonical_json(row).lower()
            for marker in FIXTURE_MARKERS:
                if marker in blob:
                    problems.append(f"{name}: row contains forbidden marker {marker!r}")
                    break
    for name in ("manifest.json", *(f"{stem}.json" for stem in RELEASE_STEMS)):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in BAD_PATTERN_HINTS:
            if re.search(pattern, text, re.IGNORECASE):
                problems.append(f"{name}: prevalence/trend-shaped text /{pattern}/")
    return problems


