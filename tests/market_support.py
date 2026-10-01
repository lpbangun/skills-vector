"""Test support for the market reference core (synthetic, temp-dir scoped).

These helpers build synthetic slices/releases and scripted agent responses for
deterministic behavior tests only. They never write into ``preview/release`` and
never ship as product data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from skills_vector.market.release import sha256_text
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
    label: str = "HR Generalist",
    geography: str = "United States",
    seniority: str = "mid-level individual contributor",
    claim_statement: str | None = None,
    claim_quote: str | None = None,
    stats_override: dict[str, Any] | None = None,
) -> Path:
    """Write a synthetic occupation slice directory (valid unless tampered)."""

    root = Path(root)
    (root / "extracts").mkdir(parents=True, exist_ok=True)
    source_id = "src_test_foundation"
    board_source_id = "src_test_board"
    extract_text = f"# source {source_id}\n{FOUNDATION_TEXT}\n"
    (root / "extracts" / f"{source_id}.txt").write_text(extract_text, encoding="utf-8")
    scope = {"geography": geography, "seniority": seniority}
    statement = claim_statement or "HR generalists administer onboarding, HRIS records and employee lifecycle processes."
    quote = claim_quote if claim_quote is not None else "administer employee lifecycle processes"
    claim_id = "clm_" + sha256_text(f"{run_id}|{statement}|{quote}")[:16]
    postings = [
        {
            "id": "pst_test_0001",
            "occupation_slug": occupation,
            "variant": None,
            "employer": "Testco",
            "title": "HR Generalist",
            "location": "Austin, TX",
            "url": "https://boards-api.greenhouse.io/v1/boards/testco/jobs?content=true#1",
            "posted_at": "2026-09-01",
            "source_id": board_source_id,
            "seniority": "mid",
            "work_level": "individual_contributor",
            "work_level_reason": "Synthetic test row: personally performs the work with no direct reports.",
            "people_management_quote": "",
            "skills": ["onboarding"],
            "dedup_key": "dedup_test_0001",
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
            "sha256": sha256_text("{}"),
            "bytes": 2,
            "rights": "Public employer job posting via public job-board API; short excerpts published with employer attribution",
            "inclusion": True,
            "role_scope": scope,
        },
    ]
    claims = [
        {
            "id": claim_id,
            "occupation_slug": occupation,
            "claim_type": "foundation",
            "statement": statement,
            "quote": quote,
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
            "id": "req_test_0001",
            "occupation_slug": occupation,
            "priority": 1,
            "label": "Employee lifecycle operations",
            "learning_outcome": "Run onboarding and offboarding workflows with auditable HRIS records.",
            "rationale": "Foundation and sample evidence both describe lifecycle administration.",
            "uncertainty": "Sample is small; ordering is evidence-based priority, not measured importance.",
            "confidence": "low",
            "basis": "advertised_demand",
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


class ScriptedAgent:
    """Deterministic stand-in for the OMP CLI subprocess.

    Writes a valid OMP-shaped session record into the ``--session-dir`` passed on
    the command line, so the runner's identity verification is exercised.
    """

    def __init__(
        self,
        responses: dict[str, str],
        *,
        fallback: bool = False,
        model: str = "opencode-go/deepseek-v4.1-flash",
        thinking: str = "max",
    ) -> None:
        self.responses = responses
        self.fallback = fallback
        self.model = model
        self.thinking = thinking
        self.calls: list[list[str]] = []

    def _session_dir(self, cmd: list[str]) -> Path:
        return Path(cmd[cmd.index("--session-dir") + 1])

    def _stage(self, prompt: str) -> str:
        if "discovery feedback pass" in prompt:
            return "discovery_feedback"
        if "discovery pass" in prompt:
            return "discovery"
        if "extraction/admission pass" in prompt:
            return "admission"
        if "reconciliation/synthesis pass" in prompt:
            return "reconciliation"
        if "evidence-linking pass" in prompt:
            return "evidence_linking"
        if "challenge/skeptic pass" in prompt:
            return "challenge"
        return "unknown"

    def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str, str]:
        self.calls.append(cmd)
        prompt = cmd[-1]
        session_dir = self._session_dir(cmd)
        session_dir.mkdir(parents=True, exist_ok=True)
        session = session_dir / f"session-{len(self.calls):02d}.jsonl"
        records = [
            {"type": "session", "id": f"test-{len(self.calls)}"},
            {
                "type": "model_change",
                "model": self.model,
                "resolvedModelIsFallback": self.fallback,
            },
            {"type": "thinking_level_change", "thinkingLevel": self.thinking},
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "provider": "opencode-go",
                    "model": self.model.split("/")[-1],
                    "usage": {"cost": {"total": 0.0}},
                },
            },
        ]
        session.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        return 0, self.responses.get(self._stage(prompt), "{}"), ""


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
