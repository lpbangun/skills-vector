"""Hash-verified independent adjudication for matched research arms."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class ComparisonError(ValueError):
    """Comparison or reviewer evidence failed closed validation."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read JSON receipt {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"receipt must contain one JSON object: {path}")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    raw = str(value or "").strip()
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ComparisonError(f"{label} must be an ISO-8601 timestamp") from exc
    if stamp.tzinfo is None:
        raise ComparisonError(f"{label} must include a timezone")
    return stamp.astimezone(UTC)


def _arm_directory(comparison: dict[str, Any], arm_name: str) -> Path:
    arm = comparison.get("arms", {}).get(arm_name)
    if not isinstance(arm, dict):
        raise ComparisonError(f"comparison has no arm {arm_name!r}")
    raw = Path(str(arm.get("slice_path") or ""))
    if not raw.is_absolute():
        raise ComparisonError("arm slice_path must be absolute")
    cursor = raw
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise ComparisonError("comparison arm path contains a symlink")
        cursor = cursor.parent
    directory = raw.resolve(strict=True)
    expected_parts = ("runs", str(comparison.get("comparison_id") or ""), "comparison", "arms", arm_name)
    if tuple(directory.parts[-len(expected_parts):]) != expected_parts or not directory.is_dir():
        raise ComparisonError("comparison arm is not in its immutable run evidence directory")
    return directory


def _verify_arm(comparison: dict[str, Any], arm_name: str) -> tuple[Path, dict[str, Any]]:
    arm = comparison.get("arms", {}).get(arm_name)
    if not isinstance(arm, dict):
        raise ComparisonError(f"comparison has no arm {arm_name!r}")
    directory = _arm_directory(comparison, arm_name)
    expected = arm.get("artifact_sha256")
    if not isinstance(expected, dict) or not expected:
        raise ComparisonError(f"comparison arm {arm_name!r} has no frozen artifact manifest")
    found: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ComparisonError(f"comparison arm contains symlink: {path}")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            found[relative] = _sha256(path)
    if found != expected:
        raise ComparisonError(f"comparison arm {arm_name!r} artifact hashes do not match the frozen manifest")
    if arm.get("source_passages_sha256") != comparison.get("source_passages_sha256"):
        raise ComparisonError(f"comparison arm {arm_name!r} does not match the frozen passage set")
    return directory, arm


