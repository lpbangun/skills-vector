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
            f"{scope.get('responsibility_scope') or scope.get('seniority') or 'responsibility scope not recorded'}; "
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


REGISTERED_ROLES = (
    {"slug": "hr-generalist", "label": "HR Generalist", "family": "People Operations & Talent"},
    {"slug": "growth-manager", "label": "Growth Manager", "family": "Go-to-market & growth"},
    {"slug": "account-executive", "label": "Account Executive", "family": "Sales"},
    {
        "slug": "forward-deployed-engineer",
        "label": "Forward Deployed Engineer",
        "family": "Applied engineering & customer delivery",
        "aliases": ["FDE", "forward-deployed engineer", "forward deployed engineer", "forward-deployed software engineer"],
        "alias_decisions": [
            {"label": "FDE / forward-deployed engineer", "decision": "accepted_canonical_alias"},
            {"label": "solutions engineer / sales engineer", "decision": "not_equivalent_by_title"},
            {"label": "customer success / implementation engineer", "decision": "not_equivalent_by_title"},
            {"label": "software engineer", "decision": "not_equivalent_by_title"},
        ],
        "official_anchor": {
            "code": "15-1252.00",
            "label": "Software Developers",
            "url": "https://www.onetonline.org/link/summary/15-1252.00",
            "status": "partial_taxonomy_anchor_only",
            "basis": "task-level comparison anchor; not an official FDE code, whole-role equivalence, or release finding",
        },
        "provisional": True,
        "registration_status": "mission-authorized-pilot",
        "human_review_status": "not_reviewed",
        "publication_status": "no_fde_findings_without_admissible_source_evidence",
    },
)

