"""Publish validated occupation slices as immutable, versioned releases.

Publishing is local-only and fail-closed:

* a candidate release is assembled from the previous release plus the newly
  validated occupation slice, then validated structurally before anything is
  written into the served release tree;
* release directories are immutable (an existing ``releases/<id>`` with
  different content is never overwritten);
* citation documents are written once per claim id and never rewritten, so a
  claim URL keeps byte-identical content across later releases;
* ``current.json`` is swapped atomically; a failed publish leaves the last good
  release and pointer untouched.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from . import RELEASE_SCHEMA_VERSION
from .core import build_citation_document
from .release import (
    ReleaseData,
    SUPPORTING_DATASETS,
    canonical_json,
    load_json_dict,
    load_release,
    load_rows,
    sha256_text,
    validate_release,
)

SLICE_FILES = ("occupations", "sources", "postings", "claims", "requirements")

FREQUENCY_CAVEAT = (
    "Posting frequency is not importance, proficiency, hires or employability; "
    "counts describe the retrieved sample only and are not market prevalence."
)

DEFAULT_SKIP_RULES: list[dict[str, Any]] = [
    {
        "rule": "insufficient_admitted_postings",
        "threshold": 8,
        "action": "publish foundation/scope claims only; no demand claims for that occupation slice",
    },
    {
        "rule": "no_matching_stored_claim",
        "action": "query returns insufficient_evidence or unsupported_question with a bounded local research handoff; never a fabricated answer",
    },
    {
        "rule": "no_trend_claims_without_comparable_periods",
        "action": "trend language is rejected at challenge; paired comparable retrieval windows are required before any trend claim",
    },
]


class PublishError(RuntimeError):
    pass


@contextmanager
def _publication_lock(release_root: Path) -> Iterator[None]:
    """Serialize release-pointer writes across refresh, publish, and rollback."""

    uid = os.getuid()
    lock_dir = Path(tempfile.gettempdir()).resolve() / f"skills-vector-publish-{uid}"
    try:
        lock_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if lock_dir.is_symlink():
        raise PublishError("publication lock directory cannot be a symlink")
    info = lock_dir.stat()
    if info.st_uid != uid or info.st_mode & 0o077:
        raise PublishError("publication lock directory must be private to the current user")
    lock_name = hashlib.sha256(str(Path(release_root).resolve()).encode("utf-8")).hexdigest() + ".lock"
    lock_path = lock_dir / lock_name
    descriptor: int | None = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise PublishError("could not securely acquire the release publication lock") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def _rows_by_id(rows: list[dict[str, Any]], collection: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = str(row.get("id") or "").strip()
        if not identifier:
            raise PublishError(f"{collection} row without an id cannot be summarized")
        if identifier in indexed:
            raise PublishError(f"{collection} contains duplicate id {identifier!r}")
        indexed[identifier] = row
    return indexed


def _row_delta(
    previous_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    collection: str,
) -> dict[str, Any]:
    previous = _rows_by_id(previous_rows, collection)
    current = _rows_by_id(current_rows, collection)
    previous_ids = set(previous)
    current_ids = set(current)
    added = sorted(current_ids - previous_ids)
    removed = sorted(previous_ids - current_ids)
    updated = sorted(
        identifier
        for identifier in previous_ids & current_ids
        if canonical_json(previous[identifier]) != canonical_json(current[identifier])
    )
    return {
        "added": added,
        "removed": removed,
        "updated": updated,
        "counts": {"added": len(added), "removed": len(removed), "updated": len(updated)},
    }


def _recorded_definition_metadata(release: ReleaseData | None) -> dict[str, Any]:
    if release is None:
        return {}

    metadata: dict[str, Any] = {}
    release_schema = release.manifest.get("release_schema")
    if release_schema is not None:
        metadata["release_schema"] = release_schema
    definition_versions = release.manifest.get("definition_versions")
    if isinstance(definition_versions, dict) and definition_versions:
        metadata["definition_versions"] = {
            key: definition_versions[key] for key in sorted(definition_versions)
        }

    occupation_versions: dict[str, dict[str, Any]] = {}
    allocation_methods: dict[str, str] = {}
    for occupation in sorted(release.occupations, key=lambda row: str(row.get("slug") or "")):
        slug = str(occupation.get("slug") or "").strip()
        versions = {
            key: occupation[key]
            for key in sorted(occupation)
            if "definition_version" in key and occupation[key] is not None
        }
        if slug and versions:
            occupation_versions[slug] = versions
        selection = occupation.get("selection")
        allocation = selection.get("cap_allocation") if isinstance(selection, dict) else None
        method = allocation.get("method") if isinstance(allocation, dict) else None
        if slug and isinstance(method, str) and method:
            allocation_methods[slug] = method
    if occupation_versions:
        metadata["occupation_definition_versions"] = occupation_versions
    if allocation_methods:
        metadata["sampling_allocation_methods"] = allocation_methods

    expectation_methods: dict[str, set[str]] = {
        "mapping_method": set(),
        "identity_method": set(),
        "relationship_method": set(),
    }
    for posting in release.postings:
        expectations = posting.get("expectations")
        if not isinstance(expectations, list):
            continue
        for expectation in expectations:
            if not isinstance(expectation, dict):
                continue
            for field_name, values in expectation_methods.items():
                value = expectation.get(field_name)
                if isinstance(value, str) and value:
                    values.add(value)
    observed_methods = {
        field_name: sorted(values)
        for field_name, values in expectation_methods.items()
        if values
    }
    if observed_methods:
        metadata["expectation_methods"] = observed_methods
    return metadata


def _build_change_summary(previous: ReleaseData | None, current: ReleaseData) -> dict[str, Any]:
    return {
        "schema_version": "market-release-changes/1",
        "release_id": current.release_id,
        "previous_release_id": previous.release_id if previous else None,
        "definition_metadata": {
            "before": _recorded_definition_metadata(previous),
            "after": _recorded_definition_metadata(current),
        },
        "changes": {
            "claims": _row_delta(previous.claims if previous else [], current.claims, "claim"),
            "postings": _row_delta(previous.postings if previous else [], current.postings, "posting"),
            "sources": _row_delta(previous.sources if previous else [], current.sources, "source"),
        },
    }


def _read_slice(slice_dir: Path) -> dict[str, list[dict[str, Any]]]:
    data: dict[str, list[dict[str, Any]]] = {}
    for stem in SLICE_FILES:
        path = slice_dir / (stem + ".json")
        data[stem] = load_rows(path)
    return data


def _slice_occupation_slug(slice_data: dict[str, list[dict[str, Any]]]) -> str:
    occupations = slice_data.get("occupations") or []
    if len(occupations) != 1:
        raise PublishError(f"a research slice must contain exactly one occupation row, found {len(occupations)}")
    slug = str(occupations[0].get("slug") or "").strip()
    if not slug:
        raise PublishError("slice occupation row has no slug")
    return slug


def _source_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("id")) for row in rows}


def merge_slice(
    release_root: Path,
    slice_dir: Path,
    *,
    generated_at: str | None = None,
) -> Path:
    """Assemble a candidate release directory in ``release_root/.staging``.

    Returns the candidate path; validation is performed by :func:`publish_release`.
    """

    slice_data = _read_slice(slice_dir)
    slug = _slice_occupation_slug(slice_data)
    release_root = Path(release_root)
    current = load_current(release_root)

    occupations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    postings: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    requirements: list[dict[str, Any]] = []
    extras: dict[str, list[dict[str, Any]]] = {name: [] for name in SUPPORTING_DATASETS}
    extracted_files: dict[str, str] = {}

    if current is not None:
        for row in current.occupations:
            if str(row.get("slug")) != slug:
                occupations.append(row)
        for row in current.sources:
            if str(row.get("occupation_slug") or "") != slug and str(row.get("id")) not in _source_ids(slice_data["sources"]):
                sources.append(row)
        for row in current.postings:
            if str(row.get("occupation_slug")) != slug:
                postings.append(row)
        for row in current.claims:
            if str(row.get("occupation_slug")) != slug:
                claims.append(row)
        for row in current.requirements:
            if str(row.get("occupation_slug")) != slug:
                requirements.append(row)
        for name in SUPPORTING_DATASETS:
            for row in load_rows(current.root / name):
                if str(row.get("occupation_slug") or "") != slug and slug not in (row.get("occupations") or []):
                    extras[name].append(row)
        extracts_dir = current.root / "extracts"
        if extracts_dir.is_dir():
            for path in sorted(extracts_dir.iterdir()):
                if path.is_file():
                    extracted_files[path.name] = path.read_text(encoding="utf-8")

    occupations.extend(slice_data["occupations"])
    sources.extend(slice_data["sources"])
    postings.extend(slice_data["postings"])
    claims.extend(slice_data["claims"])
    requirements.extend(slice_data["requirements"])
    for name in SUPPORTING_DATASETS:
        path = slice_dir / name
        if path.is_file():
            extras[name].extend(load_rows(path))
    extracts_src = slice_dir / "extracts"
    if extracts_src.is_dir():
        for path in sorted(extracts_src.iterdir()):
            if path.is_file():
                extracted_files[path.name] = path.read_text(encoding="utf-8")

    # keep only extracts referenced by a merged source: a replaced slice must not
    # leave obsolete extract bytes (e.g. an old listing extract) in the new release
    referenced_extracts = {
        Path(str(source.get("extract_path") or "")).name
        for source in sources
        if str(source.get("extract_path") or "").strip()
    }
    extracted_files = {name: text for name, text in extracted_files.items() if name in referenced_extracts}

    if not occupations:
        raise PublishError("merged release has no occupations")

    staging_root = release_root.parent / ".market-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=staging_root))

    for stem, rows in (
        ("occupations", occupations),
        ("sources", sources),
        ("postings", postings),
        ("claims", claims),
        ("requirements", requirements),
    ):
        _write_json(candidate / (stem + ".json"), rows)
    for name, rows in extras.items():
        if rows:
            _write_json(candidate / name, rows)
    extracts_dir = candidate / "extracts"
    extracts_dir.mkdir(parents=True, exist_ok=True)
    for name, text in extracted_files.items():
        (extracts_dir / name).write_text(text, encoding="utf-8")

    # fix per-source extract hashes against the merged extract files
    for source in sources:
        extract_path = str(source.get("extract_path") or "").strip()
        if not extract_path:
            continue
        path = candidate / extract_path
        if path.is_file():
            source["extract_sha256"] = sha256_text(path.read_text(encoding="utf-8"))

    data = ReleaseData(
        root=candidate,
        manifest={},
        occupations=occupations,
        sources=sources,
        postings=postings,
        claims=claims,
        requirements=requirements,
        extracts={
            str(source["id"]): extracted_files[Path(str(source["extract_path"])).name]
            for source in sources if source.get("extract_path")
        },
    )
    content_hash = data.content_hash()
    date_part = (generated_at or _now_iso())[:10].replace("-", "")
    release_id = f"rel_{date_part}_{content_hash[:12]}"
    manifest = _build_manifest(release_id, occupations, extras, generated_at or _now_iso())
    (candidate / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = {
        "release_id": release_id,
        "content_hash": content_hash,
        "occupations": [str(row.get("slug")) for row in occupations],
        "base_release_id": current.release_id if current else None,
    }
    _write_json(candidate / "candidate.json", summary)
    return candidate


def _build_manifest(
    release_id: str,
    occupations: list[dict[str, Any]],
    extras: dict[str, list[dict[str, Any]]],
    generated_at: str,
) -> dict[str, Any]:
    lineage_runs: list[dict[str, Any]] = []
    for row in extras.get("lineage.json", []):
        if isinstance(row, dict) and row.get("run_id"):
            lineage_runs.append(row)
    lineage_runs.sort(key=lambda row: str(row.get("run_id")))
    attributions = sorted({
        f"{row.get('provider')}/{model} ({role}, fallback {row.get('model_fallback')})"
        for row in lineage_runs
        for role, model in (
            row.get("models_by_role") or {"recorded": row.get("model")}
        ).items()
        if model
    })
    sampling: dict[str, Any] = {}
    for occ in occupations:
        slug = str(occ.get("slug"))
        stats = occ.get("stats") if isinstance(occ.get("stats"), dict) else {}
        sampling[slug] = {
            "geography": (occ.get("role_scope") or {}).get("geography"),
            "responsibility_scope": (occ.get("role_scope") or {}).get("responsibility_scope"),
            "sampled_at": occ.get("sampled_at"),
            "boards_attempted": stats.get("boards_attempted"),
            "boards_used": stats.get("boards_used"),
            "total_seen": stats.get("total_seen"),
            "sampled": stats.get("sampled"),
            "postings_dedup": stats.get("postings_dedup"),
            "employers_dedup": stats.get("employers_dedup"),
            "excluded_total": stats.get("excluded_total"),
        }
    return {
        "release_schema": RELEASE_SCHEMA_VERSION,
        "release_id": release_id,
        "generated_at": generated_at,
        "published_at": generated_at,
        "agent_attribution": (
            "Agent-authored synthesis produced by the recorded research model(s): "
            + "; ".join(attributions if attributions else ["agent runtime unattributed"])
            + ". Run receipts, retrieval bytes and private transcripts are retained outside the public release."
        ),
        "role_scope": {
            "geography": "United States (per-occupation bounds in occupations[].role_scope)",
            "responsibility_scope": "per-occupation bounds in occupations[].role_scope",
        },
        "sampling_scope": {
            "method": (
                "Direct retrieval from allowlisted public occupational foundations and public employer "
                "job-board APIs selected by the discovery pass; no paid or metered search provider."
            ),
            "occupations": sampling,
            "dedup": "job id + canonical url + normalized employer + normalized title",
            "label": "sample counts describe the retrieved window only; not market prevalence",
        },
        "frequency_caveat": FREQUENCY_CAVEAT,
        "skip_rules": DEFAULT_SKIP_RULES
        + [
            rule
            for row in extras.get("disagreements.json", [])
            if isinstance(row, dict) and isinstance(rule := row.get("skip_rule"), dict)
        ],
        "run_ids": [str(row.get("run_id")) for row in lineage_runs],
    }


def load_current(release_root: Path) -> ReleaseData | None:
    pointer = load_json_dict(Path(release_root) / "current.json") or {}
    current = str(pointer.get("current") or "").strip()
    if not current:
        return None
    path = Path(release_root) / "releases" / current
    if not (path / "manifest.json").is_file():
        return None
    return load_release(path)


def publish_release(
    release_root: Path,
    candidate: Path,
    *,
    min_postings: dict[str, int] | None = None,
    expected_current: str | None = None,
) -> dict[str, Any]:
    """Validate and publish, optionally requiring an unchanged current release."""

    release_root = Path(release_root)
    candidate = Path(candidate)
    problems = validate_release(candidate, min_postings=min_postings)
    if problems:
        raise PublishError("candidate release failed validation:\n- " + "\n- ".join(problems))

    summary = load_json_dict(candidate / "candidate.json") or {}
    release_id = str(summary.get("release_id") or "").strip()
    if not release_id:
        raise PublishError("candidate has no release_id")
    if expected_current is None:
        if "base_release_id" not in summary:
            raise PublishError("candidate has no frozen base-release identity")
        base_release_id = summary.get("base_release_id")
        if base_release_id is not None and (
            not isinstance(base_release_id, str) or not base_release_id.strip()
        ):
            raise PublishError("candidate base-release identity is malformed")
        expected_current = base_release_id
    target = release_root / "releases" / release_id
    published_at = _now_iso()
    citations_written: list[str] = []
    with _publication_lock(release_root):
        pointer = load_json_dict(release_root / "current.json") or {"schema_version": 1, "releases": []}
        current_id = str(pointer.get("current") or "").strip()
        if current_id != (expected_current or ""):
            raise PublishError("current release changed while the candidate was staged")
        if target.exists():
            existing = load_release(target)
            if existing.content_hash() != str(summary.get("content_hash")):
                raise PublishError(f"immutability violation: {release_id} already exists with different content")
        else:
            candidate_release = load_release(candidate)
            if candidate_release.release_id != release_id:
                raise PublishError("candidate manifest release_id does not match candidate identity")
            previous_release = load_release(release_root / "releases" / current_id) if current_id else None
            if current_id and previous_release.release_id != current_id:
                raise PublishError("current release identity does not match the pointer")
            _write_json(candidate / "changes.json", _build_change_summary(previous_release, candidate_release))
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate.rename(target)

        published = load_release(target)
        sources = published.sources
        for claim in published.claims:
            claim_id = str(claim.get("id") or "").strip()
            if not claim_id:
                raise PublishError("claim without id cannot be cited")
            citation_path = f"/release/citations/{claim_id}.json"
            document = build_citation_document(
                published.release_id, claim, sources, published_at=published_at, citation_path=citation_path
            )
            citation_file = release_root / "citations" / f"{claim_id}.json"
            if citation_file.is_file():
                existing_doc = load_json_dict(citation_file) or {}
                compare_keys = ("claim_id", "statement", "quote", "sources", "scope", "variant")
                changed = any(
                    canonical_json(existing_doc.get(key)) != canonical_json(document.get(key))
                    for key in compare_keys
                )
                if changed:
                    raise PublishError(f"citation immutability violation for {claim_id}")
                continue
            _write_json(citation_file, document)
            citations_written.append(claim_id)

        versions = [row for row in pointer.get("releases") or [] if isinstance(row, dict)]
        if not any(str(row.get("release_id")) == release_id for row in versions):
            versions.append(
                {
                    "release_id": release_id,
                    "published_at": published_at,
                    "generated_at": str(published.manifest.get("generated_at") or ""),
                    "occupations": [str(occ.get("slug")) for occ in published.occupations],
                    "manifest_sha256": sha256_text(canonical_json(published.manifest)),
                }
            )
        pointer.update(
            {
                "schema_version": 1,
                "current": release_id,
                "updated_at": published_at,
                "releases": versions,
            }
        )
        _write_json(release_root / "current.json", pointer)
        _write_public_metadata(release_root)
    shutil.rmtree(candidate, ignore_errors=True)
    return {
        "status": "published",
        "release_id": release_id,
        "published_at": published_at,
        "occupations": [str(occ.get("slug")) for occ in published.occupations],
        "claims": len(published.claims),
        "citations_written": len(citations_written),
        "release_dir": str(target),
    }


def _write_public_metadata(release_root: Path) -> None:
    """Refresh the served agent guide/schema/readme (derived, safe to rewrite)."""

    from .guide import agent_guide_markdown, api_schema

    (release_root / "agent-guide.md").write_text(agent_guide_markdown(), encoding="utf-8")
    _write_json(release_root / "api-schema.json", api_schema())
    (release_root / "README.md").write_text(
        "# Published market releases\n\n"
        "* `current.json` — atomic pointer to the served release (never edited in place).\n"
        "* `releases/<release_id>/` — immutable release artifacts: manifest, occupations, sources,\n"
        "  postings, claims, requirements, extracts, exclusions, mappings, disagreements, lineage.\n"
        "  See this repository's `acceptance/check_release.py` for the artifact contract.\n"
        "* `citations/<claim_id>.json` — write-once claim citation documents; a cited URL keeps\n"
        "  byte-identical content across later releases.\n\n"
        "Served by `/api/*` (read-only) and directly as static files. No inference happens here.\n",
        encoding="utf-8",
    )


def rollback_current(release_root: Path, release_id: str) -> dict[str, Any]:
    """Point ``current.json`` back at an existing immutable release."""

    release_root = Path(release_root)
    target = release_root / "releases" / release_id
    if not (target / "manifest.json").is_file():
        raise PublishError(f"unknown release id {release_id!r}")
    with _publication_lock(release_root):
        pointer = load_json_dict(release_root / "current.json") or {"schema_version": 1, "releases": []}
        if not any(str(row.get("release_id")) == release_id for row in pointer.get("releases") or []):
            raise PublishError(f"release {release_id!r} is not in the pointer history")
        pointer["current"] = release_id
        pointer["updated_at"] = _now_iso()
        _write_json(release_root / "current.json", pointer)
    return {"status": "rolled-back", "current": release_id}


def validate_candidate(candidate: Path, *, min_postings: dict[str, int] | None = None) -> list[str]:
    return validate_release(candidate, min_postings=min_postings)


__all__ = [
    "PublishError",
    "FREQUENCY_CAVEAT",
    "DEFAULT_SKIP_RULES",
    "merge_slice",
    "publish_release",
    "rollback_current",
    "load_current",
    "validate_candidate",
]
