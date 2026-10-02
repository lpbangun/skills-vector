"""Operator-only bounded refresh with frozen auto-publication gates.

The scheduled caller is explicit (CLI ``--once``); this module installs no job and
is not reachable from public HTTP or MCP reads. Research uses one caller-supplied
MissionBudget across all four fixed roles. A due refresh always stages every role
before it can atomically move the existing release pointer.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import uuid4

from . import RELEASE_SCHEMA_VERSION
from .budgeting import MissionBudget
from .core import CatalogStore
from .fetch import urllib_transport
from .hosts import host_kind, url_problem
from .limits import RESEARCH_LIMITS
from .publish import PublishError, merge_slice, publish_release, rollback_current
from .release import ReleaseData, canonical_json, load_release, sha256_bytes, sha256_file, sha256_text, validate_release
from .research_config import ResearchRuntimeConfig

REFRESH_SCHEMA = "skills-vector-operator-refresh/1"
REFRESH_POLICY_VERSION = "existing-four-role-auto-publication/1"
DEFAULT_SOURCE_CACHE_AGE = timedelta(days=30)
MAX_SOURCE_CACHE_ENTRIES = 32
MAX_CHECKPOINT_BYTES = 256 * 1024
FIXED_ROLES = ("account-executive", "forward-deployed-engineer", "growth-manager", "hr-generalist")
RETENTION_SCHEMA = "skills-vector-four-role-stage-retention/1"
RETENTION_RULE = "c-di-02-four-role-retention/1"
DEFINITION_VERSIONS = {
    "release_schema": RELEASE_SCHEMA_VERSION,
    "expectation_id": "expectation/2",
    "expectation_mapping": "exact-normalized-label/2",
    "expectation_identity": "source-literal-identity/1",
    "expectation_relationship": "posting-source-expectation/1",
}
PROFILE_IDENTITY_FIELDS = (
    "slug", "label", "family", "aliases", "growth_variants", "role_scope", "provisional",
    "registration_status", "human_review_status", "publication_status", "official_anchor", "alias_decisions",
)


class RefreshError(RuntimeError):
    """Refresh must stop without moving the current release pointer."""


def _product_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _outside_product(path: Path | str, field: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(_product_root())
    except ValueError:
        return resolved
    raise RefreshError(f"{field} must be outside the product worktree")


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None:
        raise RefreshError("refresh clock must be timezone-aware")
    return result.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical_json(payload) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RefreshError(f"refresh already in progress (lock: {path})") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def load_refresh_policy(path: Path | str, *, product_root: Path | str | None = None) -> dict[str, int]:
    """Load the external cadence and same-for-every-role retrieval bounds."""

    policy_path = Path(path).expanduser().resolve()
    root = Path(product_root).resolve() if product_root else _product_root()
    try:
        policy_path.relative_to(root)
    except ValueError:
        pass
    else:
        raise RefreshError("refresh policy must be outside the product worktree")
    try:
        raw = policy_path.read_bytes()
    except OSError as exc:
        raise RefreshError(f"refresh policy unavailable: {policy_path}") from exc
    if len(raw) > 16 * 1024:
        raise RefreshError("refresh policy exceeds 16384 bytes")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError("refresh policy is not valid UTF-8 JSON") from exc
    expected = {"schema_version", "cadence_hours", "min_postings", "max_postings", "max_boards"}
    if not isinstance(document, dict) or set(document) != expected:
        raise RefreshError("refresh policy fields must be exactly schema_version, cadence_hours, min_postings, max_postings, max_boards")
    if document["schema_version"] != REFRESH_SCHEMA:
        raise RefreshError(f"unsupported refresh policy schema {document['schema_version']!r}")
    result: dict[str, int] = {}
    for name, upper in (
        ("cadence_hours", 24 * 365),
        ("min_postings", int(RESEARCH_LIMITS["max_postings_per_role"])),
        ("max_postings", int(RESEARCH_LIMITS["max_postings_per_role"])),
        ("max_boards", int(RESEARCH_LIMITS["max_boards_per_role"])),
    ):
        value = document[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > upper:
            raise RefreshError(f"{name} must be an integer between 1 and {upper}")
        result[name] = value
    if result["min_postings"] > result["max_postings"]:
        raise RefreshError("min_postings cannot exceed max_postings")
    return result


def _retention_number(value: Any, label: str, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise RefreshError(f"stage-retention metric {label} must be a finite number")
    if value < 0 or integer and not isinstance(value, int):
        kind = "non-negative integer" if integer else "non-negative number"
        raise RefreshError(f"stage-retention metric {label} must be a {kind}")
    return float(value)


def _load_retained_stage(
    path: Path | str,
    *,
    mission_budget: MissionBudget,
    runtime: ResearchRuntimeConfig,
    checkpoint_state: dict[str, Any] | None,
) -> dict[str, Any]:
    record_path = _outside_product(path, "stage-retention record")
    try:
        raw = record_path.read_bytes()
    except OSError as exc:
        raise RefreshError(f"stage-retention record unavailable: {record_path}") from exc
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise RefreshError("stage-retention record exceeds its size limit")
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError("stage-retention record is not valid UTF-8 JSON") from exc
    required = {"schema_version", "selected_stage", "role_receipts", "metrics_by_role", "reviewer", "decision_reason"}
    if not isinstance(record, dict) or not required.issubset(record):
        raise RefreshError("stage-retention record is missing required four-role decision fields")
    if record.get("schema_version") != RETENTION_SCHEMA:
        raise RefreshError("unsupported four-role stage-retention record")
    selected_stage = record.get("selected_stage")
    if selected_stage not in ("primary-only", "primary-plus-challenge"):
        raise RefreshError("stage-retention record must select a frozen comparison arm")
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, dict) or any(
        not isinstance(reviewer.get(name), str) or not reviewer[name].strip()
        for name in ("reviewer_id", "provider", "model", "reasoning")
    ) or (
        reviewer.get("fresh_context") is not True
        or reviewer.get("independent_of_implementation") is not True
        or reviewer.get("product_edits_authorized") is not False
    ):
        raise RefreshError("stage-retention reviewer must attest fresh independent review without product-edit authority")
    if not isinstance(record.get("decision_reason"), str) or not record["decision_reason"].strip():
        raise RefreshError("stage-retention decision reason is required")
    record_sha256 = sha256_bytes(raw)
    pinned = checkpoint_state.get("retained_stage") if checkpoint_state else None
    if checkpoint_state is not None and (
        not isinstance(pinned, dict)
        or pinned.get("record_path") != str(record_path)
        or pinned.get("record_sha256") != record_sha256
        or pinned.get("selected_stage") != selected_stage
    ):
        raise RefreshError("stage-retention decision changed after it was pinned in the refresh checkpoint")

    role_receipts = record["role_receipts"]
    metrics_by_role = record["metrics_by_role"]
    if (
        not isinstance(role_receipts, dict) or set(role_receipts) != set(FIXED_ROLES)
        or not isinstance(metrics_by_role, dict) or set(metrics_by_role) != set(FIXED_ROLES)
    ):
        raise RefreshError("stage-retention record must cover exactly the four fixed roles")
    metric_names = {
        "supported_claim_count", "citation_failure_count", "miss_count", "unsupported_refinement_count",
        "passage_coverage", "roles_present", "wall_clock_seconds", "actual_cost_usd",
    }
    totals = {
        "primary_failure_count": 0,
        "challenge_failure_count": 0,
        "primary_unsupported_refinement_count": 0,
        "challenge_unsupported_refinement_count": 0,
        "incremental_actual_cost_usd": 0.0,
        "comparison_actual_cost_usd": 0.0,
    }
    for slug in FIXED_ROLES:
        references = role_receipts[slug]
        if (
            not isinstance(references, dict)
            or not {
                "comparison_path", "comparison_sha256", "adjudication_path", "adjudication_sha256",
            }.issubset(references)
            or any(
                not isinstance(references.get(key), str) or not references[key].strip()
                for key in ("comparison_path", "adjudication_path")
            )
            or any(
                not isinstance(references.get(key), str)
                or len(references[key]) != 64
                or any(char not in "0123456789abcdef" for char in references[key])
                for key in ("comparison_sha256", "adjudication_sha256")
            )
        ):
            raise RefreshError(f"{slug} comparison/adjudication receipt references are malformed")
        comparison_path = _outside_product(references["comparison_path"], f"{slug} comparison receipt")
        adjudication_path = _outside_product(references["adjudication_path"], f"{slug} adjudication receipt")
        try:
            comparison_bytes = comparison_path.read_bytes()
            adjudication_bytes = adjudication_path.read_bytes()
            if max(len(comparison_bytes), len(adjudication_bytes)) > MAX_CHECKPOINT_BYTES:
                raise RefreshError(f"{slug} comparison/adjudication receipt exceeds the evidence size limit")
            comparison = json.loads(comparison_bytes)
            adjudication = json.loads(adjudication_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RefreshError(f"{slug} comparison/adjudication receipt is unreadable") from exc
        if (
            sha256_bytes(comparison_bytes) != references.get("comparison_sha256")
            or sha256_bytes(adjudication_bytes) != references.get("adjudication_sha256")
            or not isinstance(comparison, dict)
            or comparison.get("schema_version") != "skills-vector-matched-comparison/1"
            or comparison.get("occupation") != slug
            or not isinstance(adjudication, dict)
            or adjudication.get("comparison_id") != comparison.get("comparison_id")
            or adjudication.get("comparison_sha256") != references.get("comparison_sha256")
        ):
            raise RefreshError(f"{slug} comparison/adjudication hashes do not bind the selected stage")
        if checkpoint_state is None:
            try:
                from .comparison import validate_adjudication

                validate_adjudication(comparison_path, adjudication_path)
            except Exception as exc:
                raise RefreshError(f"{slug} per-role evidence lacks fresh independent adjudication") from exc
        try:
            from .comparison import _verify_arm

            arms: dict[str, dict[str, Any]] = {}
            for arm_name in ("primary-only", "primary-plus-challenge"):
                _, arm = _verify_arm(comparison, arm_name)
                if arm.get("eligible_for_review") is not True or arm.get("policy_gate_reasons"):
                    raise RefreshError(f"{slug} original {arm_name} comparison arm was not eligible")
                arms[arm_name] = arm
        except Exception as exc:
            raise RefreshError(f"{slug} frozen comparison artifacts failed verification") from exc
        source_passages = comparison.get("source_passages")
        source_passages_sha256 = (
            sha256_text(canonical_json(source_passages))
            if isinstance(source_passages, list) else None
        )
        if (
            not isinstance(source_passages, list)
            or comparison.get("comparison_id") != adjudication.get("comparison_id")
            or comparison.get("source_passages_sha256") != source_passages_sha256
            or any(
                isinstance(arms[name].get("source_passage_count"), bool)
                or not isinstance(arms[name].get("source_passage_count"), int)
                or arms[name].get("source_passage_count") != len(source_passages)
                for name in ("primary-only", "primary-plus-challenge")
            )
            or arms["primary-only"].get("source_passages_sha256") != arms["primary-plus-challenge"].get("source_passages_sha256")
        ):
            raise RefreshError(f"{slug} comparison arms do not share the exact matched passage set")

        role_metrics = metrics_by_role[slug]
        if not isinstance(role_metrics, dict) or set(role_metrics) != {"primary_only", "primary_plus_challenge"}:
            raise RefreshError(f"{slug} metrics must report both frozen comparison arms")
        normalized: dict[str, dict[str, float]] = {}
        for metric_key, arm_name in (
            ("primary_only", "primary-only"),
            ("primary_plus_challenge", "primary-plus-challenge"),
        ):
            metrics = role_metrics[metric_key]
            if not isinstance(metrics, dict) or set(metrics) != metric_names:
                raise RefreshError(f"{slug} {metric_key} metrics do not include all eight frozen measures")
            values: dict[str, float] = {}
            for name in metric_names - {"passage_coverage", "roles_present"}:
                values[name] = _retention_number(
                    metrics[name],
                    f"{slug}.{metric_key}.{name}",
                    integer=name in {
                        "supported_claim_count", "citation_failure_count", "miss_count",
                        "unsupported_refinement_count",
                    },
                )
            coverage = metrics["passage_coverage"]
            if not isinstance(coverage, dict) or set(coverage) != {"cited_passages", "total_passages"}:
                raise RefreshError(f"{slug} {metric_key} passage coverage must report cited and total passages")
            cited = _retention_number(coverage["cited_passages"], f"{slug}.{metric_key}.cited_passages", integer=True)
            total = _retention_number(coverage["total_passages"], f"{slug}.{metric_key}.total_passages", integer=True)
            if cited > total or total != int(arms[arm_name].get("source_passage_count", -1)):
                raise RefreshError(f"{slug} {metric_key} passage coverage does not match the frozen input passages")
            role_presence = metrics["roles_present"]
            if (
                not isinstance(role_presence, dict)
                or set(role_presence) != {"occupations", "denominator"}
                or role_presence.get("occupations") != [slug]
                or role_presence.get("denominator") != len(FIXED_ROLES)
            ):
                raise RefreshError(f"{slug} {metric_key} role-presence metric is malformed")
            values["passage_coverage"] = float(cited) / total if total else 0.0
            values["roles_present"] = 1.0
            arm = arms[arm_name]
            if (
                values["supported_claim_count"] + values["citation_failure_count"] != int(arm.get("claims", -1))
                or not math.isclose(values["wall_clock_seconds"], float(arm.get("wall_elapsed_ms", -1)) / 1000, rel_tol=0, abs_tol=0.001)
                or not math.isclose(values["actual_cost_usd"], float(arm.get("known_cost_usd", -1)), rel_tol=0, abs_tol=1e-9)
            ):
                raise RefreshError(f"{slug} metrics disagree with the frozen comparison arm receipt")
            normalized[arm_name] = values
            failures = int(values["citation_failure_count"] + values["miss_count"] + values["unsupported_refinement_count"])
            failure_key = "primary_failure_count" if arm_name == "primary-only" else "challenge_failure_count"
            unsupported_key = (
                "primary_unsupported_refinement_count"
                if arm_name == "primary-only" else "challenge_unsupported_refinement_count"
            )
            totals[failure_key] += failures
            totals[unsupported_key] += int(values["unsupported_refinement_count"])
        totals["comparison_actual_cost_usd"] += normalized["primary-plus-challenge"]["actual_cost_usd"]
        totals["incremental_actual_cost_usd"] += (
            normalized["primary-plus-challenge"]["actual_cost_usd"]
            - normalized["primary-only"]["actual_cost_usd"]
        )
        attempts = mission_budget.attempts_for_run(str(comparison.get("comparison_id") or ""))
        if not attempts or any(
            row.get("status") not in ("settled", "known_failure")
            or not isinstance(row.get("actual_usd"), (int, float))
            or isinstance(row.get("actual_usd"), bool)
            for row in attempts
        ):
            raise RefreshError(f"{slug} initial comparison has unresolved or unknown provider cost")
        billed = sum(float(row["actual_usd"]) for row in attempts)
        if not math.isclose(billed, normalized["primary-plus-challenge"]["actual_cost_usd"], rel_tol=0, abs_tol=1e-9):
            raise RefreshError(f"{slug} comparison total cost does not reconcile with the mission ledger")

    if checkpoint_state is None:
        summary = mission_budget.summary()
        active = [row for row in summary.get("attempts", []) if row.get("status") in ("reserved", "unknown", "overrun")]
        if active:
            raise RefreshError("stage-retention cannot be authorized while mission costs remain unresolved")
        remaining_before_comparison = runtime.mission_allocation_usd - (
            float(summary["spent_or_held_usd"]) - totals["comparison_actual_cost_usd"]
        )
    else:
        pinned_aggregate = pinned.get("aggregate") if isinstance(pinned, dict) else None
        if not isinstance(pinned_aggregate, dict):
            raise RefreshError("pinned stage-retention aggregate is malformed")
        for name in (
            "primary_failure_count", "challenge_failure_count",
            "primary_unsupported_refinement_count", "challenge_unsupported_refinement_count",
        ):
            if pinned_aggregate.get(name) != totals[name]:
                raise RefreshError(f"pinned stage-retention aggregate {name} no longer matches its metrics")
        if not math.isclose(
            float(pinned_aggregate.get("incremental_actual_cost_usd", -1)),
            totals["incremental_actual_cost_usd"],
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise RefreshError("pinned stage-retention cost aggregate no longer matches its metrics")
        remaining_before_comparison = _retention_number(
            pinned_aggregate.get("remaining_before_comparison_usd"),
            "remaining_before_comparison_usd",
        )
    challenge_kept = (
        totals["primary_failure_count"] - totals["challenge_failure_count"] >= 1
        and totals["challenge_unsupported_refinement_count"] <= totals["primary_unsupported_refinement_count"]
        and totals["incremental_actual_cost_usd"] <= remaining_before_comparison + 1e-9
    )
    expected_stage = "primary-plus-challenge" if challenge_kept else "primary-only"
    if selected_stage != expected_stage:
        raise RefreshError("selected stage does not match the frozen four-role aggregate retention rule")
    if checkpoint_state is not None and pinned_aggregate.get("challenge_retained_by_rule") is not challenge_kept:
        raise RefreshError("pinned stage-retention rule outcome does not match the four-role aggregate")
    return {
        "record_path": str(record_path),
        "record_sha256": record_sha256,
        "selected_stage": selected_stage,
        "role_receipts": role_receipts,
        "reviewer": reviewer,
        "decision_reason": record["decision_reason"],
        "aggregate": {
            "primary_failure_count": totals["primary_failure_count"],
            "challenge_failure_count": totals["challenge_failure_count"],
            "primary_unsupported_refinement_count": totals["primary_unsupported_refinement_count"],
            "challenge_unsupported_refinement_count": totals["challenge_unsupported_refinement_count"],
            "incremental_actual_cost_usd": totals["incremental_actual_cost_usd"],
            "remaining_before_comparison_usd": remaining_before_comparison,
            "challenge_retained_by_rule": challenge_kept,
        },
    }


def _definition_versions(release: ReleaseData) -> dict[str, str]:
    observed: dict[str, set[str]] = {key: set() for key in DEFINITION_VERSIONS}
    observed["release_schema"].add(str(release.manifest.get("release_schema") or ""))
    for posting in release.postings:
        expectations = posting.get("expectations") or []
        if not isinstance(expectations, list):
            continue
        for expected in expectations:
            if not isinstance(expected, dict):
                continue
            for source_field, target in (
                ("mapping_method", "expectation_mapping"),
                ("identity_method", "expectation_identity"),
                ("relationship_method", "expectation_relationship"),
            ):
                if expected.get(source_field):
                    observed[target].add(str(expected[source_field]))
    versions = {"expectation_id": DEFINITION_VERSIONS["expectation_id"]}
    for key, expected_value in DEFINITION_VERSIONS.items():
        if key == "expectation_id":
            continue
        values = observed[key]
        if values != {expected_value}:
            raise RefreshError(
                f"release definition {key!r} is not the supported frozen version "
                f"{expected_value!r}: {sorted(values)}"
            )
        versions[key] = expected_value
    return versions


def _profile_identity(release: ReleaseData) -> dict[str, str]:
    rows = {str(row.get("slug") or ""): row for row in release.occupations}
    if set(rows) != set(FIXED_ROLES):
        raise RefreshError(f"routine auto-refresh requires exactly the fixed existing roles {list(FIXED_ROLES)}")
    return {
        slug: sha256_text(canonical_json({field: rows[slug].get(field) for field in PROFILE_IDENTITY_FIELDS}))
        for slug in FIXED_ROLES
    }


def _policy_fingerprint(
    policy: dict[str, Any],
    retained_stage: dict[str, Any],
    runtime: ResearchRuntimeConfig,
) -> str:
    frozen = {
        "policy_version": REFRESH_POLICY_VERSION,
        "retention_rule": RETENTION_RULE,
        "definition_versions": DEFINITION_VERSIONS,
        "selected_stage": retained_stage["selected_stage"],
        "retention_record_sha256": retained_stage["record_sha256"],
        "runtime": {
            "provider": runtime.provider,
            "endpoint": runtime.endpoint,
            "models": runtime.models,
            "pricing_receipt_sha256": runtime.pricing_receipt_sha256,
            "ledger_path": str(runtime.ledger_path),
            "month": runtime.month,
            "mission_id": runtime.mission_id,
            "mission_reservation_id": runtime.mission_reservation_id,
            "mission_allocation_usd": runtime.mission_allocation_usd,
            "monthly_cap_usd": runtime.monthly_cap_usd,
            "limits": runtime.limits,
        },
        "auto_publish_gates": [
            "existing-four-role-profile-identity-unchanged",
            "source-literal-definition-versions-unchanged",
            "all-provider-attempt-costs-settled-and-known",
            "schema-rights-quote-citation-validator-passes",
            "no-major-unresolved-contradiction",
            "all-four-candidates-eligible-for-the-retained-stage",
        ],
        "min_postings": policy["min_postings"],
        "max_postings": policy["max_postings"],
        "max_boards": policy["max_boards"],
    }
    return sha256_text(canonical_json(frozen))


def _read_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RefreshError(f"refresh checkpoint unavailable: {path}") from exc
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise RefreshError("refresh checkpoint exceeds its size limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError("refresh checkpoint is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != REFRESH_SCHEMA:
        raise RefreshError("refresh checkpoint schema is invalid")
    sampling = value.get("sampling_policy")
    retained = value.get("retained_stage")
    last_good = value.get("last_good")
    digest = value.get("policy_sha256")
    if (
        value.get("policy_version") != REFRESH_POLICY_VERSION
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or not isinstance(sampling, dict)
        or set(sampling) != {"min_postings", "max_postings", "max_boards"}
        or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in sampling.values())
        or not isinstance(value.get("cadence_hours"), int)
        or isinstance(value.get("cadence_hours"), bool)
        or not 1 <= value["cadence_hours"] <= 24 * 365
        or _parse_stamp(value.get("next_due_at")) is None
        or value.get("pending_receipt") is not None and not isinstance(value.get("pending_receipt"), dict)
        or value.get("latest_attempt") is not None and not isinstance(value.get("latest_attempt"), dict)
        or not isinstance(retained, dict)
        or not isinstance(last_good, dict)
    ):
        raise RefreshError("refresh checkpoint fields are malformed")
    pending = value.get("pending_receipt")
    if pending is not None and (
        not isinstance(pending.get("attempt_id"), str)
        or not pending["attempt_id"]
        or not isinstance(pending.get("receipt_path"), str)
        or not pending["receipt_path"]
        or pending.get("status") not in ("running", "rollback_failed")
    ):
        raise RefreshError("refresh checkpoint pending-receipt marker is malformed")
    if (
        not isinstance(retained.get("record_path"), str)
        or not retained["record_path"]
        or not isinstance(retained.get("record_sha256"), str)
        or len(retained["record_sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in retained["record_sha256"])
        or retained.get("selected_stage") not in ("primary-only", "primary-plus-challenge")
        or not isinstance(retained.get("role_receipts"), dict)
        or set(retained["role_receipts"]) != set(FIXED_ROLES)
        or not isinstance(retained.get("aggregate"), dict)
        or set(retained["aggregate"]) != {
            "primary_failure_count", "challenge_failure_count",
            "primary_unsupported_refinement_count", "challenge_unsupported_refinement_count",
            "incremental_actual_cost_usd", "remaining_before_comparison_usd", "challenge_retained_by_rule",
        }
        or any(
            isinstance(retained["aggregate"][key], bool)
            or not isinstance(retained["aggregate"][key], int)
            or retained["aggregate"][key] < 0
            for key in (
                "primary_failure_count", "challenge_failure_count",
                "primary_unsupported_refinement_count", "challenge_unsupported_refinement_count",
            )
        )
        or any(
            isinstance(retained["aggregate"][key], bool)
            or not isinstance(retained["aggregate"][key], (int, float))
            or not math.isfinite(float(retained["aggregate"][key]))
            for key in ("incremental_actual_cost_usd", "remaining_before_comparison_usd")
        )
        or not isinstance(retained["aggregate"]["challenge_retained_by_rule"], bool)
        or not isinstance(retained.get("reviewer"), dict)
        or not isinstance(retained.get("decision_reason"), str)
        or not retained["decision_reason"].strip()
    ):
        raise RefreshError("refresh checkpoint retained-stage record is malformed")
    if (
        not isinstance(last_good.get("release_id"), str)
        or not last_good["release_id"]
        or not isinstance(last_good.get("content_sha256"), str)
        or len(last_good["content_sha256"]) != 16
        or any(char not in "0123456789abcdef" for char in last_good["content_sha256"])
        or not isinstance(last_good.get("profile_identity"), dict)
        or set(last_good["profile_identity"]) != set(FIXED_ROLES)
        or not all(
            isinstance(v, str)
            and len(v) == 64
            and all(char in "0123456789abcdef" for char in v)
            for v in last_good["profile_identity"].values()
        )
        or last_good.get("definition_versions") != DEFINITION_VERSIONS
        or _parse_stamp(last_good.get("recorded_at")) is None
    ):
        raise RefreshError("refresh checkpoint last-good record is malformed")
    return value


class SourceEvidenceCache:
    """Reuse recent official foundation bytes; always re-fetch job-board listings."""

    def __init__(self, cache_root: Path, *, max_age: timedelta = DEFAULT_SOURCE_CACHE_AGE) -> None:
        self.root = cache_root
        self.max_age = max_age
        self.reused: dict[str, dict[str, str]] = {}
        if self.root.is_symlink():
            raise RefreshError("foundation response cache root cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, url: str) -> Path:
        return self.root / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")

    def _cached(self, url: str, now: datetime) -> tuple[dict[str, Any], bytes] | None:
        path = self._entry_path(url)
        if path.is_symlink() or not path.is_file():
            return None
        try:
            if path.stat().st_size > int(RESEARCH_LIMITS["max_response_bytes"]) * 2 + 16_384:
                return None
            entry = json.loads(path.read_bytes())
            if not isinstance(entry, dict) or entry.get("schema_version") != "foundation-response-cache/1":
                return None
            body = base64.b64decode(entry.get("body_b64", ""), validate=True)
            stored_at = _parse_stamp(entry.get("retrieved_at"))
            if (
                entry.get("url") != url or entry.get("status") != 200
                or len(body) > int(RESEARCH_LIMITS["max_response_bytes"])
                or sha256_bytes(body) != entry.get("body_sha256")
                or stored_at is None or now - stored_at > self.max_age or stored_at > now + timedelta(minutes=5)
            ):
                return None
            return entry, body
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def __call__(self, url: str) -> tuple[int, dict[str, str], bytes]:
        now = _utc()
        host = (urlsplit(url).hostname or "").lower()
        if url_problem(url) is None and host_kind(host) == "foundation":
            cached = self._cached(url, now)
            if cached is not None:
                entry, body = cached
                self.reused[url] = {
                    "retrieved_at": str(entry["retrieved_at"]),
                    "sha256": str(entry["body_sha256"]),
                }
                return 200, {"content-type": str(entry.get("content_type") or "application/octet-stream")}, body
        status, headers, body = urllib_transport(url)
        if status == 200 and len(body) <= int(RESEARCH_LIMITS["max_response_bytes"]) and host_kind(host) == "foundation":
            self._save(url, status, headers, body, _stamp(now))
        return status, headers, body

    def _save(self, url: str, status: int, headers: dict[str, str], body: bytes, retrieved_at: str) -> None:
        payload = {
            "schema_version": "foundation-response-cache/1",
            "url": url,
            "status": status,
            "retrieved_at": retrieved_at,
            "content_type": str(headers.get("content-type") or "application/octet-stream"),
            "body_sha256": sha256_bytes(body),
            "body_b64": base64.b64encode(body).decode("ascii"),
        }
        _atomic_json(self._entry_path(url), payload)
        entries: list[tuple[datetime, Path]] = []
        for path in self.root.glob("*.json"):
            if path.is_symlink():
                continue
            try:
                row = json.loads(path.read_bytes())
                timestamp = _parse_stamp(row.get("retrieved_at")) if isinstance(row, dict) else None
                if timestamp is None or _utc() - timestamp > self.max_age:
                    path.unlink(missing_ok=True)
                else:
                    entries.append((timestamp, path))
            except (OSError, json.JSONDecodeError, TypeError):
                path.unlink(missing_ok=True)
        entries.sort(key=lambda row: (row[0], row[1].name))
        for _, path in entries[: max(0, len(entries) - MAX_SOURCE_CACHE_ENTRIES)]:
            path.unlink(missing_ok=True)


def _stamp_cached_source_dates(candidate_slice: Path, reused: dict[str, dict[str, str]]) -> None:
    if not reused:
        return
    path = candidate_slice / "sources.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"candidate source rows are unreadable: {path}") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RefreshError(f"candidate source rows are malformed: {path}")
    changed = False
    for row in rows:
        cache = reused.get(str(row.get("url") or ""))
        if cache and row.get("sha256") == cache["sha256"]:
            row["retrieved_at"] = cache["retrieved_at"]
            changed = True
    if changed:
        _atomic_json(path, rows)


def _assert_no_unresolved_mission_attempts(budget: MissionBudget) -> None:
    active = [
        row for row in budget.summary().get("attempts", [])
        if row.get("status") in ("reserved", "unknown", "overrun") and int(row.get("n") or 0) > 0
    ]
    if active:
        raise RefreshError("routine auto-publication is blocked by unresolved mission attempts")


def _assert_attempt_costs_known(
    budget: MissionBudget,
    run_ids: list[str],
    results: list[dict[str, Any]] | None = None,
) -> None:
    _assert_no_unresolved_mission_attempts(budget)
    results_by_id = {
        str(result.get("run_id") or ""): result
        for result in results or []
    }
    for run_id in run_ids:
        attempts = budget.attempts_for_run(run_id)
        if not attempts:
            raise RefreshError(f"routine auto-publication has no billed-attempt receipt for {run_id}")
        for attempt in attempts:
            actual = attempt.get("actual_usd")
            if (
                attempt.get("status") not in ("settled", "known_failure")
                or not isinstance(actual, (int, float))
                or isinstance(actual, bool)
                or not math.isfinite(float(actual))
            ):
                raise RefreshError(f"routine auto-publication requires settled known cost for attempt {attempt.get('attempt_id')}")
        result = results_by_id.get(run_id)
        if result is None:
            continue
        receipt_path = _outside_product(str(result.get("receipt") or ""), f"{run_id} run receipt")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RefreshError(f"routine auto-publication run-cost receipt is unreadable for {run_id}") from exc
        if not isinstance(receipt, dict) or receipt.get("run_id") != run_id:
            raise RefreshError(f"routine auto-publication run-cost receipt identity is invalid for {run_id}")
        reported = _retention_number(receipt.get("cost_usd_total"), f"{run_id}.cost_usd_total")
        billed = sum(float(row["actual_usd"]) for row in attempts)
        if not math.isclose(reported, billed, rel_tol=0, abs_tol=1e-9):
            raise RefreshError(f"routine auto-publication run cost does not reconcile with the mission ledger for {run_id}")
        if result.get("status") == "comparison_ready" and result.get("challenge_enabled") is True:
            comparison_path = _outside_product(
                str(result.get("comparison_receipt") or ""),
                f"{run_id} comparison receipt",
            )
            try:
                comparison_bytes = comparison_path.read_bytes()
                comparison = json.loads(comparison_bytes)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RefreshError(f"routine auto-publication comparison cost receipt is unreadable for {run_id}") from exc
            arms = comparison.get("arms") if isinstance(comparison, dict) else None
            retained_arm = arms.get("primary-plus-challenge") if isinstance(arms, dict) else None
            if (
                not isinstance(comparison, dict)
                or hashlib.sha256(comparison_bytes).hexdigest() != result.get("comparison_sha256")
                or comparison.get("comparison_id") != run_id
                or comparison.get("occupation") != result.get("occupation")
                or not isinstance(retained_arm, dict)
                or not math.isclose(
                    _retention_number(retained_arm.get("known_cost_usd"), f"{run_id}.challenge_cost_usd"),
                    billed,
                    rel_tol=0,
                    abs_tol=1e-9,
                )
            ):
                raise RefreshError(f"routine auto-publication challenge cost does not reconcile with the mission ledger for {run_id}")

def _major_disagreements(slices: list[Path]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for slice_dir in slices:
        path = slice_dir / "disagreements.json"
        if not path.is_file():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RefreshError(f"candidate disagreement data is unreadable: {path}") from exc
        if not isinstance(rows, list):
            raise RefreshError(f"candidate disagreement data must be a list: {path}")
        blockers.extend(
            row for row in rows
            if isinstance(row, dict) and str(row.get("severity") or "").casefold() in {"blocker", "critical", "error", "major"}
        )
    return blockers

def _verified_run_arm(result: dict[str, Any], slug: str, arm_name: str) -> Path:
    run_dir = Path(str(result.get("run_dir") or "")).expanduser().resolve()
    receipt_path = Path(str(result.get("receipt") or "")).expanduser().resolve()
    run_dir = _outside_product(run_dir, f"{slug} run evidence directory")
    receipt_path = _outside_product(receipt_path, f"{slug} run receipt")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"{slug} run receipt is unreadable") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("receipt_schema") != "market-run-receipt/2"
        or receipt.get("run_id") != result.get("run_id")
        or receipt.get("occupation") != slug
        or receipt.get("status") != result.get("status")
    ):
        raise RefreshError(f"{slug} run receipt identity or status does not match its result")
    arm_location = run_dir / "comparison" / "arms" / arm_name
    cursor = arm_location
    while cursor != run_dir.parent:
        if cursor.is_symlink():
            raise RefreshError(f"{slug} candidate arm contains a symlink")
        cursor = cursor.parent
    arm_dir = arm_location.resolve()
    expected_tail = ("runs", str(result.get("run_id") or ""), "comparison", "arms", arm_name)
    if tuple(arm_dir.parts[-len(expected_tail):]) != expected_tail or not arm_dir.is_dir():
        raise RefreshError(f"{slug} candidate arm is outside its immutable run receipt directory")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RefreshError(f"{slug} run receipt has no artifact manifest")
    prefix = f"comparison/arms/{arm_name}/"
    recorded: dict[str, str] = {}
    for name, entry in artifacts.items():
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        relative = name[len(prefix):]
        artifact_relative = Path(name)
        if (
            not relative
            or artifact_relative.is_absolute()
            or ".." in artifact_relative.parts
            or not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
            or len(entry["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in entry["sha256"])
        ):
            raise RefreshError(f"{slug} run receipt artifact entry is malformed")
        artifact_path = (run_dir / artifact_relative).resolve()
        try:
            artifact_path.relative_to(run_dir)
        except ValueError as exc:
            raise RefreshError(f"{slug} run receipt artifact escapes its immutable run directory") from exc
        if Path(entry["path"]).expanduser().resolve() != artifact_path:
            raise RefreshError(f"{slug} run receipt artifact path does not match its immutable location")
        recorded[relative] = entry["sha256"]
    if not arm_dir.is_dir():
        raise RefreshError(f"{slug} candidate arm does not exist")
    found: dict[str, str] = {}
    for path in sorted(arm_dir.rglob("*")):
        if path.is_symlink():
            raise RefreshError(f"{slug} candidate arm contains a symlink")
        if path.is_file():
            found[path.relative_to(arm_dir).as_posix()] = sha256_file(path)
    if not found or found != recorded:
        raise RefreshError(f"{slug} candidate arm bytes do not match its run receipt")
    try:
        occupations = json.loads((arm_dir / "occupations.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"{slug} candidate occupation slice is unreadable") from exc
    if not isinstance(occupations, list) or [str(row.get("slug") or "") for row in occupations if isinstance(row, dict)] != [slug]:
        raise RefreshError(f"{slug} candidate arm does not contain exactly its requested occupation")
    return arm_dir


def _selected_candidate(result: dict[str, Any], slug: str, selected_stage: str) -> Path:
    if selected_stage == "primary-only":
        if (
            result.get("status") != "candidate_ready"
            or result.get("challenge_enabled") is not False
            or result.get("eligible") is not True
            or result.get("eligible_arms") != ["primary-only"]
        ):
            raise RefreshError(f"{slug} did not produce an eligible primary-only candidate")
        path = _verified_run_arm(result, slug, "primary-only")
        if Path(str(result.get("candidate_path") or "")).expanduser().resolve() != path:
            raise RefreshError(f"{slug} primary-only candidate path does not match its frozen run arm")
        return path
    if selected_stage != "primary-plus-challenge":
        raise RefreshError(f"unsupported frozen selected stage {selected_stage!r}")
    if (
        result.get("status") != "comparison_ready"
        or result.get("challenge_enabled") is not True
        or result.get("eligible") is not True
        or selected_stage not in (result.get("eligible_arms") or [])
    ):
        raise RefreshError(f"{slug} did not produce an eligible retained challenge arm")
    comparison_path = _outside_product(
        str(result.get("comparison_receipt") or ""),
        f"{slug} comparison receipt",
    )
    try:
        comparison_bytes = comparison_path.read_bytes()
        if len(comparison_bytes) > MAX_CHECKPOINT_BYTES:
            raise RefreshError(f"{slug} comparison receipt exceeds the evidence size limit")
        comparison = json.loads(comparison_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"{slug} comparison receipt is unreadable") from exc
    source_passages = comparison.get("source_passages") if isinstance(comparison, dict) else None
    source_passages_sha256 = (
        sha256_text(canonical_json(source_passages))
        if isinstance(source_passages, list) else None
    )
    if (
        not isinstance(comparison, dict)
        or sha256_bytes(comparison_bytes) != result.get("comparison_sha256")
        or comparison.get("comparison_id") != result.get("run_id")
        or comparison.get("occupation") != slug
        or not isinstance(source_passages, list)
        or comparison.get("source_passages_sha256") != source_passages_sha256
    ):
        raise RefreshError(f"{slug} comparison receipt identity, passage hash, or size is invalid")
    arm = (comparison.get("arms") or {}).get(selected_stage)
    if not isinstance(arm, dict) or arm.get("eligible_for_review") is not True or arm.get("policy_gate_reasons"):
        raise RefreshError(f"{slug} retained challenge arm fails its current candidate gates")
    primary_arm = (comparison.get("arms") or {}).get("primary-only")
    if (
        not isinstance(primary_arm, dict)
        or primary_arm.get("source_passages_sha256") != source_passages_sha256
        or arm.get("source_passages_sha256") != source_passages_sha256
        or any(
            isinstance(candidate_arm.get("source_passage_count"), bool)
            or not isinstance(candidate_arm.get("source_passage_count"), int)
            or candidate_arm.get("source_passage_count") != len(source_passages)
            for candidate_arm in (primary_arm, arm)
        )
    ):
        raise RefreshError(f"{slug} comparison arms do not match the exact frozen source passages")
    try:
        from .comparison import _verify_arm

        _verify_arm(comparison, "primary-only")
        path, arm = _verify_arm(comparison, selected_stage)
    except Exception as exc:
        raise RefreshError(f"{slug} retained challenge arm failed its frozen artifact verification") from exc
    receipt_path = Path(str(result.get("receipt") or "")).expanduser().resolve()
    _verified_run_arm(result, slug, selected_stage)
    if Path(str(arm.get("slice_path") or "")).expanduser().resolve() != path or not receipt_path.is_file():
        raise RefreshError(f"{slug} retained challenge path does not match its comparison receipt")
    return path

def _stage_all_slices(release_root: Path, slices: list[Path], stage_root: Path) -> Path:
    if stage_root.exists():
        raise RefreshError(f"refresh staging directory already exists: {stage_root}")
    shutil.copytree(release_root, stage_root)
    staged_release_id: str | None = None
    for slice_dir in slices:
        candidate = merge_slice(stage_root, slice_dir)
        result = publish_release(stage_root, candidate)
        staged_release_id = str(result["release_id"])
    if not staged_release_id:
        raise RefreshError("refresh has no role slices to stage")
    return stage_root / "releases" / staged_release_id


def run_refresh_once(
    *,
    runtime: ResearchRuntimeConfig,
    mission_budget: MissionBudget,
    evidence_root: Path | str,
    release_root: Path | str,
    checkpoint_path: Path | str,
    retention_record: Path | str,
    policy: dict[str, int],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one due four-role batch or serve the last-good release without calls."""

    evidence = _outside_product(evidence_root, "evidence_root")
    checkpoint = _outside_product(checkpoint_path, "refresh checkpoint")
    release_dir = Path(release_root).expanduser().resolve()
    expected_policy_keys = {"cadence_hours", "min_postings", "max_postings", "max_boards"}
    if not isinstance(policy, dict) or set(policy) != expected_policy_keys:
        raise RefreshError("refresh policy must be loaded with exact cadence and sampling bounds")
    bounds = {
        "cadence_hours": 24 * 365,
        "min_postings": int(RESEARCH_LIMITS["max_postings_per_role"]),
        "max_postings": int(RESEARCH_LIMITS["max_postings_per_role"]),
        "max_boards": int(RESEARCH_LIMITS["max_boards_per_role"]),
    }
    for key, upper in bounds.items():
        value = policy[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise RefreshError(f"refresh policy {key} must be an integer between 1 and {upper}")
    if policy["min_postings"] > policy["max_postings"]:
        raise RefreshError("refresh policy minimum exceeds its maximum")
    stamp = _utc(now)
    lock_path = checkpoint.with_suffix(checkpoint.suffix + ".lock")
    with _exclusive_lock(lock_path):
        catalog = CatalogStore(release_dir)
        current = catalog.release()
        if current is None:
            raise RefreshError("routine refresh requires an existing validated four-role release; initial registration stays on the reviewed path")
        current_profiles = _profile_identity(current)
        current_versions = _definition_versions(current)
        state = _read_checkpoint(checkpoint)
        retained = _load_retained_stage(
            retention_record,
            mission_budget=mission_budget,
            runtime=runtime,
            checkpoint_state=state,
        )
        policy_hash = _policy_fingerprint(policy, retained, runtime)
        if state is None:
            state = {
                "schema_version": REFRESH_SCHEMA,
                "policy_version": REFRESH_POLICY_VERSION,
                "policy_sha256": policy_hash,
                "sampling_policy": {key: policy[key] for key in ("min_postings", "max_postings", "max_boards")},
                "cadence_hours": policy["cadence_hours"],
                "retained_stage": retained,
                "last_good": {
                    "release_id": current.release_id,
                    "content_sha256": current.content_hash(),
                    "profile_identity": current_profiles,
                    "definition_versions": current_versions,
                    "recorded_at": _stamp(stamp),
                },
                "pending_receipt": None,
                "latest_attempt": None,
                "next_due_at": _stamp(stamp),
            }
            _atomic_json(checkpoint, state)
        if state.get("policy_version") != REFRESH_POLICY_VERSION or state.get("policy_sha256") != policy_hash:
            raise RefreshError("frozen auto-publication policy or sampling bounds changed; operator review is required")
        if state.get("sampling_policy") != {key: policy[key] for key in ("min_postings", "max_postings", "max_boards")}:
            raise RefreshError("refresh sampling caps changed; operator review is required")
        last_good = json.loads(json.dumps(state["last_good"]))

        pending = state.get("pending_receipt")
        if isinstance(pending, dict):
            pending_id = str(pending.get("attempt_id") or "")
            prior = state.get("latest_attempt") if isinstance(state.get("latest_attempt"), dict) else {}
            pointer_matches = (
                current.release_id == last_good.get("release_id")
                and current.content_hash() == last_good.get("content_sha256")
            )
            if not pointer_matches:
                publication_policy = current.manifest.get("publication_policy")
                expected_publish = (
                    current.release_id == prior.get("expected_release_id")
                    and current.content_hash() == prior.get("expected_release_content_sha256")
                )
                marked_publish = (
                    isinstance(publication_policy, dict)
                    and publication_policy.get("attempt_id") == pending_id
                )
                if not pending_id or not (expected_publish or marked_publish):
                    raise RefreshError("published pointer changed outside the interrupted refresh; reconcile operator changes first")
                rollback_current(release_dir, str(last_good["release_id"]))
                current = CatalogStore(release_dir).release()
                if current is None:
                    raise RefreshError("last-good release could not be restored after an interrupted refresh")
            prior.update({
                "status": "interrupted",
                "finished_at": _stamp(stamp),
                "error": "previous refresh exited before checkpoint completion; last-good pointer retained",
            })
            state["latest_attempt"] = prior
            state["pending_receipt"] = None
        current_profiles = _profile_identity(current)
        current_versions = _definition_versions(current)
        if current.release_id != last_good.get("release_id") or current.content_hash() != last_good.get("content_sha256"):
            raise RefreshError("published pointer no longer matches the refresh last-good checkpoint; reconcile operator changes first")
        if last_good.get("profile_identity") != current_profiles:
            raise RefreshError("current role identity differs from the frozen refresh baseline")
        if last_good.get("definition_versions") != current_versions:
            raise RefreshError("current material identity-definition versions differ from the frozen refresh baseline")
        if state["cadence_hours"] != policy["cadence_hours"]:
            latest = state.get("latest_attempt") if isinstance(state.get("latest_attempt"), dict) else {}
            cadence_anchor = _parse_stamp(latest.get("finished_at")) or _parse_stamp(last_good.get("recorded_at"))
            if cadence_anchor is None:
                raise RefreshError("refresh checkpoint lacks a timestamp for the configured cadence")
            state["cadence_hours"] = policy["cadence_hours"]
            state["next_due_at"] = _stamp(cadence_anchor + timedelta(hours=policy["cadence_hours"]))
            _atomic_json(checkpoint, state)
        if isinstance(pending, dict):
            _atomic_json(checkpoint, state)
            next_due = _parse_stamp(state.get("next_due_at"))
            if next_due is not None and stamp < next_due:
                return {
                    "status": "recovered_last_good",
                    "release_id": current.release_id,
                    "next_due_at": state.get("next_due_at"),
                    "receipt": prior.get("receipt_path"),
                    "provider_calls": 0,
                }
        due_at = _parse_stamp(state.get("next_due_at"))
        if due_at is not None and stamp < due_at:
            return {
                "status": "skipped_not_due",
                "release_id": current.release_id,
                "next_due_at": state.get("next_due_at"),
                "provider_calls": 0,
            }

        _assert_no_unresolved_mission_attempts(mission_budget)
        attempt_id = "refresh_" + uuid4().hex[:20]
        receipt_path = evidence / "refresh" / f"{attempt_id}.json"
        attempt: dict[str, Any] = {
            "attempt_id": attempt_id,
            "status": "running",
            "started_at": _stamp(stamp),
            "finished_at": None,
            "prior_release_id": current.release_id,
            "retained_stage": retained["selected_stage"],
            "stage_retention_record_sha256": retained["record_sha256"],
            "role_results": [],
            "source_cache_reuse": [],
            "policy_version": REFRESH_POLICY_VERSION,
            "policy_sha256": policy_hash,
            "receipt_path": str(receipt_path),
            "error": None,
        }
        state["pending_receipt"] = {"attempt_id": attempt_id, "receipt_path": str(receipt_path), "status": "running"}
        state["latest_attempt"] = attempt
        state["next_due_at"] = _stamp(stamp + timedelta(hours=policy["cadence_hours"]))
        _atomic_json(checkpoint, state)
        _atomic_json(receipt_path, attempt)

        cache = SourceEvidenceCache(evidence / "source-cache")
        results: list[dict[str, Any]] = []
        slices: list[Path] = []
        run_ids: list[str] = []
        scratch_root = release_dir.parent / ".market-refresh" / attempt_id / "slices"
        challenge_enabled = retained["selected_stage"] == "primary-plus-challenge"
        try:
            from .pipeline import ResearchConfig, run_research

            scratch_root.mkdir(parents=True, exist_ok=False)
            for slug in FIXED_ROLES:
                run_id = f"{attempt_id}_{slug}"
                run_ids.append(run_id)
                config = ResearchConfig(
                    occupation=slug,
                    evidence_root=evidence,
                    release_root=release_dir,
                    runtime=runtime,
                    mission_budget=mission_budget,
                    min_postings=policy["min_postings"],
                    max_postings=policy["max_postings"],
                    max_boards=policy["max_boards"],
                    transport=cache,
                    run_id=run_id,
                    challenge_enabled=challenge_enabled,
                )
                result = run_research(config)
                results.append(result)
                record = {
                    "occupation": slug,
                    "run_id": run_id,
                    "status": result.get("status"),
                    "challenge_enabled": result.get("challenge_enabled"),
                    "candidate_path": result.get("candidate_path"),
                    "comparison_receipt": result.get("comparison_receipt"),
                    "comparison_sha256": result.get("comparison_sha256"),
                    "error": result.get("error"),
                    "stats": result.get("stats"),
                }
                attempt["role_results"].append(record)
                _atomic_json(receipt_path, attempt)
                _atomic_json(checkpoint, state)
                _assert_attempt_costs_known(mission_budget, run_ids, results)
                successful_status = "comparison_ready" if challenge_enabled else "candidate_ready"
                if result.get("status") == successful_status:
                    candidate_arm = _selected_candidate(result, slug, retained["selected_stage"])
                    staged_slice = scratch_root / slug
                    shutil.copytree(candidate_arm, staged_slice)
                    _stamp_cached_source_dates(staged_slice, cache.reused)
                    record["staged_slice_path"] = str(staged_slice)
                    slices.append(staged_slice)
                else:
                    # All four fixed roles remain in the batch; eligibility never shrinks the scope.
                    continue

            if len(slices) != len(FIXED_ROLES):
                raise RefreshError("one or more fixed-role candidates for the retained stage were blocked; the current release remains last-good")
            _assert_attempt_costs_known(mission_budget, run_ids, results)
            attempt["source_cache_reuse"] = [
                {"url": url, "retrieved_at": row["retrieved_at"], "sha256": row["sha256"]}
                for url, row in sorted(cache.reused.items())
            ]
            current_challenge_findings = 0
            if challenge_enabled:
                for slug, result in zip(FIXED_ROLES, results, strict=True):
                    comparison_path = Path(str(result.get("comparison_receipt") or "")).expanduser().resolve()
                    try:
                        comparison_bytes = comparison_path.read_bytes()
                        comparison = json.loads(comparison_bytes)
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RefreshError(f"{slug} current challenge comparison is unreadable") from exc
                    if (
                        not isinstance(comparison, dict)
                        or sha256_bytes(comparison_bytes) != result.get("comparison_sha256")
                        or comparison.get("occupation") != slug
                    ):
                        raise RefreshError(f"{slug} current challenge comparison hash or identity is invalid")
                    challenger = comparison.get("challenger")
                    findings = challenger.get("challenge_findings") if isinstance(challenger, dict) else None
                    if not isinstance(findings, list):
                        raise RefreshError(f"{slug} current challenge findings are malformed")
                    current_challenge_findings += len(findings)
            current_challenge_status = (
                "unresolved_not_independently_adjudicated"
                if challenge_enabled and current_challenge_findings
                else "no_current_findings"
                if challenge_enabled
                else "not_run_primary_only_retained_stage"
            )
            attempt["current_challenge_status"] = current_challenge_status
            attempt["current_challenge_finding_count"] = current_challenge_findings
            stage_root = release_dir.parent / ".market-refresh" / attempt_id / "release"
            stage_root.parent.mkdir(parents=True, exist_ok=True)
            staged_release_dir: Path | None = None
            try:
                staged_release_dir = _stage_all_slices(release_dir, slices, stage_root)
                candidate_release = load_release(staged_release_dir)
                candidate_profiles = _profile_identity(candidate_release)
                candidate_versions = _definition_versions(candidate_release)
                if candidate_profiles != current_profiles:
                    raise RefreshError("candidate changes an existing profile identity or registers a new role; operator review is required")
                if candidate_versions != current_versions:
                    raise RefreshError("candidate changes material identity-definition versions; operator review is required")
                blockers = _major_disagreements(slices)
                if blockers:
                    raise RefreshError(f"candidate has {len(blockers)} major unresolved contradiction(s); operator review is required")
                problems = validate_release(staged_release_dir)
                if problems:
                    raise RefreshError("combined four-role candidate failed deterministic release validation: " + "; ".join(problems[:12]))
                final_manifest = dict(candidate_release.manifest)
                skip_rules = final_manifest.get("skip_rules")
                if not isinstance(skip_rules, list):
                    raise RefreshError("candidate skip_rules must be a list before automatic publication")
                minimum_rule = {
                    "rule": "insufficient_admitted_postings",
                    "threshold": policy["min_postings"],
                    "action": (
                        "if any fixed role has fewer than the configured minimum admitted postings, "
                        "block the full candidate and retain the last-good release as current"
                    ),
                }
                updated_skip_rules: list[Any] = []
                minimum_rule_found = False
                for rule in skip_rules:
                    if isinstance(rule, dict) and rule.get("rule") == "insufficient_admitted_postings":
                        updated_skip_rules.append({**rule, **minimum_rule})
                        minimum_rule_found = True
                    else:
                        updated_skip_rules.append(rule)
                if not minimum_rule_found:
                    updated_skip_rules.insert(0, minimum_rule)
                final_manifest["skip_rules"] = updated_skip_rules
                final_manifest["publication_policy"] = {
                    "mode": "automatic",
                    "policy_version": REFRESH_POLICY_VERSION,
                    "policy_sha256": policy_hash,
                    "cadence_hours": policy["cadence_hours"],
                    "min_postings": policy["min_postings"],
                    "max_postings": policy["max_postings"],
                    "max_boards": policy["max_boards"],
                    "selected_stage": retained["selected_stage"],
                    "stage_retention_record_sha256": retained["record_sha256"],
                    "human_reviewed": False,
                    "current_challenge_status": current_challenge_status,
                    "current_challenge_finding_count": current_challenge_findings,
                    "source_grounding": "selected arm passed deterministic quote, citation, rights, schema, and release validation",
                }
                (staged_release_dir / "manifest.json").write_text(
                    json.dumps(final_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                )
                problems = validate_release(staged_release_dir)
                if problems:
                    raise RefreshError("candidate failed final policy validation: " + "; ".join(problems[:12]))
                candidate_hash = load_release(staged_release_dir).content_hash()
                if candidate_hash != candidate_release.content_hash():
                    raise RefreshError("candidate content changed while finalizing publication policy")
                attempt["expected_release_id"] = staged_release_dir.name
                attempt["expected_release_content_sha256"] = candidate_hash
                _atomic_json(receipt_path, attempt)
                _atomic_json(checkpoint, state)
                if staged_release_dir.name == current.release_id:
                    published = {"status": "unchanged", "release_id": current.release_id}
                else:
                    published = publish_release(
                        release_dir,
                        staged_release_dir,
                        expected_current=current.release_id,
                    )
            finally:
                shutil.rmtree(stage_root.parent, ignore_errors=True)

            new_current = CatalogStore(release_dir).release()
            if new_current is None or new_current.release_id != published["release_id"]:
                raise RefreshError("publisher did not leave the expected immutable release as current")
            attempt.update({
                "status": "published" if published["status"] == "published" else "unchanged",
                "finished_at": _stamp(_utc()),
                "release_id": new_current.release_id,
                "release_content_sha256": new_current.content_hash(),
                "source_cache_reuse": [
                    {"url": url, "retrieved_at": row["retrieved_at"], "sha256": row["sha256"]}
                    for url, row in sorted(cache.reused.items())
                ],
            })
            state["last_good"] = {
                "release_id": new_current.release_id,
                "content_sha256": new_current.content_hash(),
                "profile_identity": _profile_identity(new_current),
                "definition_versions": _definition_versions(new_current),
                "recorded_at": attempt["finished_at"],
            }
            state["pending_receipt"] = None
            state["latest_attempt"] = attempt
            state["next_due_at"] = _stamp(_utc() + timedelta(hours=policy["cadence_hours"]))
            _atomic_json(receipt_path, attempt)
            _atomic_json(checkpoint, state)
            return {
                "status": attempt["status"],
                "release_id": new_current.release_id,
                "receipt": str(receipt_path),
                "checkpoint": str(checkpoint),
                "next_due_at": state["next_due_at"],
                "provider_calls": sum(int((row.get("stats") or {}).get("model_calls", 0)) for row in results),
            }
        except Exception as exc:
            actual = CatalogStore(release_dir).release()
            rollback_error: str | None = None
            pointer_changed = actual is None or (
                actual.release_id != last_good["release_id"]
                or actual.content_hash() != last_good["content_sha256"]
            )
            if pointer_changed:
                expected_refresh_release = (
                    actual is None
                    or (
                        actual.release_id == attempt.get("expected_release_id")
                        and actual.content_hash() == attempt.get("expected_release_content_sha256")
                    )
                )
                if not expected_refresh_release:
                    rollback_error = "current release changed outside this refresh; operator reconciliation is required"
                    attempt["rollback_error"] = rollback_error
                else:
                    try:
                        rollback_current(release_dir, str(last_good["release_id"]))
                        actual = CatalogStore(release_dir).release()
                    except Exception as rollback_exc:
                        rollback_error = str(rollback_exc)
                        attempt["rollback_error"] = rollback_error
            attempt.update({"status": "failed", "finished_at": _stamp(_utc()), "error": str(exc)})
            state["last_good"] = last_good
            state["latest_attempt"] = attempt
            if rollback_error:
                state["pending_receipt"] = {
                    "attempt_id": attempt_id,
                    "receipt_path": str(receipt_path),
                    "status": "rollback_failed",
                }
                state["next_due_at"] = _stamp(_utc())
            else:
                state["pending_receipt"] = None
                state["next_due_at"] = _stamp(_utc() + timedelta(hours=policy["cadence_hours"]))
            _atomic_json(receipt_path, attempt)
            _atomic_json(checkpoint, state)
            return {
                "status": "failed",
                "release_id": actual.release_id if actual else str(last_good["release_id"]),
                "last_good_release_id": str(last_good["release_id"]),
                "error": str(exc),
                "receipt": str(receipt_path),
                "checkpoint": str(checkpoint),
                "next_due_at": state["next_due_at"],
                "provider_calls": sum(int((row.get("stats") or {}).get("model_calls", 0)) for row in results),
            }
        finally:
            shutil.rmtree(scratch_root.parent, ignore_errors=True)


__all__ = [
    "DEFAULT_SOURCE_CACHE_AGE", "DEFINITION_VERSIONS", "FIXED_ROLES", "REFRESH_POLICY_VERSION",
    "RefreshError", "SourceEvidenceCache", "load_refresh_policy", "run_refresh_once",
]