FACET_DIMENSIONS = ("employer_industry", "customer_industry", "sales_segment", "work_context", "employer_size", "employer_stage", "geography")
WORK_LEVEL_VALUES = ("individual_contributor", "people_manager", "unknown")
RESPONSIBILITY_BAND_VALUES = ("early_career", "independent_ic", "senior_strategic_ic", "people_management", "unknown")
EXPECTATION_DIMENSION_VALUES = ("task", "capability", "tool", "knowledge", "experience", "contextual_expectation", "demonstration", "credential", "unknown")
EXPECTATION_BASIS_VALUES = ("employer_requirement", "employer_preference", "emergent_signal", "unknown")
EXPECTATION_PROFICIENCY_VALUES = ("explicitly_stated", "not_stated", "unknown")
COMPONENT_DEFINITION_VERSION = "distribution/1"

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
    @staticmethod
    def _component_id(occupation: str, measure: str) -> str:
        return "cmp_" + sha256_text(
            f"market-component/{occupation}/{measure}/{COMPONENT_DEFINITION_VERSION}"
        )[:20]

    def component_references(self, occupation: str, release_id: str | None = None) -> list[dict[str, Any]]:
        """Return stable component references backed by the selected release's posting rows."""

        release = self.release(release_id)
        if release is None or occupation not in release.occupation_by_slug():
            return []
        postings = release.postings_by_occupation(occupation)
        if not postings:
            return []
        measures = ["responsibility_band_distribution"]
        measures.extend(
            f"context_sample_distribution:{dimension}"
            for dimension in FACET_DIMENSIONS
            if any(
                isinstance((row.get("context_dimensions") or {}).get(dimension), dict)
                and (row["context_dimensions"][dimension].get("status") == "present")
                and str(row["context_dimensions"][dimension].get("value") or "").strip()
                for row in postings
            )
        )
        labels = {
            "responsibility_band_distribution": "Responsibility-band sample distribution",
        }
        refs = []
        for measure in measures:
            context_dimension = measure.partition(":")[2] or None
            refs.append({
                "component_id": self._component_id(occupation, measure),
                "measure": measure,
                "definition_version": COMPONENT_DEFINITION_VERSION,
                "release_id": release.release_id,
                "occupation_slug": occupation,
                "title": labels[measure] if measure in labels else f"{context_dimension.replace('_', ' ').title()} sample distribution",
                "reference": f"{self._component_id(occupation, measure)}@{release.release_id}",
            })
        return refs

    def get_component(self, component_id: str, release_id: str | None = None) -> dict[str, Any]:
        """Return a deterministic source-backed component pinned to one immutable release."""

        if not re.fullmatch(r"cmp_[a-f0-9]{20}", component_id):
            return {"status": "unknown_component", "component_id": component_id, "detail": "Invalid component id."}
        if release_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", release_id):
            return {"status": "invalid_request", "component_id": component_id, "detail": "Invalid immutable release id."}

        release = self.release(release_id)
        if release is None:
            return {
                "status": "no_release",
                "component_id": component_id,
                "release_id": None,
                "detail": "No validated release is available for this component reference.",
            }
        ref = next(
            (
                candidate
                for occupation in release.occupations
                for candidate in self.component_references(str(occupation.get("slug") or ""), release.release_id)
                if candidate["component_id"] == component_id
            ),
            None,
        )
        if ref is None:
            return {
                "status": "unknown_component",
                "component_id": component_id,
                "release_id": release.release_id,
                "detail": "No component with this stable id exists in the selected immutable release.",
            }
        occupation = str(ref["occupation_slug"])
        raw_measure = str(ref["measure"])
        context_dimension = raw_measure.partition(":")[2] or None
        postings = release.postings_by_occupation(occupation)
        sources = release.source_by_id()
        occupation_row = release.occupation_by_slug()[occupation]
        observations: list[dict[str, Any]] = []
        grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        source_links: dict[str, dict[str, Any]] = {}
        for posting in postings:
            if context_dimension:
                dimension = (posting.get("context_dimensions") or {}).get(context_dimension)
                status = str(dimension.get("status") or "unknown") if isinstance(dimension, dict) else "unknown"
                literal = str(dimension.get("value") or "").strip() if isinstance(dimension, dict) else ""
                value = literal if status == "present" and literal else None
                category_status = "present" if value is not None else "unknown"
            else:
                value = str(posting.get("responsibility_band") or "unknown").strip() or "unknown"
                category_status = "recorded" if value != "unknown" else "unknown"
            category = (category_status, value)
            source_id = str(posting.get("source_id") or "")
            source = sources.get(source_id, {})
            registered_source_url = str(source.get("url") or "").strip()
            posting_url = str(posting.get("url") or "").strip()
            source_url = posting_url or registered_source_url
            link = {
                "source_id": source_id or None,
                "url": source_url or None,
                "source_record_url": registered_source_url or None,
                "publisher": str(source.get("publisher") or "") or None,
            }
            if source_id or source_url:
                source_links[f"{source_id}\x1f{source_url}"] = link
            observation = {
                "posting_id": str(posting.get("id") or posting.get("dedup_key") or ""),
                "employer": str(posting.get("employer") or ""),
                "title": str(posting.get("title") or ""),
                "variant": str(posting.get("variant") or "") or None,
                "posted_at": str(posting.get("posted_at") or "") or None,
                "value": value,
                "status": category_status,
                "source": link,
                "classification": (
                    (posting.get("context_dimensions") or {}).get(context_dimension) or {"status": "unknown"}
                    if context_dimension else posting.get("responsibility_evidence") or {"status": "unknown"}
                ),
            }
            observations.append(observation)
            grouped.setdefault(category, []).append(observation)
        denominator = len(postings)
        if context_dimension:
            category_order = sorted(
                grouped,
                key=lambda item: (item[0] == "unknown", (item[1] or "").casefold(), item[1] or ""),
            )
            calculation = {
                "unit": "admitted posting",
                "numerator": "number of admitted postings with this exact stored context value",
                "denominator": "all admitted posting rows for this occupation in the pinned release",
                "grouping": f"context_dimensions.{context_dimension}.value, only when status is present; otherwise unknown",
            }
            definition = (
                "Each admitted posting contributes once. A literal value is counted only when this dimension is "
                "stored with status=present; every missing or non-present value remains in the explicit unknown bucket."
            )
            measure_label = context_dimension.replace("_", " ")
        else:
            band_order = {value: index for index, value in enumerate(RESPONSIBILITY_BAND_VALUES)}
            category_order = sorted(
                grouped,
                key=lambda item: (band_order.get(item[1] or "", len(band_order)), (item[1] or "").casefold()),
            )
            calculation = {
                "unit": "admitted posting",
                "numerator": "number of admitted postings assigned to this stored responsibility_band value",
                "denominator": "all admitted posting rows for this occupation in the pinned release",
                "grouping": "responsibility_band; missing or blank values are unknown",
            }
            definition = (
                "Each admitted posting contributes once to its stored responsibility_band. Missing or blank values "
                "remain unknown; role titles and advertised years are not used to infer a band."
            )
            measure_label = "responsibility band"
        rows = []
        for status, value in category_order:
            members = grouped[(status, value)]
            row_sources = {
                f"{member['source'].get('source_id') or ''}\x1f{member['source'].get('url') or ''}": member["source"]
                for member in members
                if member["source"].get("source_id") or member["source"].get("url")
            }
            rows.append({
                "category": "unknown" if status == "unknown" else "recorded",
                "value": value if value is not None else "unknown",
                "status": status,
                "numerator": len(members),
                "denominator": denominator,
                "posting_ids": [member["posting_id"] for member in members],
                "source_links": [row_sources[key] for key in sorted(row_sources)],
            })
        posting_dates = sorted(str(posting.get("posted_at") or "").strip() for posting in postings if posting.get("posted_at"))
        occupation_label = str(occupation_row.get("label") or occupation)
        component = {
            "status": "ok",
            "schema": "market-component/1",
            **ref,
            "kind": "sample_distribution",
            "context_dimension": context_dimension,
            "occupation_label": occupation_label,
            "immutable": True,
            "sample_scope": {
                "role_scope": occupation_row.get("role_scope") or {},
                "sampled_at": str(occupation_row.get("sampled_at") or "") or None,
                "posting_date_field": "posted_at",
                "posting_date_start": posting_dates[0] if posting_dates else None,
                "posting_date_end": posting_dates[-1] if posting_dates else None,
                "admitted_postings": denominator,
                "admitted_employers": (occupation_row.get("stats") or {}).get("employers_dedup"),
            },
            "numerator": sum(row["numerator"] for row in rows),
            "denominator": denominator,
            "calculation": calculation,
            "definition": definition,
            "rows": rows,
            "observations": observations,
            "source_links": [source_links[key] for key in sorted(source_links)],
            "limitations": [
                str(occupation_row.get("sampling_note") or "The sample is limited to admitted source-backed postings."),
                "Counts describe the pinned admitted sample only, not workforce prevalence or the wider labor market.",
                "Unknown means the source-backed classification is missing or non-present; it does not mean the role lacks this characteristic.",
                "This measure is not an importance, proficiency, competence, employability, or trend estimate.",
                "Values are release-specific; compare roles only as separately scoped raw counts, never as semantic equivalence.",
            ],
        }
        return component

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
        release_info = {
            "status": "ok",
            "core_version": CORE_VERSION,
            "schema_version": API_SCHEMA_VERSION,
            "has_release": True,
            "release_id": release.release_id,
            "release_schema": str(manifest.get("release_schema") or RELEASE_SCHEMA_VERSION),
            "generated_at": str(manifest.get("generated_at") or ""),
            "published_at": str(manifest.get("published_at") or ""),
            "agent_attribution": str(manifest.get("agent_attribution") or ""),
            "publication_policy": manifest.get("publication_policy"),
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
        changes_path = release.root / "changes.json"
        if changes_path.is_file() and changes_path.stat().st_size > 0:
            release_info["changes_path"] = f"/release/releases/{release.release_id}/changes.json"
        return release_info

    def list_occupations(self, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        if release is None:
            return {
                "status": "no_release",
                "release_id": None,
                "occupations": [
                    {
                        **profile,
                        "publication_status": profile.get("publication_status", "not_in_current_release"),
                        "status": "pilot_only" if profile.get("provisional") else "not_in_current_release",
                        "counts": {"claims": 0, "foundation_claims": 0, "demand_claims": 0, "learning_priorities": 0, "sources": 0},
                    }
                    for profile in REGISTERED_ROLES
                ],
                "detail": "No validated release is published yet; registered role profiles are not findings.",
            }
        registered = {str(row["slug"]): row for row in REGISTERED_ROLES}
        occupations = []
        present_slugs: set[str] = set()
        for occ in release.occupations:
            slug = str(occ.get("slug"))
            present_slugs.add(slug)
            claims = release.claims_by_occupation(slug)
            profile = registered.get(slug, {})
            occupations.append(
                {
                    "slug": slug,
                    "label": str(occ.get("label")),
                    "family": str(occ.get("family") or ""),
                    "aliases": occ.get("aliases") or profile.get("aliases") or [],
                    "role_scope": occ.get("role_scope") or {},
                    "stats": occ.get("stats") or {},
                    "growth_variants": occ.get("growth_variants") or [],
                    "provisional": occ.get("provisional") is True,
                    "registration_status": occ.get("registration_status", "published_role"),
                    "human_review_status": occ.get("human_review_status"),
                    "publication_status": occ.get("publication_status", "published"),
                    "official_anchor": occ.get("official_anchor"),
                    "coverage": occ.get("coverage"),
                    "status": "published",
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
        for slug, profile in registered.items():
            if slug in present_slugs:
                continue
            occupations.append(
                {
                    **profile,
                    "status": "pilot_only" if profile.get("provisional") else "not_in_current_release",
                    "publication_status": profile.get("publication_status", "not_in_current_release"),
                    "counts": {"claims": 0, "foundation_claims": 0, "demand_claims": 0, "learning_priorities": 0, "sources": 0},
                }
            )
        return {"status": "ok", "release_id": release.release_id, "occupations": occupations}
    def brief(self, release_id: str | None = None) -> dict[str, Any]:
        """Role index and published status for the brief-first surface."""

        roles = self.list_occupations(release_id)
        return {
            "status": roles["status"],
            "release": self.release_info(release_id),
            "roles": roles["occupations"],
            "notice": "Published sample counts describe admitted evidence only, not workforce prevalence or individual suitability.",
        }

    @staticmethod
    def _posting_bundle(posting: dict[str, Any], release: ReleaseData) -> dict[str, Any]:
        sources = release.source_by_id()
        source_id = str(posting.get("source_id") or "")
        source = sources.get(source_id, {})
        role = release.occupation_by_slug().get(str(posting.get("occupation_slug") or ""), {})
        return {
            "occupation_slug": str(posting.get("occupation_slug") or ""),
            "occupation_label": str(role.get("label") or ""),
            "posting_id": str(posting.get("id") or posting.get("dedup_key") or posting.get("url") or ""),
            "employer": str(posting.get("employer") or ""),
            "title": str(posting.get("title") or ""),
            "location": str(posting.get("location") or ""),
            "variant": posting.get("variant"),
            "work_level": str(posting.get("work_level") or "unknown"),
            "work_level_reason": str(posting.get("work_level_reason") or ""),
            "work_level_evidence": posting.get("work_level_evidence") or {},
            "responsibility_band": str(posting.get("responsibility_band") or "unknown"),
            "responsibility_evidence": posting.get("responsibility_evidence") or {},
            "advertised_experience": posting.get("advertised_experience") or {"status": "unknown", "value": []},
            "context_dimensions": posting.get("context_dimensions") or {},
            "expectations": posting.get("expectations") or [],
            "excerpt": str(posting.get("excerpt") or ""),
            "posted_at": str(posting.get("posted_at") or ""),
            "source": {
                "id": source_id,
                "url": str(source.get("url") or posting.get("url") or ""),
                "publisher": str(source.get("publisher") or ""),
                "retrieved_at": str(source.get("retrieved_at") or ""),
                "sha256": str(source.get("sha256") or ""),
                "extract_sha256": str(source.get("extract_sha256") or ""),
                "rights": str(source.get("rights") or ""),
                "inclusion": source.get("inclusion"),
            },
        }

    def refine(
        self,
        *,
        occupation: str | None = None,
        filters: dict[str, str] | None = None,
        query: str = "",
        limit: int = 50,
        release_id: str | None = None,
    ) -> dict[str, Any]:
        """Filter admitted, source-backed rows; return raw counts, including unknown values."""

        release = self.release(release_id)
        if release is None:
            return {
                "status": "no_release",
                "release_id": None,
                "rows": [],
                "count": 0,
                "facets": {},
                "expectation_identities": [],
            }
        if occupation is not None and occupation not in release.occupation_by_slug():
            registered = {str(row["slug"]) for row in REGISTERED_ROLES}
            return {
                "status": "not_in_current_release" if occupation in registered else "unknown_occupation",
                "occupation": occupation,
                "rows": [],
                "count": 0,
                "facets": {},
                "expectation_identities": [],
            }
        filters = {str(key): str(value).strip() for key, value in (filters or {}).items() if value}
        postings = [
            row for row in release.postings
            if occupation is None or str(row.get("occupation_slug") or "") == occupation
        ]
        sources_by_id = release.source_by_id()
        facets: dict[str, dict[str, int]] = {}
        identity_records: dict[str, dict[str, Any]] = {}

        def add_count(facet: str, value: str) -> None:
            values = facets.setdefault(facet, {})
            values[value] = values.get(value, 0) + 1

        for row in postings:
            add_count("work_level", str(row.get("work_level") or "unknown"))
            add_count("responsibility_band", str(row.get("responsibility_band") or "unknown"))
            expectations = [item for item in (row.get("expectations") or []) if isinstance(item, dict)]
            for facet, key in (
                ("expectation_dimension", "dimension"),
                ("expectation_basis", "basis"),
                ("expectation_proficiency", "proficiency"),
            ):
                values = {str(item.get(key) or "unknown") for item in expectations} or {"unknown"}
                for value in values:
                    add_count(facet, value)

            seen_identity_ids: set[str] = set()
            seen_identity_bases: set[tuple[str, str]] = set()
            for item in expectations:
                identity_id = str(item.get("identity_id") or "").strip()
                if not identity_id:
                    continue
                record = identity_records.setdefault(
                    identity_id,
                    {
                        "identity_id": identity_id,
                        "source_wordings": set(),
                        "dimensions": set(),
                        "kinds": set(),
                        "expectation_ids": set(),
                        "relationship_ids": set(),
                        "identity_methods": set(),
                        "relationship_methods": set(),
                        "postings_in_sample": 0,
                        "basis_counts": {},
                        "observations": [],
                    },
                )
                wording = str(item.get("source_wording") or item.get("label") or "")
                if wording:
                    record["source_wordings"].add(wording)
                dimension = str(item.get("dimension") or "")
                if dimension:
                    record["dimensions"].add(dimension)
                kind = str(item.get("kind") or "")
                if kind:
                    record["kinds"].add(kind)
                expectation_id = str(item.get("expectation_id") or "").strip()
                relationship_id = str(item.get("relationship_id") or "").strip()
                identity_method = str(item.get("identity_method") or "").strip()
                relationship_method = str(item.get("relationship_method") or "").strip()
                if expectation_id:
                    record["expectation_ids"].add(expectation_id)
                if relationship_id:
                    record["relationship_ids"].add(relationship_id)
                if identity_method:
                    record["identity_methods"].add(identity_method)
                if relationship_method:
                    record["relationship_methods"].add(relationship_method)
                source_id = str(item.get("source_id") or row.get("source_id") or "")
                source = sources_by_id.get(source_id, {})
                record["observations"].append(
                    {
                        "identity_id": identity_id,
                        "expectation_id": expectation_id or None,
                        "relationship_id": relationship_id or None,
                        "identity_method": identity_method or None,
                        "relationship_method": relationship_method or None,
                        "posting_id": str(row.get("id") or ""),
                        "source_id": source_id,
                        "source_url": str(source.get("url") or row.get("url") or ""),
                        "employer": str(row.get("employer") or ""),
                        "variant": str(row.get("variant") or ""),
                        "location": str(row.get("location") or ""),
                        "title": str(row.get("title") or ""),
                        "source_wording": wording,
                        "dimension": dimension or "unknown",
                        "kind": kind or "unknown",
                        "basis": str(item.get("basis") or "unknown"),
                        "proficiency": str(item.get("proficiency") or "unknown"),
                        "proficiency_quote": item.get("proficiency_quote"),
                        "work_level": str(row.get("work_level") or "unknown"),
                        "responsibility_band": str(row.get("responsibility_band") or "unknown"),
                        "advertised_experience": row.get("advertised_experience"),
                        "context_dimensions": row.get("context_dimensions"),
                    }
                )
                basis = str(item.get("basis") or "unknown")
                if identity_id not in seen_identity_ids:
                    record["postings_in_sample"] += 1
                    seen_identity_ids.add(identity_id)
                if (identity_id, basis) not in seen_identity_bases:
                    basis_counts = record["basis_counts"]
                    basis_counts[basis] = basis_counts.get(basis, 0) + 1
                    seen_identity_bases.add((identity_id, basis))
            for identity_id in seen_identity_ids:
                add_count("expectation_identity", identity_id)

            experience = row.get("advertised_experience") or {}
            add_count(
                "experience_status",
                str(experience.get("status") or "unknown") if isinstance(experience, dict) else "unknown",
            )
            for field_name in FACET_DIMENSIONS:
                context = (row.get("context_dimensions") or {}).get(field_name)
                if isinstance(context, dict):
                    add_count(f"{field_name}_status", str(context.get("status") or "unknown"))
                    if context.get("status") == "present" and context.get("value"):
                        add_count(f"{field_name}_value", str(context["value"]))
                else:
                    add_count(f"{field_name}_status", "unknown")

        tokens = tokenize(query)
        expectation_filter_fields = {
            "expectation_dimension": "dimension",
            "expectation_basis": "basis",
            "expectation_proficiency": "proficiency",
        }

        def matches(row: dict[str, Any]) -> bool:
            if filters.get("work_level") and str(row.get("work_level") or "unknown") != filters["work_level"]:
                return False
            if filters.get("responsibility_band") and str(row.get("responsibility_band") or "unknown") != filters["responsibility_band"]:
                return False
            expectations = [item for item in (row.get("expectations") or []) if isinstance(item, dict)]
            for filter_name, field_name in expectation_filter_fields.items():
                required = filters.get(filter_name)
                if required and not any(str(item.get(field_name) or "unknown") == required for item in expectations):
                    if required != "unknown" or expectations:
                        return False
            required_identity = filters.get("expectation_identity")
            if required_identity and not any(
                str(item.get("identity_id") or "").strip() == required_identity for item in expectations
            ):
                return False
            experience = row.get("advertised_experience") or {}
            experience_values = experience.get("value") or [] if isinstance(experience, dict) else []
            experience_status = str(experience.get("status") or "unknown") if isinstance(experience, dict) else "unknown"
            if filters.get("experience_status") and experience_status != filters["experience_status"]:
                return False
            experience_filter = filters.get("experience", "").casefold()
            if experience_filter and not any(experience_filter in str(value).casefold() for value in experience_values):
                return False
            context_name = filters.get("context_dimension")
            context = (row.get("context_dimensions") or {}).get(context_name) if context_name else None
            if context_name and context_name not in FACET_DIMENSIONS:
                return False
            if context_name and filters.get("context_status"):
                status = str(context.get("status") or "unknown") if isinstance(context, dict) else "unknown"
                if status != filters["context_status"]:
                    return False
            context_value = filters.get("context_value", "").casefold()
            if context_value and (
                not isinstance(context, dict)
                or context.get("status") != "present"
                or context_value not in str(context.get("value") or "").casefold()
            ):
                return False
            if tokens:
                reason = row.get("responsibility_evidence") or {}
                parts = [
                    str(row.get("title") or ""), str(row.get("employer") or ""), str(row.get("location") or ""),
                    str(row.get("excerpt") or ""), str(row.get("work_level_reason") or ""),
                    str(reason.get("reason") or "") if isinstance(reason, dict) else "",
                    *(str(value) for value in experience_values),
                    *(str(item.get("source_wording") or "") for item in expectations),
                    *(
                        str((row.get("context_dimensions") or {}).get(field, {}).get("value") or "")
                        for field in FACET_DIMENSIONS
                    ),
                ]
                haystack = " ".join(parts).casefold()
                if not all(token in haystack for token in tokens):
                    return False
            return True

        selected = [row for row in postings if matches(row)]
        selected.sort(
            key=lambda row: (
                str(row.get("employer") or "").casefold(),
                str(row.get("title") or "").casefold(),
                str(row.get("id") or ""),
            )
        )
        return {
            "status": "ok",
            "release_id": release.release_id,
            "occupation": occupation,
            "filters": filters,
            "query": query,
            "count": len(selected),
            "rows": [self._posting_bundle(row, release) for row in selected[: max(1, min(int(limit), 200))]],
            "facets": {key: dict(sorted(values.items())) for key, values in sorted(facets.items())},
            "expectation_identities": [
                {
                    **{
                        key: sorted(value) if isinstance(value, set) else value
                        for key, value in record.items()
                    },
                    "basis_counts": dict(sorted(record["basis_counts"].items())),
                }
                for _, record in sorted(
                    identity_records.items(),
                    key=lambda item: (
                        min(item[1]["dimensions"]) if item[1]["dimensions"] else "",
                        min(item[1]["source_wordings"]) if item[1]["source_wordings"] else item[0],
                        item[0],
                    ),
                )
            ],
            "sample_note": "Counts are from the admitted sample, not percentages, market prevalence, proficiency, or importance.",
        }

    def search(self, query: str, occupation: str | None = None, limit: int = 20, release_id: str | None = None) -> dict[str, Any]:
        """Deterministic global search over stored claims and admitted postings."""

        clean_query = re.sub(r"\s+", " ", str(query or "")).strip()
        if not clean_query:
            return {"status": "invalid_query", "query": "", "results": [], "count": 0}
        limit = max(1, min(int(limit), 100))
        claim_result = self.query(clean_query, occupation, limit=limit, release_id=release_id)
        posting_result = self.refine(
            occupation=occupation, query=clean_query, limit=limit, release_id=release_id
        )
        results = [{"kind": "claim", **claim} for claim in claim_result.get("claims", [])]
        results.extend({"kind": "posting", **row} for row in posting_result.get("rows", []))
        return {
            "status": "ok" if results else posting_result.get("status", claim_result.get("status", "no_hits")),
            "query": clean_query,
            "occupation": occupation,
            "release_id": claim_result.get("release_id"),
            "count": len(results),
            "results": results[:limit],
            "handoff": claim_result.get("handoff") if not results else None,
            "sample_note": "Search locates stored evidence; it does not run inference or assert market prevalence.",
        }

    def compare(self, occupations: list[str], release_id: str | None = None) -> dict[str, Any]:
        """Compare sample counts and explicit source-literal identities without semantic merging."""

        release = self.release(release_id)
        if release is None:
            return {"status": "no_release", "release_id": None, "roles": []}
        slugs = list(dict.fromkeys(str(slug) for slug in occupations if slug))
        if not 2 <= len(slugs) <= 4:
            return {"status": "invalid_selection", "detail": "select two to four distinct registered roles"}
        role_rows = self.list_occupations(release.release_id)["occupations"]
        by_slug = {str(row["slug"]): row for row in role_rows}
        unknown = [slug for slug in slugs if slug not in by_slug]
        if unknown:
            return {"status": "unknown_occupation", "occupations": unknown, "known_occupations": sorted(by_slug)}
        roles = []
        for slug in slugs:
            role = by_slug[slug]
            summary = self.refine(occupation=slug, limit=1, release_id=release.release_id)
            stats = role.get("stats") or {}
            roles.append(
                {
                    **role,
                    "postings_admitted": stats.get("postings_dedup"),
                    "employers_admitted": stats.get("employers_dedup"),
                    "facets": summary.get("facets") or {},
                    "expectation_identities": summary.get("expectation_identities") or [],
                    "published_findings": int((role.get("counts") or {}).get("claims") or 0),
                }
            )
        return {
            "status": "ok",
            "release_id": release.release_id,
            "roles": roles,
            "note": "Compare admitted counts, explicit exact-source-label identities, and observed dimensions; no synonym or semantic equivalence, coverage percentages, or individual fit scores.",
        }

    def occupation(self, slug: str, release_id: str | None = None) -> dict[str, Any]:
        release = self.release(release_id)
        if release is None:
            profile = next((row for row in REGISTERED_ROLES if str(row["slug"]) == slug), None)
            if profile is None:
                return {"status": "no_release", "occupation": slug, "detail": "No validated release is published yet."}
            return {
                "status": "pilot_only" if profile.get("provisional") else "no_release",
                "release_id": None,
                "occupation": profile,
                "component_refs": [],
                "detail": "This registration is not a published finding; no current validated release is available.",
            }
        occ = release.occupation_by_slug().get(slug)
        if occ is None:
            profile = next((row for row in REGISTERED_ROLES if str(row["slug"]) == slug), None)
            if profile:
                return {
                    "status": "pilot_only" if profile.get("provisional") else "not_in_current_release",
                    "release_id": release.release_id,
                    "occupation": profile,
                    "component_refs": [],
                    "detail": "No admitted findings for this registered role are present in the current release.",
                }
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
                    "expectation_dimension": claim.get("expectation_dimension") or "unknown",
                    "evidence_basis": claim.get("evidence_basis") or "unknown",
                    "source_ids": [str(s) for s in (claim.get("source_ids") or [])],
                    "confidence": str(claim.get("confidence") or ""),
                    "evidence": claim.get("evidence") or {},
                    "agent_run_id": str(claim.get("agent_run_id") or ""),
                    "asserted_at": str(claim.get("asserted_at") or ""),
                    "citation_url": self.citation_url(str(claim.get("id"))),
                    "release_id": release.release_id,
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
        expectation_sections = {}
        for section, basis in (
            ("employer_required", "employer_requirement"),
            ("employer_preferred", "employer_preference"),
            ("emerging_practice", "emergent_signal"),
            ("legal_credentials", "legal_requirement"),
        ):
            observations = [
                {
                    **expectation,
                    "posting_id": posting.get("id") or posting.get("dedup_key"),
                    "employer": posting.get("employer"),
                    "variant": posting.get("variant"),
                    "source_url": posting.get("url") or sources.get(str(posting.get("source_id") or ""), {}).get("url"),
                }
                for posting in postings
                for expectation in posting.get("expectations") or []
                if expectation.get("basis") == basis
                and (section != "legal_credentials" or expectation.get("dimension") == "credential")
            ]
            expectation_sections[section] = {
                "status": "present" if observations else "unavailable",
                "observations": observations,
                "limitation": "Source-literal observations, not normalized cross-role equivalence. A credential requirement alone is not a legal credential.",
            }
        return {
            "status": "ok",
            "release_id": release.release_id,
            "component_refs": self.component_references(slug, release.release_id),
            "occupation": {
                "slug": slug,
                "label": str(occ.get("label")),
                "family": str(occ.get("family") or ""),
                "aliases": occ.get("aliases") or [],
                "role_scope": occ.get("role_scope") or {},
                "stats": occ.get("stats") or {},
                "coverage": occ.get("coverage"),
                "sampling_note": str(occ.get("sampling_note") or ""),
                "sampled_at": str(occ.get("sampled_at") or ""),
                "provisional": occ.get("provisional") is True,
                "registration_status": occ.get("registration_status", "published_role"),
                "human_review_status": occ.get("human_review_status"),
                "publication_status": occ.get("publication_status", "published"),
                "official_anchor": occ.get("official_anchor"),
                "alias_decisions": occ.get("alias_decisions") or [],
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
                    self._posting_bundle(posting, release)
                    for posting in sorted(postings, key=lambda p: str(p.get("employer") or ""))[:40]
                ],
                "sources": source_docs(demand_claims),
            },
            "scope_claims": claim_docs(scope_claims),
            "expectation_sections": expectation_sections,
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
                "uv run skills-vector market research run "
                "--config <operator-config.json> "
                f"--occupation {occupation or '<registered-role-slug>'} "
                "--evidence-root <external-evidence-root>"
            ),
            "operator_command_note": "Local only; requires the externally approved provider config and reserved mission budget. This handoff does not execute research.",
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
                        **self._posting_bundle(posting, release),
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
