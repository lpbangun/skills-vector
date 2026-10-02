"""Test support for the market reference core (synthetic, temp-dir scoped).

These helpers build synthetic slices/releases for isolated behavior tests only.
They never write to preview/release or ship as product data.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from skills_vector.market.release import expectation_relationship_id, sha256_text, source_literal_identity
from skills_vector.market.sources import dedup_key, normalize_ws

FOUNDATION_TEXT = (
    "O*NET OnLine summary for Human Resources Specialists: human resources generalists "
    "administer employee lifecycle processes, maintain HRIS records, support employee relations, "
    "coordinate onboarding and offboarding, and help administer benefits and policy compliance."
)

DEFAULT_STATEMENT = "HR generalists administer onboarding, HRIS records and employee lifecycle processes."
DEFAULT_QUOTE = "administer employee lifecycle processes"


def default_claim_id(run_id: str = "run_test_0001") -> str:
    return "clm_" + sha256_text(f"{run_id}|{DEFAULT_STATEMENT}|{DEFAULT_QUOTE}")[:16]


def _pipeline_id(prefix: str, *parts: str) -> str:
    """Mirror pipeline ``_id`` so tests can predict recorded claim/requirement ids."""

    return f"{prefix}_{sha256_text('|'.join(parts))[:20]}"


def demand_claim_id(run_id: str, occupation: str, topic: str, posting_dedup_keys: list[str] | str) -> str:
    """Demand claim ids are derived from run, role, topic and the matched sample."""

    keys = [posting_dedup_keys] if isinstance(posting_dedup_keys, str) else list(posting_dedup_keys)
    return _pipeline_id("clm", run_id, occupation, topic, ",".join(sorted(keys)))


def requirement_id(run_id: str, occupation: str, index: int, label: str, learning_outcome: str) -> str:
    return _pipeline_id("req", run_id, occupation, str(index), label, learning_outcome)


def build_slice(
    root: Path,
    *,
    run_id: str = "run_test_0001",
    occupation: str = "hr-generalist",
    label: str | None = None,
    claim_statement: str | None = None,
    claim_quote: str | None = None,
    stats_override: dict[str, Any] | None = None,
) -> Path:
    """Write a synthetic occupation slice directory (valid unless tampered)."""

    root = Path(root)
    from skills_vector.market.pipeline import OCCUPATION_CONFIG

    registry = OCCUPATION_CONFIG[occupation]
    label = label or str(registry["label"])
    scope = {
        "geography": str(registry["geography"]),
        "responsibility_scope": str(registry["responsibility_scope"]),
    }
    (root / "extracts").mkdir(parents=True, exist_ok=True)
    suffix = "" if occupation == "hr-generalist" else "_" + occupation.replace("-", "_")
    source_id = "src_test_foundation" + suffix
    board_source_id = "src_test_board" + suffix
    extract_text = f"# source {source_id}\n{FOUNDATION_TEXT}\n"
    (root / "extracts" / f"{source_id}.txt").write_text(extract_text, encoding="utf-8")
    board_text = (
        "This is an individual contributor role with no direct reports. "
        "Independently own end-to-end onboarding workflows. "
        "Required: experience with HRIS records. Our employer operates in manufacturing. "
        "Clients include healthcare organizations. 3 years of experience. Hybrid work in Austin, TX."
    )
    board_extract = f"# synthetic test source {board_source_id}\n{board_text}\n"
    (root / "extracts" / f"{board_source_id}.txt").write_text(board_extract, encoding="utf-8")
    board_sha = sha256_text(board_text)
    contexts = {}
    for field_name, value, phrase in (
        ("employer_industry", "manufacturing", "Our employer operates in manufacturing"),
        ("customer_industry", "healthcare", "Clients include healthcare organizations"),
        ("work_context", "Hybrid", "Hybrid work in Austin, TX"),
        ("geography", "Austin, TX", "Hybrid work in Austin, TX"),
        ("sales_segment", None, None),
        ("employer_size", None, None),
        ("employer_stage", None, None),
    ):
        contexts[field_name] = {
            "value": value, "status": "present" if value else "unknown",
            "quote": phrase, "source_id": board_source_id, "source_sha256": board_sha,
            "method": "synthetic-test-annotation/1",
            "unknown_reason": None if value else "not stated in synthetic source",
        }
    expectations = []
    for dimension, basis, phrase in (
        ("task", "emergent_signal", "Independently own end-to-end onboarding workflows"),
        ("knowledge", "employer_requirement", "experience with HRIS records"),
    ):
        normalized = " ".join(phrase.split()).casefold()
        expectation_id = "exp_" + sha256_text(f"expectation/2|{dimension}|{basis}|{normalized}")[:20]
        expectations.append({
            "expectation_id": expectation_id,
            "identity_id": source_literal_identity(dimension, phrase),
            "identity_method": "source-literal-identity/1", "kind": dimension,
            "relationship_id": expectation_relationship_id(
                occupation, "dedup_test_0001" + suffix, board_source_id, expectation_id,
            ),
            "relationship_method": "posting-source-expectation/1",
            "source_wording": phrase, "normalized_label": normalized,
            "dimension": dimension, "basis": basis, "proficiency": "not_stated",
            "proficiency_quote": None, "mapping_method": "exact-normalized-label/2",
            "source_id": board_source_id, "source_sha256": board_sha,
        })
    statement = claim_statement or "HR generalists administer onboarding, HRIS records and employee lifecycle processes."
    quote = claim_quote if claim_quote is not None else "administer employee lifecycle processes"
    identity = f"{run_id}|{statement}|{quote}"
    if suffix:
        identity += "|" + occupation
    claim_id = "clm_" + sha256_text(identity)[:16]
    postings = [
        {
            "id": "pst_test_0001" + suffix,
            "occupation_slug": occupation,
            "variant": None,
            "title": label,
            "employer": "Testco",
            "location": "Austin, TX",
            "url": "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true#1",
            "posted_at": "2026-09-01",
            "source_id": board_source_id,
            "seniority": "mid",
            "work_level": "individual_contributor",
            "work_level_reason": "Synthetic test row: personally performs the work with no direct reports.",
            "people_management_quote": "",
            "work_level_evidence": {
                "source_id": board_source_id, "source_sha256": board_sha,
                "quote": "individual contributor role with no direct reports",
                "reason": "explicit synthetic non-manager wording",
                "method": "synthetic-test-annotation/1",
            },
            "responsibility_band": "independent_ic",
            "responsibility_evidence": {
                "source_id": board_source_id, "source_sha256": board_sha,
                "quote": "Independently own end-to-end onboarding workflows",
                "reason": "explicit synthetic autonomy wording",
                "method": "synthetic-test-annotation/1",
            },
            "context_dimensions": contexts,
            "advertised_experience": {
                "value": ["3 years of experience"], "quotes": ["3 years of experience"],
                "status": "present", "source_id": board_source_id, "source_sha256": board_sha,
                "method": "verbatim-advertised-wording/1",
            },
            "expectations": expectations,
            "skills": ["onboarding"],
            "dedup_key": "dedup_test_0001" + suffix,
            "admission_reason": "synthetic test row",
        }
    ]
    stats = {
        "total_seen": len(postings),
        "sampled": len(postings),
        "postings_dedup": len(postings),
        "employers_dedup": 1,
        "boards_attempted": 1,
        "boards_used": 1,
        "excluded_total": 0,
        "foundations_used": 1,
        "work_level_counts": {"individual_contributor": 1},
        "responsibility_band_counts": {"independent_ic": 1},
        "expectation_dimension_counts": {"knowledge": 1, "task": 1},
        "expectation_basis_counts": {"employer_requirement": 1, "emergent_signal": 1},
        "context_value_counts": {
            name: {row["value"]: 1} if row["value"] else {} for name, row in contexts.items()
        },
    }
    if stats_override:
        stats.update(stats_override)
    occupations = [
        {
            "slug": occupation,
            "label": label,
            "family": "People Operations & Talent",
            "role_scope": scope,
            "sampled_at": "2026-09-30T00:00:00Z",
            "sampling_note": "synthetic test sample; not market prevalence",
            "stats": stats,
            "run_ids": [run_id],
        }
    ]
    occupations[0].update({
        key: registry[key] for key in (
            "aliases", "growth_variants", "provisional", "registration_status", "human_review_status",
            "publication_status", "official_anchor", "alias_decisions",
        ) if key in registry
    })
    if occupation == "growth-manager":
        occupations[0]["growth_variants"] = ["product-growth", "growth-marketing", "sales-account-executive"]
        postings[0]["variant"] = "product-growth"
    occupations[0]["coverage"] = {
        "planned": {"objective": "isolated synthetic domain scenario", "employers_target": 3},
        "achieved": {
            "postings": 1, "employers": 1, "employer_industries": ["manufacturing"],
            "responsibility_band_counts": stats["responsibility_band_counts"],
            "context_value_counts": stats["context_value_counts"],
        },
        "unsupported_variants": {
            value: {
                "status": "unavailable", "value": value,
                "attempt_source_ids": [board_source_id],
                "query_terms": ["growth manager", "product growth", "growth marketing", "account executive"],
            }
            for value in occupations[0].get("growth_variants", []) if value != "product-growth"
        },
        "unsupported_bands": {
            value: {"status": "unavailable", "value": value, "attempt_source_ids": [board_source_id], "query_terms": [label]}
            for value in ("early_career", "senior_strategic_ic", "people_management")
        },
        "unavailable_sources": [], "caps_reached": {}, "sample_date": "2026-09-30",
        "limitation": "Synthetic isolated test data, never a public occupational finding.",
    }
    sources = [
        {
            "id": source_id,
            "url": "https://www.onetonline.org/link/summary/13-1071.00",
            "publisher": "O*NET OnLine (U.S. Department of Labor)",
            "source_type": "foundation",
            "occupation_slug": occupation,
            "retrieved_at": "2026-09-30T00:00:00Z",
            "sha256": sha256_text(FOUNDATION_TEXT),
            "bytes": len(FOUNDATION_TEXT),
            "rights": "O*NET OnLine, U.S. Department of Labor (CC BY 4.0); short excerpts published with attribution",
            "inclusion": True,
            "extract_path": f"extracts/{source_id}.txt",
            "extract_sha256": sha256_text(extract_text),
            "role_scope": scope,
        },
        {
            "id": board_source_id,
            "url": "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true",
            "publisher": "Testco",
            "source_type": "job-board",
            "retrieval_kind": "listing",
            "occupation_slug": occupation,
            "retrieved_at": "2026-09-30T00:00:00Z",
            "sha256": board_sha,
            "bytes": len(board_text.encode("utf-8")),
            "rights": "Public employer job posting via public job-board API; short excerpts published with employer attribution",
            "inclusion": True,
            "role_scope": scope,
            "extract_path": f"extracts/{board_source_id}.txt",
            "extract_sha256": sha256_text(board_extract),
        },
    ]
    claims = [
        {
            "id": claim_id,
            "occupation_slug": occupation,
            "claim_type": "foundation",
            "statement": statement,
            "quote": quote,
            "expectation_dimension": "task",
            "evidence_basis": "official_foundation",
            "source_ids": [source_id],
            "scope": scope,
            "evidence": {"kind": "official foundation excerpt"},
            "confidence": "bounded",
            "tags": ["onboarding", "hris"],
            "agent_run_id": run_id,
            "asserted_at": "2026-09-30T00:00:00Z",
        }
    ]
    requirements = [
        {
            "id": "req_test_0001" + suffix,
            "occupation_slug": occupation,
            "priority": 1,
            "label": "Employee lifecycle operations",
            "learning_outcome": "Run onboarding and offboarding workflows with auditable HRIS records.",
            "rationale": "Foundation and sample evidence both describe lifecycle administration.",
            "uncertainty": "Sample is small; ordering is evidence-based priority, not measured importance.",
            "confidence": "low",
            "basis": "foundation",
            "search_terms": ["onboarding"],
            "evidence_claim_ids": [claim_id],
        }
    ]
    for stem, rows in (
        ("occupations", occupations),
        ("sources", sources),
        ("postings", postings),
        ("claims", claims),
        ("requirements", requirements),
        ("lineage", [{
            "run_id": run_id, "occupation_slug": occupation,
            "execution_context": "isolated-unit-test", "fixtures_used": True,
            "provider": None, "models": {}, "model_fallback": None,
        }]),
    ):
        (root / f"{stem}.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return root


def make_candidate(slice_dir: Path, workdir: Path) -> Path:
    """Merge a slice into a fresh staging candidate (no old release carried over)."""

    from skills_vector.market.publish import merge_slice

    release_root = Path(workdir) / "release-root"
    release_root.mkdir(parents=True, exist_ok=True)
    return merge_slice(release_root, Path(slice_dir), generated_at="2026-09-30T00:00:00Z")


def publish_test_release(release_root: Path, slice_dir: Path) -> dict[str, Any]:
    from skills_vector.market.publish import merge_slice, publish_release

    candidate = merge_slice(Path(release_root), Path(slice_dir), generated_at="2026-09-30T00:00:00Z")
    return publish_release(Path(release_root), candidate)




def active_runtime_bundle(
    root: Path,
    *,
    allocation_usd: float = 1.0,
    monthly_cap_usd: float = 10.0,
    limit_overrides: dict[str, int] | None = None,
):
    """Create a fully isolated active DeepInfra config and seeded mission ledger.

    Tests replace the HTTP opener; these deliberately synthetic price rows and
    a test-only environment key never authorize or reach a product provider.
    """

    from skills_vector.budget import BudgetLedger
    from skills_vector.market.budgeting import MissionBudget
    from skills_vector.market.research_config import (
        CONFIG_SCHEMA,
        ENDPOINT,
        HARD_LIMITS,
        PROVIDER,
        load_research_config,
    )

    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    month = datetime.now(UTC).strftime("%Y-%m")
    model_ids = {
        "primary": "deepseek-ai/DeepSeek-V4.1-Flash",
        "challenger": "zai-org/GLM-5.3-Flash",
        "escalation": "zai-org/GLM-5.3",
    }
    price_rows = {
        model_id: {
            "model_id": model_id,
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 1.0,
            "standard_tier": True,
            "promotional_discount_not_applied": True,
            "catalog_record": {
                "model_name": model_id,
                "pricing": {"rate_per_input_token_cached": 0.1},
            },
        }
        for model_id in model_ids.values()
    }
    pricing_receipt = root / "test-pricing-receipt.json"
    pricing_bytes = json.dumps(
        {"provider": PROVIDER, "endpoint": ENDPOINT, "verified_at": f"{month}-01T00:00:00Z", "models": price_rows},
        sort_keys=True,
    ).encode("utf-8")
    pricing_receipt.write_bytes(pricing_bytes)

    ledger_path = root / "test-budget.sqlite3"
    mission_id = "test-market-mission"
    connection = sqlite3.connect(ledger_path)
    try:
        ledger = BudgetLedger(connection, monthly_cap_usd=monthly_cap_usd)
        reservation_id = ledger.reserve(
            run_id=mission_id,
            model_id="catalog-wide-deepinfra",
            estimated_usd=allocation_usd,
            note="isolated market test reservation",
        )
    finally:
        connection.close()

    limits = dict(HARD_LIMITS)
    limits.update(limit_overrides or {})
    config_path = root / "test-research-config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": CONFIG_SCHEMA,
                "provider": PROVIDER,
                "endpoint": ENDPOINT,
                "ledger_path": str(ledger_path),
                "month": month,
                "monthly_cap_usd": monthly_cap_usd,
                "mission_id": mission_id,
                "mission_reservation_id": reservation_id,
                "mission_allocation_usd": allocation_usd,
                "pricing_receipt_path": str(pricing_receipt),
                "pricing_receipt_sha256": hashlib.sha256(pricing_bytes).hexdigest(),
                "models": model_ids,
                "limits": limits,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    product_root = Path(__file__).resolve().parents[1]
    runtime = load_research_config(config_path, product_root=product_root)
    return runtime, MissionBudget(runtime)


def greenhouse_payload(jobs: list[dict[str, Any]]) -> bytes:
    return json.dumps({"jobs": jobs}).encode("utf-8")


def sample_job(job_id: str, title: str, location: str, description: str, token: str = "testco") -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{job_id}",
        "updated_at": "2026-09-15T00:00:00Z",
        "content": description,
    }


def transport_for(job_payload: bytes, foundation_body: bytes) -> Callable[[str], tuple[int, dict[str, str], bytes]]:
    def transport(url: str) -> tuple[int, dict[str, str], bytes]:
        if "onetonline.org" in url:
            return 200, {"content-type": "text/html; charset=utf-8"}, foundation_body
        if "boards-api.greenhouse.io" in url:
            return 200, {"content-type": "application/json"}, job_payload
        return 404, {"content-type": "text/plain"}, b"not found"

    return transport


def routed_transport(
    routes: dict[str, bytes],
    *,
    foundation_body: bytes,
    statuses: dict[str, int] | None = None,
) -> Callable[[str], tuple[int, dict[str, str], bytes]]:
    """Transport stub matching URL substrings in insertion order (first match wins)."""

    def transport(url: str) -> tuple[int, dict[str, str], bytes]:
        if "onetonline.org" in url:
            return 200, {"content-type": "text/html; charset=utf-8"}, foundation_body
        for fragment, body in routes.items():
            if fragment in url:
                status = (statuses or {}).get(fragment, 200)
                return status, {"content-type": "application/json"}, body
        return 404, {"content-type": "text/plain"}, b"not found"

    return transport


def greenhouse_detail_payload(job_id: str, title: str, location: str, description: str) -> bytes:
    return json.dumps(
        {
            "id": job_id,
            "title": title,
            "location": {"name": location},
            "absolute_url": f"https://boards.greenhouse.io/testco/jobs/{job_id}",
            "updated_at": "2026-09-15T00:00:00Z",
            "content": description,
        }
    ).encode("utf-8")


def dedup_for(employer: str, job_id: str, url: str) -> str:
    return dedup_key(employer, job_id, url)


def normalized(text: str) -> str:
    return normalize_ws(text)