def validate_adjudication(comparison_path: Path, adjudication_path: Path) -> dict[str, Any]:
    """Validate reviewer identity, freshness, exact freeze hash and promotion gate."""

    comparison_path = Path(comparison_path)
    adjudication_path = Path(adjudication_path)
    comparison = _read_object(comparison_path)
    review = _read_object(adjudication_path)
    if comparison.get("schema_version") != "skills-vector-matched-comparison/1":
        raise ComparisonError("unsupported comparison schema")
    if review.get("schema_version") != "skills-vector-independent-adjudication/1":
        raise ComparisonError("unsupported independent-adjudication schema")
    comparison_id = str(comparison.get("comparison_id") or "")
    frozen_hash = _sha256(comparison_path)
    if review.get("comparison_id") != comparison_id or review.get("comparison_sha256") != frozen_hash:
        raise ComparisonError("review receipt does not name the exact frozen comparison bytes")
    created = _timestamp(comparison.get("created_at"), "comparison.created_at")
    reviewed = _timestamp(review.get("reviewed_at"), "reviewed_at")
    now = datetime.now(UTC)
    if reviewed < created or reviewed > now + timedelta(minutes=5) or now - reviewed > timedelta(days=7):
        raise ComparisonError("review receipt is not fresh and contemporaneous with this comparison")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, dict):
        raise ComparisonError("reviewer object is required")
    reviewer_id = str(reviewer.get("id") or "").strip()
    reviewer_role = str(reviewer.get("role") or "").strip()
    if not reviewer_id or len(reviewer_id) > 120 or not reviewer_role or len(reviewer_role) > 120:
        raise ComparisonError("reviewer id and role must be nonempty and bounded")
    if reviewer.get("independent_of_pipeline") is not True or reviewer.get("not_author_of_candidate") is not True:
        raise ComparisonError("reviewer must attest independence from the pipeline and candidate authorship")
    selected = str(review.get("selected_arm") or "")
    if selected not in ("primary-only", "primary-plus-challenge"):
        raise ComparisonError("selected_arm must name one of the two frozen arms")
    for arm_name in ("primary-only", "primary-plus-challenge"):
        _verify_arm(comparison, arm_name)
    arm = comparison["arms"][selected]
    if arm.get("eligible_for_review") is not True or arm.get("policy_gate_reasons"):
        raise ComparisonError("selected arm does not satisfy candidate publication preconditions")
    rationale = str(review.get("decision_reason") or "").strip()
    if not rationale or len(rationale) > 1200:
        raise ComparisonError("decision_reason must be specific, nonempty and at most 1200 characters")

    challenger = comparison.get("challenger")
    if not isinstance(challenger, dict):
        raise ComparisonError("challenger adjudication evidence is missing")
    miss_ids = set(str(value) for value in challenger.get("nominated_source_backed_claim_ids", []))
    miss_decisions = review.get("miss_adjudications")
    if not isinstance(miss_decisions, dict) or set(miss_decisions) != miss_ids:
        raise ComparisonError("review must adjudicate every nominated challenger miss exactly once")
    if any(value not in ("retain_supported", "reject_unsupported", "not_selected") for value in miss_decisions.values()):
        raise ComparisonError("miss adjudication has an unsupported decision value")
    retained = {claim_id for claim_id, decision in miss_decisions.items() if decision == "retain_supported"}

    refinements = challenger.get("unsupported_refinements", [])
    refinement_ids = {
        str(row.get("id")) for row in refinements if isinstance(row, dict) and row.get("id")
    }
    refinement_decisions = review.get("unsupported_refinement_adjudications")
    if not isinstance(refinement_decisions, dict) or set(refinement_decisions) != refinement_ids:
        raise ComparisonError("review must adjudicate every proposed refinement exactly once")
    if any(value not in ("unsupported", "supported", "unresolved") for value in refinement_decisions.values()):
        raise ComparisonError("unsupported-refinement adjudication has an invalid decision value")
    if selected == "primary-plus-challenge":
        if not retained.issubset(miss_ids):
            raise ComparisonError("review retained a challenger miss not present in the frozen comparison")
        if any(value != "supported" for value in refinement_decisions.values()):
            raise ComparisonError("challenger selection is blocked by unsupported or unresolved refinements")
        candidate_claims = set(arm.get("claim_ids", []))
        if not retained.issubset(candidate_claims):
            raise ComparisonError("retained challenger misses are absent from the selected arm")

    return {
        "comparison": comparison,
        "comparison_sha256": frozen_hash,
        "adjudication": review,
        "selected_arm": selected,
        "reviewer_id": reviewer_id,
        "retained_supported_misses": sorted(retained),
    }


def materialize_reviewed_candidate(
    comparison_path: Path,
    adjudication_path: Path,
    destination: Path,
) -> dict[str, Any]:
    """Copy the selected immutable arm only after fresh independent adjudication."""

    validated = validate_adjudication(comparison_path, adjudication_path)
    arm_name = validated["selected_arm"]
    source, arm = _verify_arm(validated["comparison"], arm_name)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ComparisonError(f"candidate destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)
    copied = {
        path.relative_to(destination).as_posix(): _sha256(path)
        for path in sorted(destination.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    if copied != arm["artifact_sha256"]:
        shutil.rmtree(destination)
        raise ComparisonError("materialized candidate does not match its frozen arm artifacts")
    return {
        "candidate_path": str(destination.resolve()),
        "selected_arm": arm_name,
        "comparison_path": str(Path(comparison_path).resolve()),
        "comparison_sha256": validated["comparison_sha256"],
        "adjudication_path": str(Path(adjudication_path).resolve()),
        "reviewer_id": validated["reviewer_id"],
        "retained_supported_misses": validated["retained_supported_misses"],
        "candidate_artifact_sha256": copied,
    }


__all__ = ["ComparisonError", "materialize_reviewed_candidate", "validate_adjudication"]
