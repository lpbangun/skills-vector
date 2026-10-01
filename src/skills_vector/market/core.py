"""Shared catalog/query core: browse, cited answers, claims, inspector, handoff.

This module is the single implementation behind the frontend API, the deployed
read API, the CLI JSON output and the MCP tools. It performs no inference: it
selects, scores and cites stored release artifacts, and it never fabricates an
answer when evidence is missing — it returns an honest status plus a bounded,
local-only research handoff plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import API_SCHEMA_VERSION, CORE_VERSION, RELEASE_SCHEMA_VERSION
from .limits import RESEARCH_LIMITS
from .release import ReleaseData, canonical_json, load_json_dict, load_release, sha256_text

STOPWORDS = frozenset(
    """a an and are as at be by for from has have how i in is it its of on or that the their they this to was were what when where which who with you your do does about into over after before between during under more most other some such only own same too very can will just should now role roles job jobs work working""".split()
)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#_-]*")

INSPECTOR_SECTIONS = ("extracts", "admissions", "exclusions", "mappings", "disagreements", "lineage")

HANDOFF_STAGES = (
    "discovery",
    "retrieval",
    "extraction",
    "admission",
    "mapping",
    "reconciliation",
    "challenge",
    "release",
)


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(str(text).lower()) if token not in STOPWORDS and len(token) > 1]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _truncate(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _scope_basis(occupations: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for occ in occupations:
        scope = occ.get("role_scope") if isinstance(occ.get("role_scope"), dict) else {}
        stats = occ.get("stats") if isinstance(occ.get("stats"), dict) else {}
        parts.append(
            f"{occ.get('label') or occ.get('slug')}: {scope.get('geography') or 'unbounded geography'}; "
            f"{scope.get('seniority') or 'unbounded seniority'}; "
            f"{stats.get('postings_dedup', 0)} admitted postings / {stats.get('employers_dedup', 0)} employers "
            f"of {stats.get('sampled', 0)} sampled of {stats.get('total_seen', 0)} retrieved"
        )
    return " | ".join(parts) if parts else "no published sampling scope"


def build_citation_document(
    release_id: str,
    claim: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    published_at: str,
    citation_path: str,
) -> dict[str, Any]:
    """Immutable, versioned claim citation document (written once per claim id)."""

    source_ids = {str(sid) for sid in claim.get("source_ids") or []}
    source_docs = [
        {
            "id": str(source.get("id")),
            "url": str(source.get("url")),
            "publisher": str(source.get("publisher") or ""),
            "source_type": str(source.get("source_type") or ""),
            "retrieval_kind": str(source.get("retrieval_kind") or ""),
            "parent_source_id": str(source.get("parent_source_id") or ""),
            "retrieved_at": str(source.get("retrieved_at") or ""),
            "sha256": str(source.get("sha256") or ""),
            "rights": str(source.get("rights") or ""),
            "inclusion": source.get("inclusion"),
        }
        for source in sources
        if str(source.get("id")) in source_ids
    ]
    body = {
        "citation_schema": "market-citation/1",
        "claim_id": str(claim.get("id")),
        "release_id": release_id,
        "occupation_slug": str(claim.get("occupation_slug") or ""),
        "claim_type": str(claim.get("claim_type") or ""),
        "statement": str(claim.get("statement") or ""),
        "quote": claim.get("quote"),
        "variant": claim.get("variant"),
        "scope": claim.get("scope"),
        "sources": source_docs,
        "asserted_at": str(claim.get("asserted_at") or ""),
        "published_at": published_at,
        "citation_path": citation_path,
        "attribution": str(claim.get("agent_attribution") or ""),
        "labels": [
            "agent-authored synthesis over admitted public evidence; not practitioner validation",
            "posting frequency is not importance, proficiency, hires or employability",
        ],
    }
    body["content_sha256"] = sha256_text(canonical_json({k: v for k, v in body.items() if k != "content_sha256"}))
    return body


@dataclass
class CatalogStore:
    """Read-only view over a release root (``current.json`` + ``releases/``)."""

    root: Path
    base_url: str = ""

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.base_url = str(self.base_url or "").rstrip("/")
        self._cache: dict[tuple[str, int], ReleaseData] = {}

    # -- release pointer -------------------------------------------------

    def pointer(self) -> dict[str, Any]:
        pointer = load_json_dict(self.root / "current.json") or {}
        if not isinstance(pointer.get("releases"), list):
            pointer["releases"] = []
        return pointer

    def release_ids(self) -> list[str]:
        releases = self.pointer().get("releases") or []
        ids = [str(row.get("release_id")) for row in releases if isinstance(row, dict) and row.get("release_id")]
        releases_dir = self.root / "releases"
        if releases_dir.is_dir():
            for path in sorted(p.name for p in releases_dir.iterdir() if p.is_dir()):
                if path not in ids:
                    ids.append(path)
        return ids

    def current_release_id(self) -> str | None:
        pointer = self.pointer()
        current = str(pointer.get("current") or "").strip()
        if current:
            return current
        ids = self.release_ids()
        return ids[-1] if ids else None

    def release(self, release_id: str | None = None) -> ReleaseData | None:
        rid = release_id or self.current_release_id()
        if not rid:
            return None
        path = (self.root / "releases" / rid).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        manifest = path / "manifest.json"
        if not manifest.is_file():
            return None
        mtime = manifest.stat().st_mtime_ns
        key = (str(path), mtime)
        cached = self._cache.get(key)
        if cached is None:
            cached = load_release(path)
            self._cache = {k: v for k, v in self._cache.items() if k[0] != str(path)}
            self._cache[key] = cached
        return cached

    def _url(self, path: str) -> str:
        path = "/" + path.lstrip("/")
        return f"{self.base_url}{path}" if self.base_url else path

    def citation_url(self, claim_id: str) -> str:
        return self._url(f"/release/citations/{claim_id}.json")

    def release_manifest_url(self, release_id: str) -> str:
        return self._url(f"/release/releases/{release_id}/manifest.json")

    def claim_api_url(self, claim_id: str, release_id: str | None = None) -> str:
        suffix = f"&release={release_id}" if release_id else ""
        return self._url(f"/api/claim?id={claim_id}{suffix}")

    # -- browse ----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        release = self.release()
        return {
            "status": "ok" if release else "no_release",
            "core_version": CORE_VERSION,
            "schema_version": API_SCHEMA_VERSION,
            "release_id": release.release_id if release else None,
            "generated_at": str(release.manifest.get("generated_at")) if release else None,
            "occupations": [str(occ.get("slug")) for occ in (release.occupations if release else [])],
            "checked_at": _now_iso(),
        }

    def release_info(self, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        pointer = self.pointer()
        if release is None:
            return {
                "status": "no_release",
                "core_version": CORE_VERSION,
                "schema_version": API_SCHEMA_VERSION,
                "detail": "No validated release is published yet. Research runs publish releases; the site does not fabricate one.",
                "versions": pointer.get("releases") or [],
                "has_release": False,
            }
        manifest = release.manifest
        return {
            "status": "ok",
            "core_version": CORE_VERSION,
            "schema_version": API_SCHEMA_VERSION,
            "has_release": True,
            "release_id": release.release_id,
            "release_schema": str(manifest.get("release_schema") or RELEASE_SCHEMA_VERSION),
            "generated_at": str(manifest.get("generated_at") or ""),
            "published_at": str(manifest.get("published_at") or ""),
            "agent_attribution": str(manifest.get("agent_attribution") or ""),
            "role_scope": manifest.get("role_scope") or {},
            "sampling_scope": manifest.get("sampling_scope") or {},
            "frequency_caveat": manifest.get("frequency_caveat"),
            "skip_rules": manifest.get("skip_rules") or [],
            "occupations": [
                {
                    "slug": str(occ.get("slug")),
                    "label": str(occ.get("label")),
                    "role_scope": occ.get("role_scope") or {},
                    "stats": occ.get("stats") or {},
                    "growth_variants": occ.get("growth_variants") or [],
                }
                for occ in release.occupations
            ],
            "versions": pointer.get("releases") or [],
            "citation_path_pattern": "/release/citations/<claim_id>.json",
            "manifest_path": f"/release/releases/{release.release_id}/manifest.json",
            "lineage_path": f"/release/releases/{release.release_id}/lineage.json",
        }

    def list_occupations(self, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        if release is None:
            return {
                "status": "no_release",
                "release_id": None,
                "occupations": [],
                "detail": "No validated release is published yet.",
            }
        occupations = []
        for occ in release.occupations:
            slug = str(occ.get("slug"))
            claims = release.claims_by_occupation(slug)
            occupations.append(
                {
                    "slug": slug,
                    "label": str(occ.get("label")),
                    "family": str(occ.get("family") or ""),
                    "aliases": occ.get("aliases") or [],
                    "role_scope": occ.get("role_scope") or {},
                    "stats": occ.get("stats") or {},
                    "growth_variants": occ.get("growth_variants") or [],
                    "counts": {
                        "claims": len(claims),
                        "foundation_claims": len([c for c in claims if c.get("claim_type") == "foundation"]),
                        "demand_claims": len([c for c in claims if c.get("claim_type") == "advertised_demand"]),
                        "learning_priorities": len(release.requirements_by_occupation(slug)),
                        "sources": len(
                            {
                                str(sid)
                                for claim in claims
                                for sid in (claim.get("source_ids") or [])
                            }
                        ),
                    },
                    "run_ids": sorted({str(claim.get("agent_run_id")) for claim in claims if claim.get("agent_run_id")}),
                }
            )
        return {"status": "ok", "release_id": release.release_id, "occupations": occupations}

    def occupation(self, slug: str, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        if release is None:
            return {"status": "no_release", "occupation": slug, "detail": "No validated release is published yet."}
        occ = release.occupation_by_slug().get(slug)
        if occ is None:
            return {
                "status": "unknown_occupation",
                "occupation": slug,
                "known_occupations": [str(o.get("slug")) for o in release.occupations],
            }
        claims = release.claims_by_occupation(slug)
        requirements = sorted(
            release.requirements_by_occupation(slug), key=lambda row: (row.get("priority") if isinstance(row.get("priority"), int) else 99)
        )
        postings = release.postings_by_occupation(slug)
        sources = release.source_by_id()

        variant_counts: dict[str, dict[str, int]] = {}
        for posting in postings:
            variant = str(posting.get("variant") or "unspecified")
            bucket = variant_counts.setdefault(variant, {"postings": 0, "employers": 0, "_employers": set()})  # type: ignore[assignment]
            bucket["postings"] += 1  # type: ignore[operator]
            bucket["_employers"].add(str(posting.get("employer") or ""))  # type: ignore[union-attr]
        for variant, bucket in variant_counts.items():
            bucket["employers"] = len(bucket.pop("_employers"))  # type: ignore[arg-type]

        employers: dict[str, int] = {}
        for posting in postings:
            employer = str(posting.get("employer") or "")
            employers[employer] = employers.get(employer, 0) + 1

        def source_docs(claim_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            ids = [str(sid) for claim_row in claim_rows for sid in (claim_row.get("source_ids") or [])]
            seen: list[dict[str, Any]] = []
            for sid in ids:
                if sid in {str(s.get("id")) for s in seen}:
                    continue
                source = sources.get(sid)
                if source:
                    seen.append(
                        {
                            "id": sid,
                            "url": str(source.get("url")),
                            "publisher": str(source.get("publisher") or ""),
                            "source_type": str(source.get("source_type") or ""),
                            "retrieval_kind": str(source.get("retrieval_kind") or ""),
                            "parent_source_id": str(source.get("parent_source_id") or ""),
                            "retrieved_at": str(source.get("retrieved_at") or ""),
                            "sha256": str(source.get("sha256") or ""),
                            "rights": str(source.get("rights") or ""),
                            "inclusion": source.get("inclusion"),
                        }
                    )
            return seen

        def claim_docs(claim_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    "claim_id": str(claim.get("id")),
                    "statement": str(claim.get("statement") or ""),
                    "claim_type": str(claim.get("claim_type") or ""),
                    "variant": claim.get("variant"),
                    "quote": claim.get("quote"),
                    "source_ids": [str(s) for s in (claim.get("source_ids") or [])],
                    "confidence": str(claim.get("confidence") or ""),
                    "evidence": claim.get("evidence") or {},
                    "agent_run_id": str(claim.get("agent_run_id") or ""),
                    "asserted_at": str(claim.get("asserted_at") or ""),
                    "citation_url": self.citation_url(str(claim.get("id"))),
                }
                for claim in claim_rows
            ]

        foundation_claims = [c for c in claims if c.get("claim_type") == "foundation"]
        demand_claims = [c for c in claims if c.get("claim_type") == "advertised_demand"]
        scope_claims = [c for c in claims if c.get("claim_type") == "scope"]
        learning = [
            {
                "requirement_id": str(req.get("id")),
                "priority": req.get("priority"),
                "label": str(req.get("label") or ""),
                "learning_outcome": str(req.get("learning_outcome") or ""),
                "rationale": str(req.get("rationale") or ""),
                "evidence_rationale": str(req.get("evidence_rationale") or ""),
                "uncertainty": str(req.get("uncertainty") or ""),
                "confidence": str(req.get("confidence") or ""),
                "basis": str(req.get("basis") or ""),
                "variant": req.get("variant"),
                "search_terms": [str(t) for t in (req.get("search_terms") or [])],
                "evidence_claim_ids": [str(c) for c in (req.get("evidence_claim_ids") or [])],
                "citation_urls": [self.citation_url(str(c)) for c in (req.get("evidence_claim_ids") or [])],
            }
            for req in requirements
        ]
        return {
            "status": "ok",
            "release_id": release.release_id,
            "occupation": {
                "slug": slug,
                "label": str(occ.get("label")),
                "family": str(occ.get("family") or ""),
                "aliases": occ.get("aliases") or [],
                "role_scope": occ.get("role_scope") or {},
                "stats": occ.get("stats") or {},
                "growth_variants": occ.get("growth_variants") or [],
                "sampling_note": str(occ.get("sampling_note") or ""),
                "sampled_at": str(occ.get("sampled_at") or ""),
            },
            "foundations": {
                "claims": claim_docs(foundation_claims),
                "sources": source_docs(foundation_claims),
            },
            "demand": {
                "claims": claim_docs(demand_claims),
                "variant_counts": variant_counts,
                "employers": [
                    {"employer": employer, "postings": count}
                    for employer, count in sorted(employers.items(), key=lambda item: (-item[1], item[0]))
                ],
                "sample_postings": [
                    {
                        "posting_id": str(posting.get("id") or posting.get("dedup_key")),
                        "employer": str(posting.get("employer") or ""),
                        "title": str(posting.get("title") or ""),
                        "location": str(posting.get("location") or ""),
                        "variant": posting.get("variant"),
                        "seniority": str(posting.get("seniority") or ""),
                        "url": str(posting.get("url") or ""),
                        "posted_at": str(posting.get("posted_at") or ""),
                        "skills": [str(s) for s in (posting.get("skills") or [])],
                        "source_id": str(posting.get("source_id") or ""),
                    }
                    for posting in sorted(postings, key=lambda p: str(p.get("employer") or ""))[:40]
                ],
                "sources": source_docs(demand_claims),
            },
            "scope_claims": claim_docs(scope_claims),
            "learning_priorities": learning,
            "claims": claim_docs(claims),
            "lineage": self.evidence("lineage", slug, release_id=release.release_id).get("rows", []),
            "citation_path_pattern": "/release/citations/<claim_id>.json",
        }

    # -- query -----------------------------------------------------------

    def _claim_score(self, tokens: list[str], claim: dict[str, Any]) -> float:
        statement = str(claim.get("statement") or "").lower()
        tags = " ".join(str(t) for t in (claim.get("tags") or [])).lower()
        score = 0.0
        for token in tokens:
            if token in statement:
                score += 3.0
            if token in tags:
                score += 2.0
        return score

    def query(self, q: str, occupation: str | None = None, limit: int = 5, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        clean_q = re.sub(r"\s+", " ", str(q or "")).strip()
        if release is None:
            result = {
                "status": "insufficient_evidence",
                "query": clean_q,
                "occupation": occupation,
                "release_id": None,
                "answer": None,
                "claims": [],
                "sources": [],
                "learning_priorities": [],
                "confidence": {"level": "none", "basis": "no validated release is published yet"},
                "answer_kind": "none",
            }
            result["handoff"] = self.handoff(clean_q, occupation, "no validated release is published yet")
            return result
        tokens = tokenize(clean_q)
        known_slugs = {str(occ.get("slug")): occ for occ in release.occupations}
        if occupation and occupation not in known_slugs:
            result = {
                "status": "insufficient_evidence",
                "query": clean_q,
                "occupation": occupation,
                "release_id": release.release_id,
                "answer": None,
                "claims": [],
                "sources": [],
                "learning_priorities": [],
                "known_occupations": sorted(known_slugs),
                "confidence": {"level": "none", "basis": f"occupation {occupation!r} is not in the current release"},
                "answer_kind": "none",
            }
            result["handoff"] = self.handoff(clean_q, occupation, "occupation not covered by the current release")
            return result

        candidates = [
            claim
            for claim in release.claims
            if (occupation is None or str(claim.get("occupation_slug")) == occupation)
        ]
        scored = sorted(
            (
                (self._claim_score(tokens, claim), claim)
                for claim in candidates
            ),
            key=lambda item: (-item[0], str(item[1].get("id"))),
        )
        selected = [claim for score, claim in scored if score >= 3.0][: max(1, min(int(limit), 20))]

        role_hit = False
        if occupation:
            role_hit = True
        else:
            for slug, occ in known_slugs.items():
                haystack = " ".join(
                    [slug, str(occ.get("label") or ""), *[str(a) for a in (occ.get("aliases") or [])]]
                ).lower()
                if any(token in haystack for token in tokens):
                    role_hit = True
                    break
        requirement_hit = any(
            any(
                token in " ".join(
                    [str(req.get("label") or ""), *[str(t) for t in (req.get("search_terms") or [])], str(req.get("rationale") or "")]
                ).lower()
                for token in tokens
            )
            for req in release.requirements
            if occupation is None or str(req.get("occupation_slug")) == occupation
        )
        any_hit = bool(selected) or role_hit or requirement_hit or any(
            self._claim_score(tokens, claim) > 0 for claim in candidates
        )

        if not clean_q or len(tokens) == 0:
            status = "unsupported_question"
        elif selected:
            status = "cited_evidence"
        elif any_hit:
            status = "insufficient_evidence"
        else:
            status = "unsupported_question"

        sources_by_id = release.source_by_id()
        selected_source_ids: list[str] = []
        for claim in selected:
            for sid in claim.get("source_ids") or []:
                if str(sid) not in selected_source_ids:
                    selected_source_ids.append(str(sid))
        source_docs = [
            {
                "id": sid,
                "url": str(sources_by_id[sid].get("url")),
                "publisher": str(sources_by_id[sid].get("publisher") or ""),
                "source_type": str(sources_by_id[sid].get("source_type") or ""),
                "retrieval_kind": str(sources_by_id[sid].get("retrieval_kind") or ""),
                "parent_source_id": str(sources_by_id[sid].get("parent_source_id") or ""),
                "retrieved_at": str(sources_by_id[sid].get("retrieved_at") or ""),
                "sha256": str(sources_by_id[sid].get("sha256") or ""),
                "rights": str(sources_by_id[sid].get("rights") or ""),
            }
            for sid in selected_source_ids
            if sid in sources_by_id
        ]
        selected_claim_ids = {str(claim.get("id")) for claim in selected}
        related_requirements = [
            {
                "requirement_id": str(req.get("id")),
                "priority": req.get("priority"),
                "label": str(req.get("label") or ""),
                "learning_outcome": str(req.get("learning_outcome") or ""),
                "confidence": str(req.get("confidence") or ""),
                "citation_urls": [self.citation_url(str(c)) for c in (req.get("evidence_claim_ids") or [])],
            }
            for req in release.requirements
            if selected_claim_ids & {str(c) for c in (req.get("evidence_claim_ids") or [])}
        ]
        affected_occupations = [
            occ for occ in release.occupations
            if str(occ.get("slug")) in {str(claim.get("occupation_slug")) for claim in selected}
        ]
        claim_docs = [
            {
                "claim_id": str(claim.get("id")),
                "occupation_slug": str(claim.get("occupation_slug")),
                "statement": str(claim.get("statement") or ""),
                "claim_type": str(claim.get("claim_type") or ""),
                "variant": claim.get("variant"),
                "quote": claim.get("quote"),
                "source_ids": [str(s) for s in (claim.get("source_ids") or [])],
                "evidence": claim.get("evidence") or {},
                "confidence": str(claim.get("confidence") or ""),
                "citation_url": self.citation_url(str(claim.get("id"))),
                "claim_api_url": self.claim_api_url(str(claim.get("id")), release.release_id),
            }
            for claim in selected
        ]
        if status == "cited_evidence":
            answer = " ".join(str(claim.get("statement")) for claim in selected)
            answer_kind = "selected_stored_claims"
            confidence = {
                "level": "bounded",
                "basis": _scope_basis(affected_occupations),
                "labels": [
                    "agent-authored synthesis over admitted public evidence",
                    "posting frequency is not importance, proficiency, hires or employability",
                ],
            }
        elif status == "insufficient_evidence":
            answer = None
            answer_kind = "none"
            confidence = {
                "level": "none",
                "basis": "in-domain question; no stored claim supports an answer above the citation threshold",
            }
        else:
            answer = None
            answer_kind = "none"
            confidence = {
                "level": "none",
                "basis": "question outside the covered occupational scope",
            }

        result: dict[str, Any] = {
            "status": status,
            "query": clean_q,
            "occupation": occupation,
            "release_id": release.release_id,
            "answer": answer,
            "answer_kind": answer_kind,
            "claims": claim_docs,
            "sources": source_docs,
            "learning_priorities": related_requirements,
            "confidence": confidence,
            "core_version": CORE_VERSION,
        }
        if status != "cited_evidence":
            result["handoff"] = self.handoff(clean_q, occupation, result["confidence"]["basis"])
        return result

    def handoff(self, q: str, occupation: str | None, reason: str) -> dict[str, Any]:
        from .hosts import ALLOWED_SOURCE_HOSTS

        return {
            "kind": "bounded_research_request",
            "version": 1,
            "local_only": True,
            "mutates_public_state": False,
            "question": q,
            "occupation": occupation,
            "reason": reason,
            "suggested_stages": list(HANDOFF_STAGES),
            "source_policy": {
                "foundations": ["onetonline.org", "bls.gov"],
                "job_boards": ["greenhouse", "lever", "ashby", "smartrecruiters", "workable"],
                "allowlisted_hosts": list(ALLOWED_SOURCE_HOSTS),
                "paid_search": False,
            },
            "bounds": {key: RESEARCH_LIMITS[key] for key in (
                "max_retrievals",
                "max_model_calls",
                "max_response_bytes",
                "serialized_calls",
            )},
            "operator_command": (
                "env PYTHONPATH=src python3 -m skills_vector market research run "
                f"--occupation {occupation or '<slug>'} --question-file <plan.json> --overlay <runtime-overlay.yml>"
            ),
            "plan_url": self._url("/api/research-plan?q=" + re.sub(r"[^A-Za-z0-9 ]", "", q).strip().replace(" ", "+")
                                  + (f"&occupation={occupation}" if occupation else "")),
        }

    def research_plan(self, q: str, occupation: str | None = None) -> dict[str, Any]:
        return self.query(q, occupation, limit=1).get(
            "handoff",
            self.handoff(q, occupation, "explicit plan request"),
        )

    # -- claims and citations -------------------------------------------

    def claim(self, claim_id: str, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        if release is None:
            return {"status": "no_release", "claim_id": claim_id, "detail": "No validated release is published yet."}
        claim = release.claim_by_id().get(str(claim_id))
        if claim is None:
            return {
                "status": "unknown_claim",
                "claim_id": claim_id,
                "release_id": release.release_id,
                "known_claim_count": len(release.claims),
            }
        sources_by_id = release.source_by_id()
        cited = [
            {
                "id": str(sid),
                "url": str(sources_by_id[sid].get("url")),
                "publisher": str(sources_by_id[sid].get("publisher") or ""),
                "source_type": str(sources_by_id[sid].get("source_type") or ""),
                "retrieval_kind": str(sources_by_id[sid].get("retrieval_kind") or ""),
                "parent_source_id": str(sources_by_id[sid].get("parent_source_id") or ""),
                "retrieved_at": str(sources_by_id[sid].get("retrieved_at") or ""),
                "sha256": str(sources_by_id[sid].get("sha256") or ""),
                "rights": str(sources_by_id[sid].get("rights") or ""),
                "extract_sha256": str(sources_by_id[sid].get("extract_sha256") or ""),
            }
            for sid in (claim.get("source_ids") or [])
            if str(sid) in sources_by_id
        ]
        quote = claim.get("quote")
        quote_verified = bool(
            quote
            and any(quote in release.extracts.get(str(sid), "") for sid in (claim.get("source_ids") or []))
        )
        return {
            "status": "ok",
            "claim_id": str(claim.get("id")),
            "release_id": release.release_id,
            "occupation_slug": str(claim.get("occupation_slug") or ""),
            "claim_type": str(claim.get("claim_type") or ""),
            "variant": claim.get("variant"),
            "statement": str(claim.get("statement") or ""),
            "quote": quote,
            "quote_verified": quote_verified,
            "scope": claim.get("scope") or {},
            "evidence": claim.get("evidence") or {},
            "confidence": str(claim.get("confidence") or ""),
            "agent_run_id": str(claim.get("agent_run_id") or ""),
            "asserted_at": str(claim.get("asserted_at") or ""),
            "sources": cited,
            "citation_url": self.citation_url(str(claim.get("id"))),
            "claim_api_url": self.claim_api_url(str(claim.get("id")), release.release_id),
            "citations_resolvable_after_update": True,
        }

    def citation(self, claim_id: str) -> dict[str, Any]:
        path = self.root / "citations" / f"{claim_id}.json"
        document = load_json_dict(path)
        if document is None:
            return {
                "status": "unknown_citation",
                "claim_id": claim_id,
                "detail": "Citation documents are written once per published claim; this id has no citation.",
            }
        document = dict(document)
        document["status"] = "ok"
        return document

    # -- evidence inspector ---------------------------------------------

    def inspector(
        self,
        occupation: str | None = None,
        limit: int = 20,
        release_id: str | None = None,
        include_text: bool = True,
    ) -> dict[str, Any]:
        """All publishable supporting data in one document (HG07-shaped)."""

        release = self.release(release_id)
        if release is None:
            return {
                "status": "no_release",
                "release_id": None,
                "occupation": occupation,
                "sections_available": list(INSPECTOR_SECTIONS),
                "sections": {},
            }
        sections: dict[str, Any] = {}
        counts: dict[str, int] = {}
        for name in INSPECTOR_SECTIONS:
            payload = self.evidence(name, occupation, limit=limit, release_id=release.release_id, include_text=include_text)
            sections[name] = payload.get("rows", [])
            counts[name] = int(payload.get("count", 0))
        return {
            "status": "ok",
            "release_id": release.release_id,
            "occupation": occupation,
            "sections_available": list(INSPECTOR_SECTIONS),
            "counts": counts,
            "sections": sections,
        }

    def _load_extra(self, release: ReleaseData, name: str) -> list[dict[str, Any]]:
        return load_rows_safe(release.root / name)

    def evidence(
        self,
        section: str,
        occupation: str | None = None,
        limit: int = 50,
        release_id: str | None = None,
        include_text: bool = True,
    ) -> dict[str, Any]:
        release = self.release(release_id)
        if section == "all":
            return self.inspector(occupation, limit=limit, release_id=release_id, include_text=include_text)
        if section not in INSPECTOR_SECTIONS:
            return {
                "status": "unknown_section",
                "section": section,
                "sections": list(INSPECTOR_SECTIONS),
            }
        if release is None:
            return {"status": "no_release", "section": section, "release_id": None, "rows": []}
        limit = max(1, min(int(limit), 200))
        rows: list[dict[str, Any]] = []
        if section == "extracts":
            for source in release.sources:
                if occupation and str(source.get("occupation_slug") or "") not in ("", occupation):
                    continue
                sid = str(source.get("id"))
                text = release.extracts.get(sid, "")
                rows.append(
                    {
                        "source_id": sid,
                        "url": str(source.get("url")),
                        "publisher": str(source.get("publisher") or ""),
                        "source_type": str(source.get("source_type") or ""),
                        "retrieval_kind": str(source.get("retrieval_kind") or ""),
                        "parent_source_id": str(source.get("parent_source_id") or ""),
                        "retrieved_at": str(source.get("retrieved_at") or ""),
                        "sha256": str(source.get("sha256") or ""),
                        "rights": str(source.get("rights") or ""),
                        "inclusion": source.get("inclusion"),
                        "exclusion_reason": str(source.get("exclusion_reason") or ""),
                        "extract_path": str(source.get("extract_path") or ""),
                        "extract_sha256": str(source.get("extract_sha256") or ""),
                        "extract_chars": len(text),
                        "extract_text": _truncate(text, 4000) if include_text else None,
                    }
                )
        elif section == "admissions":
            for posting in release.postings:
                if occupation and str(posting.get("occupation_slug")) != occupation:
                    continue
                rows.append(
                    {
                        "posting_id": str(posting.get("id") or posting.get("dedup_key")),
                        "occupation_slug": str(posting.get("occupation_slug")),
                        "variant": posting.get("variant"),
                        "employer": str(posting.get("employer") or ""),
                        "title": str(posting.get("title") or ""),
                        "location": str(posting.get("location") or ""),
                        "seniority": str(posting.get("seniority") or ""),
                        "work_level": str(posting.get("work_level") or ""),
                        "work_level_reason": str(posting.get("work_level_reason") or ""),
                        "people_management_quote": str(posting.get("people_management_quote") or ""),
                        "url": str(posting.get("url") or ""),
                        "posted_at": str(posting.get("posted_at") or ""),
                        "source_id": str(posting.get("source_id") or ""),
                        "skills": [str(s) for s in (posting.get("skills") or [])],
                        "admission_reason": str(posting.get("admission_reason") or ""),
                        "dedup_key": str(posting.get("dedup_key") or ""),
                    }
                )
        elif section == "exclusions":
            for row in self._load_extra(release, "exclusions.json"):
                if occupation and str(row.get("occupation_slug") or "") not in ("", occupation):
                    continue
                rows.append(row)
        elif section == "mappings":
            for row in self._load_extra(release, "mappings.json"):
                if occupation and str(row.get("occupation_slug") or "") not in ("", occupation):
                    continue
                rows.append(row)
        elif section == "disagreements":
            for row in self._load_extra(release, "disagreements.json"):
                if occupation and str(row.get("occupation_slug") or "") not in ("", occupation):
                    continue
                rows.append(row)
        elif section == "lineage":
            for row in self._load_extra(release, "lineage.json"):
                if occupation and occupation not in (row.get("occupations") or []):
                    if str(row.get("occupation_slug") or "") != occupation:
                        continue
                rows.append(row)
        return {
            "status": "ok",
            "section": section,
            "release_id": release.release_id,
            "occupation": occupation,
            "count": len(rows),
            "rows": rows[:limit],
            "sections": list(INSPECTOR_SECTIONS),
        }

def load_rows_safe(path: Path) -> list[dict[str, Any]]:
    from .release import load_rows

    try:
        return load_rows(path)
    except (OSError, ValueError):
        return []
