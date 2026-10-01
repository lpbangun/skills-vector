#!/usr/bin/env python3
"""Structural release checks (HG03 / HG04 / HG05 / HG06 evidence collectors).

Usage: python3 acceptance/check_release.py <release_dir> [--min-postings N] [--json]
Reads a published release directory (JSON/JSONL artifacts) and reports
structural findings per gate. It never scores categories and never relaxes
BENCHMARK.md; advisor judgment against §4 anchors prevails.

Every finding carries the gate tag(s) it belongs to, so acceptance/gates.py can
attribute verdicts per gate. Exit 0 = no findings; exit 1 = findings (with
--strict, advisories also fail; gates.py runs with strict semantics and maps
advisories to UNKNOWN, never pass).

Release artifact contract (files may be .json array or .jsonl rows):
  manifest.json     required. {release_id, generated_at, agent_attribution,
                    role_scope{geography,seniority}, frequency_caveat,
                    skip_rules, sampling_scope?}
  occupations.*     required. [{slug,label,role_scope{geography,seniority},
                    growth_variants?:[...], stats{postings_dedup,employers_dedup,
                    sampled,total_seen}}]
  sources.*         required. [{id,url,retrieved_at,sha256,rights,inclusion,
                    exclusion_reason?,role_scope{geography,seniority},
                    extract_path?,extract_sha256?,source_type?}]
  postings.*        required. [{occupation_slug,variant?,source_id,employer,url,
                    posted_at?,dedup_key?}]
  claims.*          required. [{id,occupation_slug,statement,source_ids:[...],
                    quote?,claim_type?}]
  requirements.*    required. [{id,occupation_slug,priority,rationale,
                    evidence_claim_ids:[...],search_terms?,uncertainty}]
  extracts/<file>   stored extracts referenced by sources[].extract_path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

BAD_PATTERN_HINTS = (
    r"\bprevalence\b.*\d+%",
    r"\b\d+(?:\.\d+)?%\s*(?:of (?:the )?(?:market|demand|hiring))\b",
)

REQUIRED_VARIANTS = ("product-growth", "growth-marketing", "sales-account-executive")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_rows(release: Path, stem: str) -> list[dict[str, Any]]:
    return load_jsonl(release / f"{stem}.jsonl") or _load_json_array(release / f"{stem}.json")


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _finding(gates: tuple[str, ...], text: str) -> dict[str, Any]:
    return {"gates": list(gates), "detail": text}


def _role_scope_ok(role_scope: Any) -> bool:
    return isinstance(role_scope, dict) and bool(
        str(role_scope.get("geography") or "").strip()
    ) and bool(str(role_scope.get("seniority") or "").strip())


def run_checks(release: Path, min_postings: int | None = None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []

    def add(gates: tuple[str, ...], text: str) -> None:
        findings.append(_finding(gates, text))

    def advise(text: str) -> None:
        advisories.append({"gates": [], "detail": text})

    if not release.is_dir():
        add(("HG03", "HG04", "HG05", "HG06"), f"release dir not found: {release}")
        return {"release_dir": str(release), "findings": findings, "advisories": advisories}

    manifest = _load_json_dict(release / "manifest.json")
    occupations = load_rows(release, "occupations")
    sources = load_rows(release, "sources")
    postings = load_rows(release, "postings")
    claims = load_rows(release, "claims")
    requirements = load_rows(release, "requirements")

    if not manifest:
        add(("HG03", "HG05", "HG06"), "missing manifest.json")
    if not occupations:
        add(("HG04", "HG05"), "no occupations published")
    if not sources:
        add(("HG03",), "no sources published")
    if not postings:
        add(("HG05",), "no postings dataset published (deduplicated counts unverifiable)")
    if not claims:
        add(("HG06",), "no claims published — learning-priority linkage unverifiable")
    if not requirements:
        add(("HG06",), "no learning priorities (requirements) published")

    occ_slugs = {str(o.get("slug") or "") for o in occupations}
    source_ids = {str(s.get("id") or "") for s in sources}
    claim_ids = {str(c.get("id") or "") for c in claims}

    # ---- manifest-level checks ----
    if manifest:
        for field in ("release_id", "generated_at"):
            if not str(manifest.get(field) or "").strip():
                add(("HG03", "HG05", "HG06"), f"manifest missing {field}")
        if not str(manifest.get("agent_attribution") or "").strip():
            add(("HG03",), "manifest missing agent_attribution (synthesis must be agent-attributed)")
        if not _role_scope_ok(manifest.get("role_scope")):
            add(("HG04",), "manifest role_scope missing geography/seniority bounds")
        caveat = manifest.get("frequency_caveat")
        if not (caveat is True or str(caveat or "").strip()):
            add(("HG05",), "manifest missing frequency_caveat (posting frequency ≠ importance/proficiency/hire)")
        skip_rules = manifest.get("skip_rules")
        if not (isinstance(skip_rules, (dict, list)) and len(skip_rules) > 0):
            add(("HG06",), "manifest missing skip_rules (honest uncertain-skip rules)")
        sampling_scope = manifest.get("sampling_scope")
        if not (isinstance(sampling_scope, (dict, list)) and len(sampling_scope) > 0):
            advise("manifest lacks sampling_scope detail")

    # ---- occupations / variants / bounds (HG04) + stats (HG05) ----
    growth_slugs: list[str] = []
    for occ in occupations:
        slug = str(occ.get("slug") or "").strip()
        if not slug or not str(occ.get("label") or "").strip():
            add(("HG04",), f"occupation row missing slug/label: {occ}")
            continue
        if not _role_scope_ok(occ.get("role_scope")):
            add(("HG04",), f"occupation {slug}: role_scope missing geography/seniority bounds")
        if "growth" in slug:
            growth_slugs.append(slug)
            variants = occ.get("growth_variants") or []
            missing = [v for v in REQUIRED_VARIANTS if v not in variants]
            if missing:
                add(("HG04",), f"occupation {slug}: growth variants incomplete: missing {missing}")
            for v in variants:
                if not any(str(p.get("variant") or "") == v and str(p.get("occupation_slug") or "") == slug for p in postings):
                    add(("HG04",), f"occupation {slug}: variant {v} has no postings rows (variants not separated)")
        stats = occ.get("stats")
        if not isinstance(stats, dict):
            add(("HG05",), f"occupation {slug}: missing stats (dedup counts/denominators)")
        else:
            for field in ("postings_dedup", "employers_dedup", "sampled", "total_seen"):
                val = stats.get(field)
                if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                    add(("HG05",), f"occupation {slug}: stats.{field} not a non-negative integer")

    # ---- sources (HG03) ----
    extract_cache: dict[str, str] = {}
    for src in sources:
        sid = str(src.get("id") or "") or "<no-id>"
        for field in ("url", "retrieved_at", "sha256", "rights"):
            if not str(src.get(field) or "").strip():
                add(("HG03",), f"source {sid}: missing {field}")
        if "inclusion" not in src:
            add(("HG03",), f"source {sid}: missing inclusion record")
        elif src.get("inclusion") is False and not str(src.get("exclusion_reason") or "").strip():
            add(("HG03",), f"source {sid}: excluded without exclusion_reason")
        if not _role_scope_ok(src.get("role_scope")):
            add(("HG03",), f"source {sid}: role_scope missing geography/seniority record")
        extract_path = str(src.get("extract_path") or "").strip()
        if extract_path:
            ef = release / extract_path
            if not ef.is_file():
                add(("HG03",), f"source {sid}: extract_path {extract_path} not found in release")
            else:
                extract_cache[sid] = ef.read_text(encoding="utf-8", errors="replace")
                recorded = str(src.get("extract_sha256") or "").strip()
                if not recorded:
                    add(("HG03",), f"source {sid}: extract_sha256 required when extract_path present")
                elif recorded != sha256_file(ef):
                    add(("HG03",), f"source {sid}: extract_sha256 mismatch for {extract_path}")

    # quote byte-verification (C1: every published quote byte-verifiable)
    for claim in claims:
        quote = claim.get("quote")
        if not quote:
            continue
        cid = str(claim.get("id") or "") or "<no-id>"
        if not isinstance(quote, str) or not quote.strip():
            add(("HG03",), f"claim {cid}: empty quote field")
            continue
        verified = False
        for sid in claim.get("source_ids") or []:
            text = extract_cache.get(str(sid))
            if text is not None and quote in text:
                verified = True
                break
        if not verified:
            add(("HG03",), f"claim {cid}: quote not byte-verbatim in any cited source extract")

    # claims → sources linkage (HG03 provenance)
    for claim in claims:
        cid = str(claim.get("id") or "") or "<no-id>"
        sids = claim.get("source_ids")
        if not isinstance(sids, list) or not sids:
            add(("HG03",), f"claim {cid}: no source_ids (uncited claim)")
            continue
        dangling = [s for s in sids if str(s) not in source_ids]
        if dangling:
            add(("HG03",), f"claim {cid}: dangling source_ids {dangling}")
        if str(claim.get("occupation_slug") or "") not in occ_slugs:
            add(("HG03",), f"claim {cid}: unknown occupation_slug")

    # ---- postings / dedup counts / denominators (HG05) ----
    if postings and occupations:
        for p in postings:
            slug = str(p.get("occupation_slug") or "")
            if slug not in occ_slugs:
                add(("HG05",), f"posting row references unknown occupation_slug {slug!r}")
            sid = str(p.get("source_id") or "")
            if sid not in source_ids:
                add(("HG05",), f"posting row references unknown source_id {sid!r}")
            if not str(p.get("employer") or "").strip():
                add(("HG05",), "posting row missing employer (employer dedup impossible)")
            if not str(p.get("url") or "").strip():
                add(("HG05",), "posting row missing url")
        for occ in occupations:
            slug = str(occ.get("slug") or "")
            stats = occ.get("stats") if isinstance(occ.get("stats"), dict) else {}
            rows = [p for p in postings if str(p.get("occupation_slug") or "") == slug]
            def dedup_key(p: dict[str, Any]) -> str:
                return str(p.get("dedup_key") or "") or str(p.get("url") or "")
            computed_postings = len({dedup_key(p) for p in rows})
            computed_employers = len({str(p.get("employer") or "") for p in rows})
            declared_postings = stats.get("postings_dedup")
            declared_employers = stats.get("employers_dedup")
            if isinstance(declared_postings, int) and declared_postings != computed_postings:
                add(("HG05",), f"occupation {slug}: declared postings_dedup {declared_postings} != computed {computed_postings}")
            if isinstance(declared_employers, int) and declared_employers != computed_employers:
                add(("HG05",), f"occupation {slug}: declared employers_dedup {declared_employers} != computed {computed_employers}")
            sampled = stats.get("sampled")
            total_seen = stats.get("total_seen")
            if isinstance(sampled, int) and computed_postings > sampled:
                add(("HG05",), f"occupation {slug}: computed postings {computed_postings} exceed denominator sampled {sampled}")
            if isinstance(sampled, int) and isinstance(total_seen, int) and sampled > total_seen:
                add(("HG05",), f"occupation {slug}: sampled {sampled} exceed total_seen {total_seen}")
            if min_postings is not None and computed_postings < min_postings:
                add(("HG05",), f"occupation {slug}: {computed_postings} deduplicated postings < required minimum {min_postings}")

    # ---- learning priorities (HG06) ----
    evidence_text = json.dumps({"claims": claims, "sources": sources, "postings": postings}, default=str).lower()
    evidence_text += " ".join(extract_cache.values()).lower()
    for req in requirements:
        rid = str(req.get("id") or "") or "<no-id>"
        for field in ("rationale", "uncertainty"):
            if not str(req.get(field) or "").strip():
                add(("HG06",), f"requirement {rid}: missing {field} (rationale/uncertainty required)")
        cids = req.get("evidence_claim_ids")
        if not isinstance(cids, list) or not cids:
            add(("HG06",), f"requirement {rid}: no evidence_claim_ids (unlinked priority)")
        else:
            dangling = [c for c in cids if str(c) not in claim_ids]
            if dangling:
                add(("HG06",), f"requirement {rid}: dangling evidence_claim_ids {dangling}")
        if str(req.get("occupation_slug") or "") not in occ_slugs:
            add(("HG06",), f"requirement {rid}: unknown occupation_slug")
        terms = req.get("search_terms")
        if isinstance(terms, list):
            for term in terms:
                t = str(term).strip().lower()
                if t and t not in evidence_text:
                    add(("HG06",), f"requirement {rid}: search term {term!r} not backed by recorded evidence (goal-invented?)")

    # ---- fixture leak + prevalence patterns ----
    corpus_files = [
        "manifest.json", "claims.json", "claims.jsonl", "sources.json", "sources.jsonl",
        "postings.json", "postings.jsonl", "requirements.json", "requirements.jsonl",
        "occupations.json", "occupations.jsonl", "summary.md", "README.md",
    ]
    fixture_rows = 0
    for row in claims + sources + requirements + postings + occupations:
        if "fixture" in json.dumps(row, default=str).lower():
            fixture_rows += 1
    if fixture_rows:
        add(("HG03",), f"fixture references present in release rows: {fixture_rows} (fixtures are for negative tests only)")
    for name in corpus_files:
        p = release / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in BAD_PATTERN_HINTS:
            if re.search(pattern, text, re.IGNORECASE):
                add(("HG05",), f"prevalence-claim-shaped text in {name}: /{pattern}/")
    for md in release.glob("*.md"):
        if md.name in corpus_files:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in BAD_PATTERN_HINTS:
            if re.search(pattern, text, re.IGNORECASE):
                add(("HG05",), f"prevalence-claim-shaped text in {md.name}: /{pattern}/")

    return {"release_dir": str(release), "findings": findings, "advisories": advisories}


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("release_dir", type=Path)
    ap.add_argument("--min-postings", type=int, default=None)
    ap.add_argument("--strict", action="store_true", help="advisories also fail")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    result = run_checks(args.release_dir, args.min_postings)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for f in result["findings"]:
            print(f"FAIL[{'+'.join(f['gates'])}]: {f['detail']}")
        for a in result["advisories"]:
            print(f"ADVISORY: {a['detail']}")
        if not result["findings"] and not result["advisories"]:
            print("OK: no structural findings")
    if result["findings"]:
        return 1
    return 1 if args.strict and result["advisories"] else 0


if __name__ == "__main__":
    sys.exit(main())
