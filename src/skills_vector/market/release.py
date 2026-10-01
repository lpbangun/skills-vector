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

# Admitted postings must carry a literal individual-contributor scope decision.
WORK_LEVEL_ADMIT = "individual_contributor"

SUPPORTING_DATASETS = ("exclusions.json", "mappings.json", "disagreements.json", "lineage.json")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


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
    if not isinstance(scope, dict) or not str(scope.get("geography") or "").strip() or not str(scope.get("seniority") or "").strip():
        return [f"{what}: role_scope needs explicit geography and seniority"]
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
                    problems.append(f"occupation {slug}: variant {variant} has no postings rows")
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
        dangling = [s for s in sids if str(s) not in source_ids]
        if dangling:
            problems.append(f"claim {cid}: dangling source_ids {dangling}")
        if str(claim.get("occupation_slug") or "") not in occ_slugs:
            problems.append(f"claim {cid}: unknown occupation_slug")
        quote = claim.get("quote")
        if quote is not None:
            if not isinstance(quote, str) or not quote.strip():
                problems.append(f"claim {cid}: empty quote field")
            else:
                verified = any(
                    quote in extracts_checked.get(str(sid), "") for sid in sids
                )
                if not verified:
                    problems.append(f"claim {cid}: quote not byte-verbatim in any cited source extract")
        claim_type = claim.get("claim_type")
        if claim_type is not None and str(claim_type) not in KNOWN_CLAIM_TYPES:
            problems.append(f"claim {cid}: unknown claim_type {claim_type!r}")

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
        # Scope admission: every admitted posting must carry a literal
        # individual-contributor work-level decision with a rationale and no
        # people-management evidence (no compatibility default for older rows).
        work_level = posting.get("work_level")
        if work_level != WORK_LEVEL_ADMIT:
            problems.append(
                f"posting row work_level {work_level!r} is not {WORK_LEVEL_ADMIT!r} "
                f"(admitted postings must be individual-contributor scope)"
            )
        elif not isinstance(posting.get("work_level_reason"), str) or not posting["work_level_reason"].strip():
            problems.append("posting row missing or malformed work_level_reason for individual_contributor decision")
        management_quote = posting.get("people_management_quote")
        if not isinstance(management_quote, str):
            problems.append("posting row missing people_management_quote (empty string expected)")
        elif management_quote.strip():
            problems.append(
                f"posting row carries people-management evidence: {management_quote[:120]!r}"
            )

    for occ in data.occupations:
        slug = str(occ.get("slug") or "")
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


