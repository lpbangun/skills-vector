"""Round-one HR Generalist Structured Job Analysis over the frozen public dev corpus.

The occupational backbone is an authored, O*NET/OPM-anchored desk analysis. Posting
text is used only for counts-only demand observations and explicitly labeled context
adaptations. This module never retrieves sources and never treats model output as
validation.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .budget import PINNED_MODELS, estimate_cost_usd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_DIR = Path(
    "/home/logani/oprun-evidence/skills-vector-poc-prep-74b159c/shared-benchmark"
)
DEFAULT_BASE_CORPUS_DIR = DEFAULT_BENCHMARK_DIR.parent / "corpus"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs/structured-job-analysis-round1"
DEFAULT_LIVE_LOCK = REPO_ROOT / ".poc-env/state/structured-job-analysis-final-live.json"

EXPECTED_BENCHMARK_MANIFEST_SHA256 = "bcb3d5e5aff1dd915b549b9397135e1d102e08b5ceb4cbe750633ee3a8baeb5e"
EXPECTED_DEV_SPLIT_SHA256 = "ad3a28e495a47c47ee2fc967796e8c9f2d87b6c62c94a819b2a9dcefc605d67f"
EXPECTED_BASE_MANIFEST_SHA256 = "81df7df71c2e22a896e23765d3ad877d6084b2f9c4c63b4f6be2093cf3091317"
SNAPSHOT_IDS = (
    "luna-max-hr-generalist-starter-2026-09-23",
    "shared-reviewer-hr-ic-postings-v2-2026-09-23",
)
PINNED_MODEL_ID = "deepseek-ai/DeepSeek-V4.1-Flash"
MAX_FINAL_SPEND_USD = 0.50
LIVE_ENDPOINT = "https://api.deepinfra.com/v1/openai/chat/completions"
LIVE_SYSTEM_PROMPT = (
    "You are an untrusted evidence-review assistant for a desk-research job analysis. "
    "Review only the supplied, cited work units and evidence. Do not add sources, duties, "
    "competencies, proficiency levels, or practitioner claims. Return one JSON object: "
    "{\"reviews\":[{\"unit_id\":string,\"stance\":\"supported\"|\"challenge\"|\"unclear\","
    "\"reason\":string,\"evidence\":[{\"source_id\":string,\"locator\":string,"
    "\"quote\":string}]}]}. A review is a model suggestion, not validation."
)


class StructuredAnalysisError(RuntimeError):
    """A fail-closed corpus, citation, resource, or run-once error."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StructuredAnalysisError(f"cannot read JSON input {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise StructuredAnalysisError(f"expected a JSON object in {path}")
    return value


def _verify_readonly_file(path: Path, expected_hash: str, expected_bytes: int | None = None) -> bytes:
    try:
        raw = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise StructuredAnalysisError(f"frozen input unavailable: {path}") from exc
    if _sha256(raw) != expected_hash:
        raise StructuredAnalysisError(f"frozen input hash mismatch: {path}")
    if expected_bytes is not None and len(raw) != expected_bytes:
        raise StructuredAnalysisError(f"frozen input byte-count mismatch: {path}")
    if stat.st_mode & 0o222:
        raise StructuredAnalysisError(f"frozen input is writable: {path}")
    return raw


def _safe_manifest_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise StructuredAnalysisError(f"path escapes frozen corpus: {relative}") from exc
    return candidate


@dataclass(frozen=True, slots=True)
class FrozenCorpus:
    benchmark_dir: Path
    base_corpus_dir: Path
    benchmark_manifest: dict[str, Any]
    base_manifest: dict[str, Any]
    postings: tuple[dict[str, Any], ...]
    extracts: dict[str, str]
    sources: dict[str, dict[str, Any]]
    source_texts: dict[str, str]
    source_paths: dict[str, Path]
    base_manifest_sha256: str
    benchmark_manifest_sha256: str


def load_frozen_corpus(benchmark_dir: Path, base_corpus_dir: Path) -> FrozenCorpus:
    """Verify and load only v1 sources, public v2 dev postings, and splits/dev.json.

    The held-out tree is neither opened nor enumerated.
    """
    benchmark_dir = benchmark_dir.resolve()
    base_corpus_dir = base_corpus_dir.resolve()
    benchmark_manifest_path = benchmark_dir / "corpus/manifest.json"
    benchmark_raw = benchmark_manifest_path.read_bytes()
    benchmark_hash = _sha256(benchmark_raw)
    if benchmark_hash != EXPECTED_BENCHMARK_MANIFEST_SHA256:
        raise StructuredAnalysisError("frozen v2 manifest hash does not match the round-zero pin")
    benchmark = json.loads(benchmark_raw)
    if benchmark.get("schema_version") != "skills-vector.poc-corpus.v2":
        raise StructuredAnalysisError("unexpected v2 corpus schema")
    if benchmark.get("snapshot_id") != SNAPSHOT_IDS[1]:
        raise StructuredAnalysisError("unexpected v2 snapshot id")

    split_path = benchmark_dir / "splits/dev.json"
    split_raw = _verify_readonly_file(split_path, EXPECTED_DEV_SPLIT_SHA256)
    split = json.loads(split_raw)
    dev_rows = split.get("items")
    if not isinstance(dev_rows, list):
        raise StructuredAnalysisError("frozen dev split has no item list")

    base_manifest_path = base_corpus_dir / "manifest.json"
    base_raw = base_manifest_path.read_bytes()
    base_hash = _sha256(base_raw)
    if base_hash != EXPECTED_BASE_MANIFEST_SHA256 or base_hash != benchmark["base_snapshot"]["manifest_sha256"]:
        raise StructuredAnalysisError("frozen v1 starter manifest hash does not match the round-zero pin")
    if Path(benchmark["base_snapshot"]["manifest_path"]).resolve() != base_manifest_path:
        raise StructuredAnalysisError("base corpus path differs from the frozen v2 manifest")
    base = json.loads(base_raw)
    if base.get("snapshot_id") != SNAPSHOT_IDS[0] or len(base.get("sources", ())) != 6:
        raise StructuredAnalysisError("unexpected v1 starter snapshot or source count")

    extracts: dict[str, str] = {}
    sources: dict[str, dict[str, Any]] = {}
    source_texts: dict[str, str] = {}
    source_paths: dict[str, Path] = {}
    for source in base["sources"]:
        if source.get("status") != "retrieved":
            raise StructuredAnalysisError(f"v1 source is not retrieved: {source.get('id')}")
        raw_path = _safe_manifest_path(base_corpus_dir, source["path"])
        extract_path = _safe_manifest_path(base_corpus_dir, source["extract_path"])
        _verify_readonly_file(raw_path, source["sha256"], source.get("bytes"))
        extract_raw = _verify_readonly_file(
            extract_path, source["extract_sha256"], source.get("extract_bytes")
        )
        source_id = source["id"]
        if source_id in source_texts:
            raise StructuredAnalysisError(f"duplicate v1 source id: {source_id}")
        source_texts[source_id] = extract_raw.decode("utf-8")
        source_paths[source_id] = extract_path
        sources[source_id] = source
        extracts[source_id] = source_texts[source_id]

    postings = tuple(benchmark.get("postings", ()))
    if len(postings) != 7 or any(row.get("split") != "dev" for row in postings):
        raise StructuredAnalysisError("the public v2 corpus must contain exactly seven dev items")
    if len({row["id"] for row in postings}) != 7:
        raise StructuredAnalysisError("duplicate or missing v2 dev posting ids")
    if len({row["duplicate_family"] for row in postings}) != 7:
        raise StructuredAnalysisError("unexpected duplicate family in the frozen dev items")
    if len(dev_rows) != 7 or {row["id"] for row in dev_rows} != {row["id"] for row in postings}:
        raise StructuredAnalysisError("manifest postings and public dev split disagree")
    for posting in postings:
        if posting.get("geography", {}).get("country") != "US":
            raise StructuredAnalysisError(f"non-US posting in dev input: {posting['id']}")
        if posting.get("role_class") not in {"common_core", "context_addition"}:
            raise StructuredAnalysisError(f"unlabeled posting role class: {posting['id']}")
        rel_extract = posting["extract"]["path"]
        rel_raw = posting["raw"]["path"]
        raw_path = _safe_manifest_path(benchmark_dir / "corpus", rel_raw)
        extract_path = _safe_manifest_path(benchmark_dir / "corpus", rel_extract)
        _verify_readonly_file(raw_path, posting["raw"]["sha256"], posting["raw"].get("bytes"))
        extract_raw = _verify_readonly_file(
            extract_path, posting["extract"]["sha256"], posting["extract"].get("bytes")
        )
        source_id = posting["id"]
        if source_id in source_texts:
            raise StructuredAnalysisError(f"duplicate source id across v1/v2: {source_id}")
        source_texts[source_id] = extract_raw.decode("utf-8")
        source_paths[source_id] = extract_path
        sources[source_id] = posting
        extracts[source_id] = source_texts[source_id]

    expected_ids = set(source_texts)
    if len(expected_ids) != 13:
        raise StructuredAnalysisError("expected six v1 source ids plus seven v2 dev posting ids")
    return FrozenCorpus(
        benchmark_dir=benchmark_dir,
        base_corpus_dir=base_corpus_dir,
        benchmark_manifest=benchmark,
        base_manifest=base,
        postings=postings,
        extracts=extracts,
        sources=sources,
        source_texts=source_texts,
        source_paths=source_paths,
        base_manifest_sha256=base_hash,
        benchmark_manifest_sha256=benchmark_hash,
    )


def _citation(source_id: str, locator: str, quote: str, **extra: Any) -> dict[str, Any]:
    return {"source_id": source_id, "locator": locator, "quote": quote, **extra}


# Short exact spans from the v1 O*NET/OPM/DACUM captures. They are verified against
# the frozen extract bytes before a release is written.
TASKS: tuple[dict[str, Any], ...] = (
    {
        "unit_id": "task-policy-guidance",
        "statement": "Interpret HR policies, procedures, laws, and regulations and explain how they apply to employees and managers.",
        "quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-hr-knowledge", "competency-law-policy", "competency-written-communication"],
        "links": {
            "competency-hr-knowledge": "The task is explicitly about interpreting HR policy; personnel and human-resources knowledge supports accurate explanation.",
            "competency-law-policy": "The task explicitly covers laws, standards, and regulations; law-and-policy literacy supports accurate interpretation.",
            "competency-written-communication": "The task requires explaining rules to others; written expression supports clear, usable guidance.",
        },
    },
    {
        "unit_id": "task-employee-relations",
        "statement": "Address employee-relations concerns, including complaints, harassment allegations, and other employee issues.",
        "quote": "Address employee relations issues, such as harassment allegations, work complaints, or other employee concerns.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-active-listening", "competency-social-perceptiveness", "competency-judgment"],
        "links": {
            "competency-active-listening": "Intake and resolution require understanding what employees report; this is an analyst inference from the task and listening definition.",
            "competency-social-perceptiveness": "Handling employee concerns benefits from interpreting others' reactions; the pairing is desk-research judgment, not a task rating.",
            "competency-judgment": "Employee-relations issues may require choosing among actions; this directional link is a desk-research inference, not validated assessment evidence.",
        },
    },
    {
        "unit_id": "task-employee-records",
        "statement": "Prepare and maintain accurate employee records across hiring, termination, leave, transfer, and promotion events using HR systems.",
        "quote": "Prepare or maintain employment records related to events, such as hiring, termination, leaves, transfers, or promotions, using human resources management system software.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-administrative-records", "competency-hr-knowledge"],
        "links": {
            "competency-administrative-records": "The task names records and HR-management software; administrative systems knowledge supports accurate record handling.",
            "competency-hr-knowledge": "The records concern personnel lifecycle events; HR knowledge supports correct handling of personnel information.",
        },
    },
    {
        "unit_id": "task-manager-guidance",
        "statement": "Provide managers with information or training about performance processes, counseling practices, and documentation of performance issues.",
        "quote": "performance appraisals, counseling techniques, or documentation of performance issues.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-instructing", "competency-hr-knowledge", "competency-written-communication"],
        "links": {
            "competency-instructing": "The task expressly includes information or training; instructing is therefore a directionally relevant desk-research link.",
            "competency-hr-knowledge": "The task concerns HR practices and performance processes; HR knowledge supports accurate manager guidance.",
            "competency-written-communication": "The task includes documenting performance issues; clear written expression supports usable records and guidance.",
        },
    },
    {
        "unit_id": "task-new-hire-orientation",
        "statement": "Schedule or conduct new-employee orientation as one part of the broader employee lifecycle.",
        "quote": "Schedule or conduct new employee orientations.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-instructing", "competency-active-listening"],
        "links": {
            "competency-instructing": "Orientation conveys work information to new employees; the competency link is a desk-research inference from the task wording.",
            "competency-active-listening": "Orientation also involves responding to questions; this is a cautious task-design inference, not a measured proficiency requirement.",
        },
    },
    {
        "unit_id": "task-people-data-reporting",
        "statement": "Analyze employment-related data and prepare required reports; the source does not establish a particular analytics tool or proficiency level.",
        "quote": "Analyze employment-related data and prepare required reports.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-critical-thinking", "competency-judgment"],
        "links": {
            "competency-critical-thinking": "Analyzing employment data maps directionally to reasoning through information; the link is authored desk research, not an observed rating.",
            "competency-judgment": "Reports may inform decisions, but the task alone does not prove a specific analytical depth; this remains a medium-confidence inference.",
        },
    },
)

COMPETENCIES: tuple[dict[str, Any], ...] = (
    {
        "unit_id": "competency-hr-knowledge",
        "statement": "Personnel and human-resources knowledge covering HR procedures, training, compensation and benefits, labor relations, and personnel information systems.",
        "quote": "Knowledge of principles and procedures for personnel recruitment, selection, training, compensation and benefits, labor relations and negotiation, and personnel information systems.",
        "locator": "O*NET OnLine 13-1071.00 › Knowledge › Personnel and Human Resources",
    },
    {
        "unit_id": "competency-law-policy",
        "statement": "Working knowledge of applicable laws, government regulations, and agency or organizational rules relevant to assigned HR work.",
        "quote": "Knowledge of laws, legal codes, court procedures, precedents, government regulations, executive orders, agency rules",
        "locator": "O*NET OnLine 13-1071.00 › Knowledge › Law and Government",
    },
    {
        "unit_id": "competency-active-listening",
        "statement": "Attend to employee and manager accounts, clarify meaning, and understand the points being made before responding.",
        "quote": "Giving full attention to what other people are saying, taking time to understand the points being made",
        "locator": "O*NET OnLine 13-1071.00 › Skills › Active Listening",
    },
    {
        "unit_id": "competency-social-perceptiveness",
        "statement": "Notice and interpret others' reactions during employee support and manager consultation.",
        "quote": "Being aware of others' reactions and understanding why they react as they do.",
        "locator": "O*NET OnLine 13-1071.00 › Transferable Skills › Social Perceptiveness",
    },
    {
        "unit_id": "competency-judgment",
        "statement": "Consider the relative costs and benefits of potential actions when selecting a proportionate next step.",
        "quote": "Considering the relative costs and benefits of potential actions to choose the most appropriate one.",
        "locator": "O*NET OnLine 13-1071.00 › Transferable Skills › Judgment and Decision Making",
    },
    {
        "unit_id": "competency-administrative-records",
        "statement": "Use administrative procedures and information systems to maintain files and records accurately.",
        "quote": "Knowledge of administrative and office procedures and systems such as word processing, managing files and records",
        "locator": "O*NET OnLine 13-1071.00 › Knowledge › Administrative",
    },
    {
        "unit_id": "competency-instructing",
        "statement": "Explain processes and teach others how to carry them out; no formal instructional certification is inferred.",
        "quote": "Teaching others how to do something.",
        "locator": "O*NET OnLine 13-1071.00 › Transferable Skills › Instructing",
    },
    {
        "unit_id": "competency-written-communication",
        "statement": "Communicate information and ideas in writing so intended readers can understand them.",
        "quote": "The ability to communicate information and ideas in writing so others will understand.",
        "locator": "O*NET OnLine 13-1071.00 › Abilities › Written Expression",
    },
    {
        "unit_id": "competency-critical-thinking",
        "statement": "Use logic and reasoning to examine the strengths and weaknesses of alternative solutions or conclusions.",
        "quote": "Using logic and reasoning to identify the strengths and weaknesses of alternative solutions, conclusions, or approaches to problems.",
        "locator": "O*NET OnLine 13-1071.00 › Skills › Critical Thinking",
    },
)

CONTEXT_ADAPTATIONS: tuple[dict[str, Any], ...] = (
    {
        "unit_id": "task-context-scaled-organization-design",
        "statement": "In some scaled HR-business-partner roles, job ads add organization design, workforce planning, and people-strategy work; treat these as context adaptations, not universal HR Generalist duties.",
        "onet_quote": "Confer with management to develop or implement personnel policies or procedures.",
        "onet_locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "posting_ids": ["p-coinbase-7997879", "p-stripe-7466921", "p-stripe-7704660"],
        "posting_quote_terms": ["org design", "org design", "org design"],
        "context_label": "context_addition",
        "competency_unit_id": "competency-hr-knowledge",
        "link_justification": "Management policy work directionally requires personnel and HR knowledge; the organization-design extension remains context-specific.",
        "uncertainty": "medium",
    },
    {
        "unit_id": "task-context-coordinator-operations",
        "statement": "One coordinator-level People Solutions posting adds service-center triage, onboarding/offboarding administration, employee-data maintenance, and document audits; this is an entry-level operational context addition, not a seniority baseline.",
        "onet_quote": "Prepare or maintain employment records related to events, such as hiring, termination, leaves, transfers, or promotions, using human resources management system software.",
        "onet_locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "posting_ids": ["p-datadog-7728298"],
        "posting_quote_terms": ["own the administrative execution of new hire onboarding and offboarding"],
        "context_label": "context_addition",
        "competency_unit_id": "competency-administrative-records",
        "link_justification": "Employee-data administration is directionally supported by the O*NET records task and administrative-systems knowledge anchor; the coordinator context is not universal.",
        "uncertainty": "high",
    },
    {
        "unit_id": "task-context-defense-access",
        "statement": "A defense-sector posting requires U.S.-person status for access to export-controlled information; this is employer- and work-context-specific, not a general HR qualification.",
        "onet_quote": "Interpret and explain human resources policies, procedures, laws, standards, or regulations.",
        "onet_locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "posting_ids": ["p-andurilindustries-4835624007", "p-andurilindustries-4836444007", "p-andurilindustries-4855853007"],
        "posting_quote_terms": ["u.s. person status is required", "u.s. person status is required", "must be a u.s. person"],
        "context_label": "context_addition",
        "competency_unit_id": "competency-law-policy",
        "link_justification": "The O*NET policy task and law-and-government knowledge anchor support the HR-policy portion; U.S.-person status remains an employer-specific posting context, not a general competency requirement.",
        "uncertainty": "high",
    },
    {
        "unit_id": "task-context-benefit-administration",
        "statement": "O*NET lists employee benefit-plan administration as a supplemental task; retain it as role-specific context unless local job analysis confirms it is in scope.",
        "onet_quote": "Administer employee benefit plans.",
        "onet_locator": "O*NET OnLine 13-1071.00 › Tasks › Supplemental",
        "posting_ids": [],
        "posting_quote_terms": [],
        "context_label": "context_addition",
        "competency_unit_id": "competency-hr-knowledge",
        "link_justification": "O*NET classifies benefit-plan administration as supplemental; general HR knowledge is a desk-research link, with local role scope unresolved.",
        "uncertainty": "high",
    },
)

# Exact phrase screens are a reproducible descriptive coverage proxy. A hit is not
# a semantic adjudication, absence claim, job-prevalence estimate, or labor-market
# demand estimate.
COVERAGE_RULES: dict[str, tuple[str, ...]] = {
    "task-policy-guidance": ("hr policy guidance", "hr policies", "company policies"),
    "task-employee-relations": ("employee relations", "employee issues", "employee concerns"),
    "task-employee-records": ("employee records", "employee changes", "employment records", "employee lifecycle"),
    "task-manager-guidance": ("coach", "counsel", "advise", "performance management"),
    "task-new-hire-orientation": ("onboarding", "new employee orientations", "new hire onboarding"),
    "task-people-data-reporting": ("people metrics", "people data", "people insights", "employment-related data"),
    "task-context-scaled-organization-design": ("org design", "workforce planning", "people strategies"),
    "task-context-coordinator-operations": ("new hire onboarding", "offboarding", "employee life cycle"),
    "task-context-defense-access": ("u.s. person status is required", "must be a u.s. person"),
}


def _git_revision(root: Path = REPO_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise StructuredAnalysisError("a git candidate revision is required") from exc
    revision = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise StructuredAnalysisError("git returned an invalid candidate revision")
    return revision


def _source_citation(corpus: FrozenCorpus, source_id: str, locator: str, quote: str, **extra: Any) -> dict[str, Any]:
    source_text = corpus.source_texts.get(source_id)
    if source_text is None:
        raise StructuredAnalysisError(f"unknown evidence source id: {source_id}")
    if not quote or quote not in source_text:
        raise StructuredAnalysisError(f"quoted span does not resolve exactly in {source_id} ({locator})")
    return _citation(source_id, locator, quote, **extra)


def _find_context_quote(text: str, phrase: str) -> str:
    index = text.find(phrase)
    if index < 0:
        raise StructuredAnalysisError(f"configured context phrase is absent from posting extract: {phrase}")
    start = max(text.rfind(".", 0, index), text.rfind("\n", 0, index)) + 1
    stops = [position for marker in (".", "\n") if (position := text.find(marker, index + len(phrase))) >= 0]
    end = min(stops) + 1 if stops else len(text)
    excerpt = text[start:end].strip()
    if len(excerpt) > 260:
        excerpt = text[index : index + len(phrase)]
    if phrase not in excerpt:
        raise StructuredAnalysisError(f"cannot resolve configured context phrase: {phrase}")
    return excerpt


def _context_records(corpus: FrozenCorpus) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    competencies = {item["unit_id"]: item for item in COMPETENCIES}
    for item in CONTEXT_ADAPTATIONS:
        evidence = [_source_citation(
            corpus,
            "onet_hr_specialist",
            item["onet_locator"],
            item["onet_quote"],
            evidence_role="occupational_backbone_anchor",
        )]
        for source_id, phrase in zip(item["posting_ids"], item["posting_quote_terms"], strict=True):
            posting = corpus.sources[source_id]
            evidence.append(_source_citation(
                corpus,
                source_id,
                f"Employer posting {posting['title']} › responsibilities (phrase screen)",
                _find_context_quote(corpus.source_texts[source_id], phrase),
                evidence_role="labeled_context_addition",
                context_label=item["context_label"],
            ))
        competency = competencies[item["competency_unit_id"]]
        link = {
            "competency_unit_id": item["competency_unit_id"],
            "direction": "task-to-required-competency",
            "justification": item["link_justification"],
            "justification_provenance": "desk-research inference; this context addition is not a measured or validated task requirement",
            "evidence": _link_evidence(
                corpus,
                {"locator": item["onet_locator"], "quote": item["onet_quote"]},
                competency,
            ),
        }
        records.append({
            "unit_id": item["unit_id"],
            "statement": item["statement"],
            "kind": "task",
            "classification": item["context_label"],
            "evidence": evidence,
            "uncertainty": {"level": item["uncertainty"], "notes": "Employer-specific illustration; not a universal role requirement."},
            "method_fields": {
                "method_fields_marker": "structured-job-analysis.v1",
                "onet_anchor_ids": ["onet_hr_specialist"],
                "onet_anchor_locators": [item["onet_locator"]],
                "task_competency_links": [link],
                "proficiency": {
                    "source": "desk-research",
                    "statement": "No numeric proficiency assigned; context observations do not establish a validated level.",
                },
                "dacum_informed": True,
                "practitioner_validated": False,
                "context_adaptation": True,
            },
        })
    return tuple(records)


def _link_evidence(corpus: FrozenCorpus, task: dict[str, Any], competency: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _source_citation(
            corpus,
            "onet_hr_specialist",
            task["locator"],
            task["quote"],
            evidence_role="task_anchor",
        ),
        _source_citation(
            corpus,
            "onet_hr_specialist",
            competency["locator"],
            competency["quote"],
            evidence_role="competency_anchor",
        ),
        _source_citation(
            corpus,
            "opm_job_analysis",
            "OPM Job Analysis › definition of job analysis",
            "the competencies required to perform those tasks, and the connection between the tasks and competencies.",
            evidence_role="method_anchor",
        ),
    ]


def _build_work_units(corpus: FrozenCorpus) -> list[dict[str, Any]]:
    competencies_by_id: dict[str, dict[str, Any]] = {}
    for competency in COMPETENCIES:
        competency_id = competency["unit_id"]
        competencies_by_id[competency_id] = competency

    units: list[dict[str, Any]] = []
    for task in TASKS:
        task_quote = _source_citation(
            corpus,
            "onet_hr_specialist",
            task["locator"],
            task["quote"],
            evidence_role="onet_task_anchor",
        )
        links = []
        for competency_id in task["competencies"]:
            competency = competencies_by_id[competency_id]
            rationale = task["links"][competency_id]
            links.append({
                "competency_unit_id": competency_id,
                "direction": "task-to-required-competency",
                "justification": rationale,
                "justification_provenance": "desk-research inference; not an O*NET task-rating link or practitioner validation",
                "evidence": _link_evidence(corpus, task, competency),
            })
        units.append({
            "unit_id": task["unit_id"],
            "kind": "task",
            "statement": task["statement"],
            "evidence": [task_quote],
            "demand": {},
            "uncertainty": {
                "level": "medium",
                "notes": "O*NET occupation-profile task adapted to an HR Generalist desk guide; not all employers assign the same task scope.",
            },
            "method_fields": {
                "method_fields_marker": "structured-job-analysis.v1",
                "onet_anchor_ids": ["onet_hr_specialist"],
                "onet_anchor_locators": [task["locator"]],
                "task_competency_links": links,
                "proficiency": {
                    "source": "desk-research",
                    "statement": "No numeric proficiency assigned; the task statement does not establish a proficiency threshold.",
                },
                "dacum_informed": True,
                "practitioner_validated": False,
                "classification": "common_core",
            },
        })

    for competency in COMPETENCIES:
        citation = _source_citation(
            corpus,
            "onet_hr_specialist",
            competency["locator"],
            competency["quote"],
            evidence_role="onet_competency_anchor",
        )
        units.append({
            "unit_id": competency["unit_id"],
            "kind": "competency",
            "statement": competency["statement"],
            "evidence": [citation],
            "demand": {},
            "uncertainty": {
                "level": "medium",
                "notes": "O*NET profile competency construct; role-specific proficiency, level, and assessment threshold were not established.",
            },
            "method_fields": {
                "method_fields_marker": "structured-job-analysis.v1",
                "onet_anchor_ids": ["onet_hr_specialist"],
                "onet_anchor_locators": [competency["locator"]],
                "task_competency_links": [],
                "proficiency": {
                    "source": "desk-research",
                    "statement": "No numeric proficiency assigned; no validated proficiency scale or role-specific task-rating records were in the admitted source set.",
                },
                "dacum_informed": True,
                "practitioner_validated": False,
                "classification": "common_core",
            },
        })
    units.extend(_context_records(corpus))
    return units


def _coverage_for_unit(corpus: FrozenCorpus, unit_id: str, phrases: tuple[str, ...]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    employers: set[str] = set()
    for posting in corpus.postings:
        source_id = posting["id"]
        text = corpus.source_texts[source_id]
        found = next((phrase for phrase in phrases if phrase in text), None)
        if found is None:
            continue
        employers.add(posting["employer"])
        matches.append({
            "source_id": source_id,
            "locator": f"Employer posting {posting['title']} › extract phrase screen",
            "quote": _find_context_quote(text, found),
            "matched_term": found,
            "evidence_role": "counts_only_phrase_screen",
        })
    return {
        "unit_id": unit_id,
        "matched_dev_postings": len(matches),
        "dev_posting_denominator": len(corpus.postings),
        "dev_employer_denominator": len({posting["employer"] for posting in corpus.postings}),
        "matched_employers": len(employers),
        "match_basis": "case-folded exact phrase hit in normalized admitted posting extract; first matching phrase per posting",
        "matches": matches,
    }


def _fill_demand(corpus: FrozenCorpus, units: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = corpus.benchmark_manifest
    dd = manifest["demand_denominators"]
    coverage_by_unit: list[dict[str, Any]] = []
    for unit in units:
        if unit["kind"] != "task" or unit["unit_id"] not in COVERAGE_RULES:
            unit["demand"] = {
                "posting_count": 0,
                "postings_denominator": 0,
                "employers_denominator": 0,
                "period": "not assessed for competency units",
                "geography": "not assessed for competency units",
                "notes": "No posting coverage count is assigned to this competency or O*NET-supplemental context item; no absence is inferred.",
                "matched_dev_posting_ids": [],
            }
            continue
        coverage = _coverage_for_unit(corpus, unit["unit_id"], COVERAGE_RULES.get(unit["unit_id"], ()))
        unit["demand"] = {
            "posting_count": coverage["matched_dev_postings"],
            "postings_denominator": coverage["dev_posting_denominator"],
            "employers_denominator": coverage["dev_employer_denominator"],
            "period": "single employer-feed snapshot retrieved 2026-09-23; not a comparable time series",
            "geography": "US postings as adjudicated in the frozen manifest; one multi-location posting also lists Toronto",
            "notes": (
                "Counts-only phrase-screen coverage among unique dev postings; exact phrase match is not semantic task adjudication, "
                "prevalence, hiring probability, actual hires, occupational importance, or proficiency."
            ),
            "matched_dev_posting_ids": [item["source_id"] for item in coverage["matches"]],
        }
        coverage_by_unit.append(coverage)

    dev_employers = len({posting["employer"] for posting in corpus.postings})
    if len(corpus.postings) != dd["admitted_dev_items"] or dd["admitted_unique_items"] != 14:
        raise StructuredAnalysisError("frozen demand denominator counts do not match dev corpus")
    return {
        "period": "One retrieval window on 2026-09-23; posting publication dates span 2025-08-22 through 2026-06-12; no trend claim.",
        "geography": "US-only after reviewer adjudication; one multi-location posting also lists Toronto.",
        "denominators": {
            "employers_scanned": len(manifest["collection"]["boards_scanned"]),
            "board_total_openings": dd["board_total_openings_sum"],
            "admitted_postings": dd["admitted_unique_items"],
            "dedup_families": manifest["deduplication"]["families"],
            "matched_by_title_filter": dd["matched_by_title_filter_sum"],
            "admitted_member_postings_before_exact_dedup": dd["admitted_member_postings"],
            "dev_unique_postings": dd["admitted_dev_items"],
            "dev_employers": dev_employers,
            "heldout_unique_postings": dd["admitted_held_out_items"],
            "min_denominator_policy": dd["min_denominator_policy"],
            "current_status_under_policy": dd["current_status_under_policy"],
        },
        "claims_allowed": False,
        "statements": [
            "Counts only: 23 employer boards were scanned (5,129 board-total openings); 43 postings matched the title filter; 15 member postings were admitted and one exact-content duplicate was collapsed, leaving 14 unique postings overall (7 dev, 7 held out).",
            "The dev slice contains 7 unique postings across 4 employers; phrase-screen counts below describe this small dev sample only.",
            "Prevalence and advertised-demand percentage claims are disallowed: the frozen policy requires employers>=5 and admitted unique postings>=20; corpus status is INSUFFICIENT (14 overall).",
            "Posting counts are not occupational importance, proficiency, hires, or hiring probability. The sample is a one-time, nonrepresentative convenience sample of employers with public Greenhouse feeds; no trend claim is supported.",
        ],
        "coverage_by_unit": coverage_by_unit,
    }


def _provenance(corpus: FrozenCorpus) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_id, source in corpus.sources.items():
        is_posting = source_id.startswith("p-")
        if is_posting:
            rows.append({
                "source_id": source_id,
                "url": source["url"],
                "retrieved_at": source["period"]["retrieved_at"],
                "sha256": source["extract"]["sha256"],
                "rights_note": corpus.benchmark_manifest["rights"]["note"],
                "attribution": source["attribution"],
                "source_kind": source["source_kind"],
                "role_class": source["role_class"],
            })
        else:
            rows.append({
                "source_id": source_id,
                "url": source["url"],
                "retrieved_at": source["retrieved_at"],
                "sha256": source["extract_sha256"],
                "rights_note": source["rights_note"],
                "attribution": source["attribution"],
                "source_kind": source["source_kind"],
            })
    return rows


def _context_evidence(corpus: FrozenCorpus, posting_id: str, phrase: str) -> dict[str, Any]:
    posting = corpus.sources[posting_id]
    return _source_citation(
        corpus,
        posting_id,
        f"Employer posting {posting['title']} › responsibilities",
        _find_context_quote(corpus.source_texts[posting_id], phrase),
        evidence_role="labeled_context_addition",
        purpose="responsibility_based_title_validation",
    )


def build_release(corpus: FrozenCorpus, candidate_revision: str, *, mode: str = "offline") -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{7,40}", candidate_revision):
        raise StructuredAnalysisError("candidate_revision must be a git SHA")
    started = time.monotonic()
    units = _build_work_units(corpus)
    demand = _fill_demand(corpus, units)
    variant_evidence = [
        _context_evidence(corpus, "p-andurilindustries-4855853007", "support managers and employees throughout the entire employment lifecycle"),
        _context_evidence(corpus, "p-datadog-7728298", "own all aspects of maintaining employee changes and data throughout the full employee life cycle"),
        _context_evidence(corpus, "p-coinbase-7997879", "translate business strategy into people and org strategy"),
    ]
    literal_people_operations_titles = [
        posting["id"] for posting in corpus.postings
        if re.search(r"\bpeople operations\b", posting["title"], re.IGNORECASE)
    ]
    role_definition_evidence = [
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Interpret and explain human resources policies, procedures, laws, standards, or regulations.", evidence_role="role_definition_support"),
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Address employee relations issues, such as harassment allegations, work complaints, or other employee concerns.", evidence_role="role_definition_support"),
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Prepare or maintain employment records related to events, such as hiring, termination, leaves, transfers, or promotions, using human resources management system software.", evidence_role="role_definition_support"),
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Analyze employment-related data and prepare required reports.", evidence_role="role_definition_support"),
    ]
    supporting_title_evidence = [
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Sample of reported job titles", "HR Generalist (Human Resources Generalist)", evidence_role="source_occupation_title"),
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › occupation title", "Human Resources Specialists", evidence_role="source_occupation_title"),
        _source_citation(corpus, "p-coinbase-7997879", "Employer posting HR Business Partner › role overview", "as an hr business partner on the people team, you'll partner with one or more functional leadership teams to translate business strategy into people and org strategy.", evidence_role="labeled_context_addition", purpose="responsibility_based_title_validation"),
        _source_citation(corpus, "p-stripe-7466921", "Employer posting People Partner, Technology › role overview", "stripe is looking for a people partner (hrbp) to drive that effort across our technology organization.", evidence_role="labeled_context_addition", purpose="responsibility_based_title_validation"),
    ]
    onet_method_quote = _source_citation(
        corpus,
        "onet_task_ratings_dictionary",
        "O*NET 31.0 task_ratings data dictionary › description",
        "This table contains ratings which describe the importance, relevance, and frequency of occupation-specific tasks and duties performed in a job.",
        evidence_role="rating-provenance-boundary",
    )
    opm_method_quote = _source_citation(
        corpus,
        "opm_job_analysis",
        "OPM Job Analysis › definition of job analysis",
        "the competencies required to perform those tasks, and the connection between the tasks and competencies.",
        evidence_role="method-anchor",
    )
    dacum_quote = _source_citation(
        corpus,
        "dacum_method",
        "DACUM International Training Center › process description",
        "a panel of expert workers and a skilled facilitator working together to precisely identify the duties and tasks performed in a job",
        evidence_role="method-boundary",
    )
    methods = {
        "approach": "Structured Job Analysis with O*NET/OPM task–competency anchoring and DACUM-informed desk research.",
        "dacum_informed": True,
        "practitioner_validated": False,
        "description": "DACUM informs the duty/task decomposition structure only. This is desk research, not a DACUM study or practitioner-validated analysis; no practitioners participated.",
        "task_competency_links": "Each directional task→competency link has a separate analyst rationale and quoted task/competency/method anchors. Links are desk-research inferences, not O*NET task-rating relationships or validated job-relatedness findings.",
        "rating_provenance": "The admitted O*NET task_ratings source is a data dictionary, not downloaded HR-specific task-rating records. No numeric proficiency or rating is inferred. O*NET profile importance values, where displayed, are not proficiency.",
        "method_evidence": [onet_method_quote, opm_method_quote, dacum_quote],
    }
    title_validation = {
        "variant": "People Operations",
        "status": (
            "responsibility-based functional variant; the exact title string is absent from the 7 dev posting titles"
            if not literal_people_operations_titles
            else "responsibility-based functional variant; exact title observed in a subset of dev postings"
        ),
        "validation": "Several HR Business Partner/People Partner descriptions include employee lifecycle, manager support, employee relations, people strategy, or employee-data responsibilities that overlap this guide's HR/people work. The observed responsibilities support this cautious functional variant; they do not establish cross-employer title equivalence or demand for the literal title.",
        "evidence": variant_evidence,
        "supporting_related_title_evidence": supporting_title_evidence,
        "literal_title_posting_ids": literal_people_operations_titles,
        "negative_title_scan": (
            "No literal People Operations title occurs in the seven admitted dev posting titles; responsibility-based functional overlap only."
            if not literal_people_operations_titles
            else "A literal People Operations title occurs in the listed dev posting ids; responsibility-based scope remains separately evaluated."
        ),
    }
    title_variants = [
        "Human Resources Generalist",
        "Human Resources Specialists (O*NET 13-1071.00 source occupation)",
        "HR Business Partner / People Partner (responsibility-based adjacent title variants)",
        "People Operations (responsibility-based functional variant; exact title not observed in the dev sample)",
    ]
    config = {
        "mode": mode,
        "model": "deterministic-structured-job-analysis.v1" if mode == "offline" else PINNED_MODEL_ID,
        "provider": "local" if mode == "offline" else "deepinfra",
        "evidence_policy": "frozen-v1-starter-plus-v2-dev-only",
        "snapshot_ids": SNAPSHOT_IDS,
        "quote_verifier": "exact-substring-v1",
        "coverage_rules": COVERAGE_RULES,
    }
    config_hash = _sha256(_json_bytes(config))
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "schema_version": "skills-vector.poc-output.v1",
        "method": "structured-job-analysis",
        "run": {
            "run_id": f"sja-a-r1-{_sha256(_json_bytes([candidate_revision, corpus.benchmark_manifest_sha256, mode]))[:16]}",
            "round": 1,
            "candidate_revision": candidate_revision,
            "corpus_snapshot_ids": list(SNAPSHOT_IDS),
            "model": config["model"],
            "provider": config["provider"],
            "config_hash": config_hash,
            "started_at": now,
            "finished_at": now,
            "resource_ledger": {
                "inference_requests": 0,
                "inference_cost_usd_estimate": 0.0,
                "retrieval_requests": 0,
                "wall_clock_minutes": round((time.monotonic() - started) / 60, 6),
            },
            "mode": mode,
            "resource_caps": {
                "max_inference_cost_usd": MAX_FINAL_SPEND_USD,
                "max_retrieval_requests": 0,
                "live_execution": "one post-freeze final execution; not used in this round-one local build",
            },
        },
        "role": {
            "canonical_title": "HR Generalist (individual contributor)",
            "title_variants": title_variants,
            "scope": {
                "geography": "United States (adjudicated US locations; one multi-location posting also lists Toronto)",
                "industries": [
                    "Cross-industry intended scope; the dev sample covers aerospace/defense, financial services/fintech, and software/technology only.",
                    "The sample is uneven and Greenhouse-skewed; sectors and employer sizes are not representative.",
                ],
                "seniority": "Individual-contributor focus. Observed ads skew toward experienced/senior HRBP work; one coordinator-level posting is labeled a context addition. No general experience threshold is inferred.",
            },
            "exclusions": [
                "HR directors and executive HR leadership are outside scope.",
                "Recruiting-only jobs and recruiting-only duties are not represented as the HR Generalist common core.",
                "People-manager-only, intern/campus, and non-US roles are excluded by the frozen admission protocol.",
            ],
            "definition": "A cross-industry HR individual contributor who interprets policy, supports employee relations and records, guides managers, helps with lifecycle programs, and reports employment-related data; local role design varies.",
            "definition_evidence": role_definition_evidence,
            "title_variant_validation": title_validation,
        },
        "guide": {"path": "guide.md", "format": "markdown", "html_path": "guide.html"},
        "work_units": units,
        "demand_layer": demand,
        "provenance": _provenance(corpus),
        "unlinked_or_ambiguous": [
            {
                "item": "Role-specific proficiency levels for each competency or task",
                "reason": "The admitted O*NET 31.0 source is only a task-rating data dictionary; no HR-specific rating records or proficiency standard are present. No levels are invented.",
            },
            {
                "item": "Whether every employer uses the literal title People Operations interchangeably with HR Generalist",
                "reason": "No literal People Operations title appears among the seven dev posting titles; only responsibility overlap in selected People Partner/HRBP descriptions is evidenced.",
            },
            {
                "item": "Union, public-sector, part-time entry-level, legacy-employer, and broad non-technology contexts",
                "reason": "These contexts are absent or sparse in the admitted dev sample and cannot be resolved from this evidence set.",
            },
        ],
        "limitations": list(corpus.benchmark_manifest["limitations"]) + [
            "O*NET 13-1071.00 is a Human Resources Specialists profile, not a complete HR Generalist/People Operations corpus; selected recruiting-heavy material was excluded from the common core.",
            "The O*NET task_ratings source is a dictionary page, not HR-specific task rating records. No numeric proficiency, task importance, job-relatedness, or validated competency threshold is claimed.",
            "Task-to-competency links and competency wording are desk-research inferences anchored to quoted O*NET/OPM text, not practitioner review or an empirical validation study.",
            "Phrase-screen coverage is a deterministic wording-hit proxy over seven dev extracts. It can miss paraphrases and can count a phrase without proving a complete duty; no prevalence claim follows.",
            "People Operations is a responsibility-based functional variant only; the literal title is absent from the seven dev titles and title interchangeability is not established.",
        ],
        "method_notes": methods,
        "negative_findings": [
            "No People Operations string appears as an exact title in the seven dev posting metadata rows; responsibility-based functional overlap only.",
            "No HR-specific task-rating data records or validated numeric proficiency scale were admitted; no numeric proficiency or rating is emitted.",
            "Recruiting-only scope is not elevated into the HR Generalist common core despite recruiting content in the broad O*NET specialist profile.",
            "Counts-only: the frozen 14-item overall corpus is below the employers>=5 AND unique postings>=20 threshold; no percentage/prevalence claim is allowed.",
            "The visible dev slice is seven unique postings across four employers; this does not establish sector representativeness or role-wide task prevalence.",
        ],
        "coverage_audit": {
            "dev_postings_scanned": len(corpus.postings),
            "dev_unique_employers": len({posting["employer"] for posting in corpus.postings}),
            "duty_area_coverage": [
                {"area": unit["unit_id"], "represented": True, "anchor_source_id": unit["method_fields"]["onet_anchor_ids"][0]}
                for unit in units if unit["kind"] == "task" and unit["method_fields"].get("classification") == "common_core"
            ],
            "context_addition_units": [
                unit["unit_id"] for unit in units
                if unit["kind"] == "task" and unit["method_fields"].get("context_adaptation") is True
            ],
            "unmapped_claim": "No one-to-one comprehensive duty mapping is claimed; phrase screen reports wording hits only and its miss cases are not treated as proof of absence.",
            "excluded_role_scan": {
                "method": "literal scan of admitted posting title metadata and the authored work-unit statements for director/executive and recruiting-only markers",
                "director_or_executive_titles_in_dev": [p["id"] for p in corpus.postings if re.search(r"director|chief people|chief human resources", p["title"], re.I)],
                "literal_people_operations_titles_in_dev": literal_people_operations_titles,
                "recruiting_only_work_units": [u["unit_id"] for u in units if re.search(r"recruiter|recruiting-only", u["statement"], re.I)],
                "note": "The broad O*NET profile contains recruiting tasks, but none is included as an HR Generalist core work unit.",
            },
        },
        "acceptance": {
            "commands_run": [{
                "command": "generated by the structured-job-analysis CLI invocation recorded in run-receipt.json",
                "exit_code": 0,
                "receipt_path": "run-receipt.json",
            }],
        },
    }
    # Validate every evidence and method citation, including extras not covered by
    # the frozen validator's basic evidence/provenance traversal.
    validate_citations(release, corpus)
    return release


def validate_citations(release: dict[str, Any], corpus: FrozenCorpus) -> None:
    known = set(corpus.sources)

    def visit(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            if {"source_id", "locator", "quote"} <= value.keys():
                source_id = value["source_id"]
                if source_id not in known:
                    raise StructuredAnalysisError(f"citation source id outside frozen corpus at {path}: {source_id}")
                if source_id.startswith("p-") and value.get("evidence_role") not in {"labeled_context_addition", "counts_only_phrase_screen"}:
                    raise StructuredAnalysisError(f"posting evidence is not separated from the occupational backbone at {path}")
                if not value["locator"].strip() or not value["quote"].strip():
                    raise StructuredAnalysisError(f"empty citation locator or quote at {path}")
                if value["quote"] not in corpus.source_texts[source_id]:
                    raise StructuredAnalysisError(f"citation quote does not resolve exactly at {path}")
            for key, item in value.items():
                visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(release)
    for unit in release["work_units"]:
        method = unit["method_fields"]
        if method.get("method_fields_marker") != "structured-job-analysis.v1":
            raise StructuredAnalysisError(f"unexpected method marker on {unit['unit_id']}")
        if method.get("practitioner_validated") is not False:
            raise StructuredAnalysisError(f"practitioner validation must remain false on {unit['unit_id']}")
        if not method.get("onet_anchor_ids"):
            raise StructuredAnalysisError(f"missing O*NET anchor on {unit['unit_id']}")
        if method.get("proficiency", {}).get("source") != "desk-research":
            raise StructuredAnalysisError(f"invalid proficiency provenance on {unit['unit_id']}")
        if method.get("proficiency", {}).get("level") is not None:
            raise StructuredAnalysisError(f"numeric proficiency is not supported on {unit['unit_id']}")
    ids = {unit["unit_id"] for unit in release["work_units"]}
    if len(ids) != len(release["work_units"]):
        raise StructuredAnalysisError("duplicate work unit ids")
    for unit in release["work_units"]:
        for link in unit["method_fields"]["task_competency_links"]:
            if link.get("direction") != "task-to-required-competency" or link.get("competency_unit_id") not in ids:
                raise StructuredAnalysisError(f"invalid or unresolved task-competency link on {unit['unit_id']}")
            if not link.get("justification") or not link.get("evidence"):
                raise StructuredAnalysisError(f"unjustified task-competency link on {unit['unit_id']}")
    if release["demand_layer"]["claims_allowed"] is not False:
        raise StructuredAnalysisError("demand claims must be blocked under the frozen INSUFFICIENT policy")


def render_markdown(release: dict[str, Any]) -> str:
    role = release["role"]
    lines = [
        "# HR Generalist / People Operations — structured job analysis",
        "",
        "**Round 1 · POC A · US individual-contributor scope**  ",
        f"Candidate `{release['run']['candidate_revision']}` · run `{release['run']['run_id']}`  ",
        f"Evidence snapshots: {', '.join(release['run']['corpus_snapshot_ids'])}",
        "",
        "> **Evidence boundary.** This is DACUM-informed desk research, not a DACUM study and not a practitioner-validated analysis. O*NET/OPM anchor the task–competency backbone; employer postings are kept in separate counts-only demand observations or clearly labeled context additions.",
        "",
        "## Role and scope",
        "",
        role["definition"],
        "",
        f"- **Geography:** {role['scope']['geography']}",
        f"- **Industries:** {' '.join(role['scope']['industries'])}",
        f"- **Seniority:** {role['scope']['seniority']}",
        "",
        "**Out of scope:**",
    ]
    lines.extend(f"- {item}" for item in role["exclusions"])
    lines.extend(["", "**Definition evidence:**"])
    for evidence in role["definition_evidence"]:
        lines.append(f"- “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
    lines.extend([
        "",
        "### Title variants: responsibility evidence, not title prevalence",
        "",
        "Accepted working labels: " + "; ".join(role["title_variants"]) + ".",
        "",
        role["title_variant_validation"]["validation"],
        f"**Exact-title negative finding:** {role['title_variant_validation']['negative_title_scan']}",
        "",
        "**Supporting title/responsibility excerpts:**",
    ])
    for evidence in role["title_variant_validation"]["supporting_related_title_evidence"]:
        lines.append(f"- “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
    for evidence in role["title_variant_validation"]["evidence"]:
        lines.append(f"- “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']} (responsibility-based People Operations variant).")
    lines.extend(["", "## Task–competency backbone", ""])
    for unit in release["work_units"]:
        if unit["method_fields"].get("classification") != "common_core":
            continue
        citation = unit["evidence"][0]
        lines.extend([
            f"### {unit['statement']}",
            "",
            f"> “{citation['quote']}” — `{citation['source_id']}`, {citation['locator']}.",
            "",
            f"**Uncertainty:** {unit['uncertainty']['level']} — {unit['uncertainty']['notes']}",
            "",
        ])
        links = unit["method_fields"]["task_competency_links"]
        if links:
            lines.append("**Directional links (desk-research inference; not validated ratings):**")
            for link in links:
                lines.append(f"- `{unit['unit_id']}` → `{link['competency_unit_id']}`: {link['justification']}")
                for ev in link["evidence"][:2]:
                    lines.append(f"  - Evidence: “{ev['quote']}” — `{ev['source_id']}`, {ev['locator']}.")
            lines.append("")
        proficiency = unit["method_fields"]["proficiency"]
        lines.append(f"**Proficiency provenance:** `{proficiency['source']}`. {proficiency['statement']}")
        lines.append("")
    lines.extend(["### Competency statements", ""])
    for unit in release["work_units"]:
        if unit["kind"] != "competency":
            continue
        ev = unit["evidence"][0]
        lines.append(f"- **{unit['unit_id']}** — {unit['statement']} “{ev['quote']}” (`{ev['source_id']}`, {ev['locator']}). No level is assigned.")
    lines.extend(["", "## Context adaptations — do not generalize", ""])
    for unit in release["work_units"]:
        if unit["method_fields"].get("context_adaptation") is not True:
            continue
        lines.append(f"### {unit['statement']}")
        lines.append("")
        for ev in unit["evidence"]:
            lines.append(f"- “{ev['quote']}” — `{ev['source_id']}`, {ev['locator']} (context addition).")
        for link in unit["method_fields"]["task_competency_links"]:
            lines.append(f"- `{unit['unit_id']}` → `{link['competency_unit_id']}`: {link['justification']}")
            for ev in link["evidence"][:2]:
                lines.append(f"  - Link evidence: “{ev['quote']}” — `{ev['source_id']}`, {ev['locator']}.")
        lines.append("")
    demand = release["demand_layer"]
    den = demand["denominators"]
    lines.extend([
        "## Advertised-posting evidence — separate, counts only",
        "",
        " | ".join(demand["statements"]),
        "",
        f"**Denominators:** {den['employers_scanned']} boards scanned; {den['board_total_openings']:,} total board openings; {den['matched_by_title_filter']} title-filter matches; {den['admitted_member_postings_before_exact_dedup']} admitted member postings collapsed to {den['admitted_postings']} unique items ({den['dev_unique_postings']} dev / {den['heldout_unique_postings']} held out); {den['dedup_families']} exact-content families.",
        "",
        "Phrase-screen counts below use only the seven public dev items (four employers). A literal phrase hit is a reproducible coverage proxy, not semantic adjudication or prevalence; a miss does not show the duty is absent. The held-out content is not included.",
        "",
        "| Work-unit phrase screen | Dev posting matches / 7 | Evidence excerpts |",
        "|---|---:|---|",
    ])
    for row in demand["coverage_by_unit"]:
        excerpts = "<br>".join(
            f"{item['source_id']}: “{item['quote']}”" for item in row["matches"]
        ) or "No configured phrase hit; absence is not inferred."
        lines.append(f"| `{row['unit_id']}` | {row['matched_dev_postings']} / {row['dev_posting_denominator']} | {excerpts} |")
    lines.extend([
        "",
        "### Coverage and limits",
        "",
        f"The local analysis represents {len(release['coverage_audit']['duty_area_coverage'])} O*NET-anchored task areas and {sum(1 for u in release['work_units'] if u['kind'] == 'competency')} competencies. This is an authored coverage selection, not a claim that it exhausts every employer's HR Generalist work.",
        release["coverage_audit"]["unmapped_claim"],
        "",
        "## Method and proficiency provenance",
        "",
        release["method_notes"]["description"],
        release["method_notes"]["task_competency_links"],
        release["method_notes"]["rating_provenance"],
        "",
        "**Method source excerpts:**",
    ])
    for evidence in release["method_notes"]["method_evidence"]:
        lines.append(f"- “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
    lines.extend(["", "## Negative findings preserved", ""])
    lines.extend(f"- {item}" for item in release["negative_findings"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in release["limitations"])
    lines.extend(["", "## Provenance", "", "The machine release contains the full source URLs, retrieval timestamps, extract hashes, attribution, and rights notes. Short excerpts above retain source ids and locators.", ""])
    for source in release["provenance"]:
        lines.append(f"- `{source['source_id']}` — [{source['attribution']}]({source['url']}); retrieved {source['retrieved_at']}; extract SHA-256 `{source['sha256']}`. {source['rights_note']}")
    lines.extend(["", "## Machine release", "", "Read `release.json` for stable unit ids, exact citations, uncertainty labels, separate posting counts, and the run/resource ledger.", ""])
    return "\n".join(lines)


def _inline_html(text: str) -> str:
    safe = html.escape(text)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', safe)
    return safe


def _markdown_body_html(markdown: str) -> str:
    lines = markdown.splitlines()
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            rendered.append(f"<h{level}>{_inline_html(heading.group(2))}</h{level}>")
            index += 1
            continue
        if line.startswith("> "):
            quote_lines = []
            while index < len(lines) and lines[index].startswith("> "):
                quote_lines.append(_inline_html(lines[index][2:]))
                index += 1
            rendered.append("<blockquote>" + " ".join(quote_lines) + "</blockquote>")
            continue
        if line.startswith("| "):
            table_rows: list[list[str]] = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip("|").split("|")]
                if not all(set(cell) <= {"-", ":", " "} for cell in cells):
                    table_rows.append(cells)
                index += 1
            if table_rows:
                header, *body = table_rows
                rendered.append("<div class=\"table-wrap\"><table><thead><tr>" + "".join(f"<th>{_inline_html(cell)}</th>" for cell in header) + "</tr></thead><tbody>")
                for row in body:
                    padded = row + [""] * max(0, len(header) - len(row))
                    rendered.append("<tr>" + "".join(f"<td>{_inline_html(cell)}</td>" for cell in padded[:len(header)]) + "</tr>")
                rendered.append("</tbody></table></div>")
            continue
        if line.startswith("- "):
            items = []
            while index < len(lines) and lines[index].startswith("- "):
                items.append("<li>" + _inline_html(lines[index][2:]) + "</li>")
                index += 1
            rendered.append("<ul>" + "".join(items) + "</ul>")
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(r"^#{1,6}\s+", lines[index]) and not lines[index].startswith(("> ", "- ", "|")):
            paragraph.append(lines[index].strip())
            index += 1
        rendered.append("<p>" + _inline_html(" ".join(paragraph)) + "</p>")
    return "\n".join(rendered)


def render_html(markdown: str, release: dict[str, Any]) -> str:
    """Render a self-contained, browser-readable guide without runtime assets."""
    title = "HR Generalist / People Operations — Structured Job Analysis"
    candidate = html.escape(release["run"]["candidate_revision"])
    run_id = html.escape(release["run"]["run_id"])
    body = _markdown_body_html(markdown)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{color-scheme:light;--ink:#0c1828;--muted:#3f4a55;--paper:#fafbfc;--mineral:#f2f5f7;--rule:#bcc8d4;--present:#1a4f8c;--uncertain:#8a5c48}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(140deg,var(--paper),var(--mineral));color:var(--ink);font:16px/1.6 \"IBM Plex Sans\",Arial,sans-serif}}
main{{max-width:1000px;margin:auto;padding:2rem 1.25rem 4rem}}header{{border-top:2px solid var(--present);border-bottom:1px solid var(--rule);padding:1.25rem 0;margin-bottom:1.5rem}}
h1,h2,h3{{font-family:\"STIX Two Text\",Georgia,serif;line-height:1.2}}h1{{font-size:clamp(2rem,4vw,3rem);margin:.2rem 0}}h2{{margin-top:2.4rem;border-bottom:1px solid var(--rule);padding-bottom:.4rem}}
.meta{{font: .75rem/1.4 \"IBM Plex Mono\",monospace;color:var(--muted);overflow-wrap:anywhere}}a{{color:var(--present)}}code{{font-family:\"IBM Plex Mono\",monospace;font-size:.88em}}blockquote,.warning{{background:#efe6e0;border:1px solid #cbb9ae;padding:.9rem;margin:1rem 0}}blockquote{{color:#163049}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{border:1px solid var(--rule);padding:.55rem;text-align:left;vertical-align:top}}th{{background:var(--mineral)}}
@media(max-width:640px){{main{{padding:1rem}}.meta{{font-size:.68rem}}}}
</style>
</head>
<body><main><header><p class=\"meta\">ROUND 1 · POC A · LOCAL EVIDENCE GUIDE</p><h1>{html.escape(title)}</h1>
<p class=\"meta\">Candidate {candidate} · run {run_id}</p></header>
<p class=\"warning\"><strong>Evidence boundary.</strong> DACUM-informed desk research only. No practitioner validation; posting counts are not prevalence or occupational importance.</p>
<article>{body}</article></main></body></html>
"""


def _reject_credential_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if re.search(r"api[_-]?key|access[_-]?token|secret|authorization|password|credential", str(key), re.IGNORECASE):
                raise StructuredAnalysisError("resource config must not contain credential fields")
            _reject_credential_fields(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_credential_fields(item)


def _reject_secret_echo(payload: dict[str, Any], api_key: str) -> None:
    if api_key and api_key in json.dumps(payload, ensure_ascii=False):
        raise StructuredAnalysisError("provider response contained credential material; response discarded")


def validate_resource_config(config: dict[str, Any]) -> None:
    _reject_credential_fields(config)
    if config.get("model") != PINNED_MODEL_ID or config.get("provider") != "deepinfra":
        raise StructuredAnalysisError("live final mode is pinned to DeepInfra deepseek-ai/DeepSeek-V4.1-Flash")
    sampling = config.get("sampling")
    caps = config.get("caps")
    if not isinstance(sampling, dict) or not isinstance(caps, dict):
        raise StructuredAnalysisError("live resource config must include sampling and caps objects")
    try:
        temperature = float(sampling["temperature"])
        top_p = float(sampling["top_p"])
        max_tokens = sampling["max_tokens"]
        max_requests = caps["max_inference_requests"]
        max_cost = float(caps["max_inference_cost_usd"])
        retrieval_cap = caps["max_retrieval_requests"]
        wall_cap = float(caps["max_wall_minutes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StructuredAnalysisError("live resource config is missing numeric sampling or cap fields") from exc
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise StructuredAnalysisError("sampling.temperature must be between 0 and 2")
    if not math.isfinite(top_p) or not 0 < top_p <= 1:
        raise StructuredAnalysisError("sampling.top_p must be in (0, 1]")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 8192:
        raise StructuredAnalysisError("sampling.max_tokens must be an integer in [1, 8192]")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise StructuredAnalysisError("caps.max_inference_requests must be a positive integer")
    if not math.isfinite(max_cost) or not 0 < max_cost <= MAX_FINAL_SPEND_USD:
        raise StructuredAnalysisError("caps.max_inference_cost_usd must be positive and no greater than USD 0.50")
    if isinstance(retrieval_cap, bool) or retrieval_cap != 0:
        raise StructuredAnalysisError("live final mode is frozen-corpus-only; max_retrieval_requests must be zero")
    if not math.isfinite(wall_cap) or wall_cap <= 0:
        raise StructuredAnalysisError("caps.max_wall_minutes must be positive")


def _request_body(release: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    evidence = {
        "run_id": release["run"]["run_id"],
        "method": release["method"],
        "work_units": [
            {
                "unit_id": unit["unit_id"],
                "kind": unit["kind"],
                "statement": unit["statement"],
                "evidence": unit["evidence"],
                "task_competency_links": unit["method_fields"]["task_competency_links"],
                "uncertainty": unit["uncertainty"],
            }
            for unit in release["work_units"]
        ],
        "limits": [
            "Only inspect the supplied citations; no web retrieval or extra source allowed.",
            "Do not propose practitioner validation, proficiency numbers, prevalence, or newly sourced work claims.",
            "Return review suggestions only; the local pipeline will not incorporate them into the role guide or work-unit claims.",
        ],
    }
    sampling = config["sampling"]
    return {
        "model": PINNED_MODEL_ID,
        "messages": [
            {"role": "system", "content": LIVE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False, sort_keys=True)},
        ],
        "temperature": sampling["temperature"],
        "top_p": sampling["top_p"],
        "max_tokens": sampling["max_tokens"],
        "response_format": {"type": "json_object"},
    }


def estimate_request_cost(body: dict[str, Any]) -> float:
    """Conservative byte-count input estimate plus the configured output-token ceiling."""
    input_bytes = len(_json_bytes(body))
    # Two input tokens per UTF-8 byte is intentionally conservative for this short
    # one-request prompt; the budget cap still includes every planned request.
    input_tokens = input_bytes * 2
    price = PINNED_MODELS["extract"]
    return estimate_cost_usd(price, input_tokens=input_tokens, output_tokens=body["max_tokens"])


def preflight_request_batch(requests: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[float], float]:
    """Preflight all planned requests before any provider call; retries are forbidden."""
    validate_resource_config(config)
    caps = config["caps"]
    if not requests:
        raise StructuredAnalysisError("live final mode has no planned provider request")
    if len(requests) > caps["max_inference_requests"]:
        raise StructuredAnalysisError("planned request batch exceeds the approved inference-request cap")
    # Estimate the entire planned batch before sending its first request. The current
    # final pipeline uses one request; this summation also safely handles a reviewed
    # multi-request plan without making any provider call until all costs fit.
    costs = [estimate_request_cost(request) for request in requests]
    total = sum(costs)
    if total > min(float(caps["max_inference_cost_usd"]), MAX_FINAL_SPEND_USD):
        raise StructuredAnalysisError(
            f"whole-run preflight US${total:.6f} exceeds the approved cap; no provider request sent"
        )
    return costs, total


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _write_release_files(output_dir: Path, release: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "release.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = render_markdown(release)
    (output_dir / "guide.md").write_text(markdown, encoding="utf-8")
    (output_dir / "guide.html").write_text(render_html(markdown, release), encoding="utf-8")
    manifest = {
        "model": release["run"]["model"],
        "provider": release["run"]["provider"],
        "sampling": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 0} if release["run"]["mode"] == "offline" else release["run"].get("sampling", {}),
        "caps": release["run"]["resource_caps"],
        "corpus_snapshot_ids": release["run"]["corpus_snapshot_ids"],
        "evidence_policy": "frozen-corpus-only; no retrieval",
    }
    _write_json(output_dir / "run-manifest.json", manifest)


def _verify_freeze(revision: str, frozen_sha: str | None) -> None:
    if not frozen_sha or not re.fullmatch(r"[0-9a-f]{40}", frozen_sha):
        raise StructuredAnalysisError("live final mode requires --freeze-sha with a full 40-character candidate SHA")
    if frozen_sha != revision:
        raise StructuredAnalysisError("--freeze-sha must exactly match current HEAD before the one final run")


def _acquire_final_lock(lock_path: Path, metadata: dict[str, Any]) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise StructuredAnalysisError("the one final live execution was already started; retries are disabled") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sanitized_failure(exc: BaseException, api_key: str) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"provider returned HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "provider network error (detail intentionally redacted)"
    text = f"{type(exc).__name__}: {exc}".replace(api_key, "[REDACTED]")
    return text[:300]


def _call_provider_once(body: dict[str, Any], api_key: str, opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        LIVE_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    open_request = opener or urllib.request.urlopen
    # No loop, retry, model escalation, or fixture fallback.
    with open_request(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise StructuredAnalysisError("provider response was not a JSON object")
    return payload


def validate_live_review(payload: dict[str, Any], release: dict[str, Any], corpus: FrozenCorpus) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices:
        raise StructuredAnalysisError("provider response did not contain a completion")
    content = choices[0].get("message", {}).get("content")
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StructuredAnalysisError("single provider response was not valid review JSON") from exc
    allowed_units = {unit["unit_id"] for unit in release["work_units"]}
    reviews = parsed.get("reviews") if isinstance(parsed, dict) else None
    if not isinstance(reviews, list) or not reviews:
        raise StructuredAnalysisError("single provider response omitted a non-empty reviews array")
    normalized_reviews: list[dict[str, Any]] = []
    for index, review in enumerate(reviews):
        if not isinstance(review, dict) or review.get("unit_id") not in allowed_units:
            raise StructuredAnalysisError(f"unresolved model review unit at index {index}")
        if review.get("stance") not in {"supported", "challenge", "unclear"}:
            raise StructuredAnalysisError(f"invalid model review stance at index {index}")
        if not isinstance(review.get("reason"), str) or not review["reason"].strip():
            raise StructuredAnalysisError(f"empty model review rationale at index {index}")
        evidence = review.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise StructuredAnalysisError(f"missing model review citations at index {index}")
        citations: list[dict[str, str]] = []
        for citation in evidence:
            if not isinstance(citation, dict):
                raise StructuredAnalysisError(f"malformed model review citation at index {index}")
            source_id = citation.get("source_id")
            locator = citation.get("locator")
            quote = citation.get("quote")
            if not isinstance(source_id, str) or source_id not in corpus.source_texts or not isinstance(locator, str) or not locator.strip() or not isinstance(quote, str) or quote not in corpus.source_texts[source_id]:
                raise StructuredAnalysisError(f"model review citation does not resolve at index {index}")
            citations.append({"source_id": source_id, "locator": locator, "quote": quote})
        normalized_reviews.append({
            "unit_id": review["unit_id"],
            "stance": review["stance"],
            "reason": review["reason"],
            "evidence": citations,
        })
    usage = payload.get("usage") or {}
    return {
        "status": "untrusted_model_review_not_validation",
        "model": PINNED_MODEL_ID,
        "provider": "deepinfra",
        "review_count": len(normalized_reviews),
        "reviews": normalized_reviews,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
        "notice": "Suggestions are not practitioner validation and are excluded from the occupational backbone and guide.",
    }


def _update_lock(lock_path: Path, update: dict[str, Any]) -> None:
    try:
        current = _read_json(lock_path)
    except StructuredAnalysisError:
        current = {}
    current.update(update)
    _write_json(lock_path, current)


def run_pipeline(
    *,
    benchmark_dir: Path = DEFAULT_BENCHMARK_DIR,
    base_corpus_dir: Path = DEFAULT_BASE_CORPUS_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    mode: str = "offline",
    resource_config_path: Path | None = None,
    freeze_sha: str | None = None,
    confirm_final_run: bool = False,
    candidate_revision: str | None = None,
    command: str | None = None,
    api_key: str | None = None,
    _live_lock_path: Path | None = None,
    _provider_call: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build local artifacts; live-final adds exactly one guarded model review request."""
    if mode not in {"offline", "live-final"}:
        raise StructuredAnalysisError("mode must be offline or live-final")
    revision = candidate_revision or _git_revision()
    if mode == "live-final":
        _verify_freeze(revision, freeze_sha)
        if not confirm_final_run:
            raise StructuredAnalysisError("live final mode requires the explicit --confirm-final-run flag")
        if resource_config_path is None:
            raise StructuredAnalysisError("live final mode requires a parent-approved --resource-config")
        if api_key is None:
            api_key = os.environ.get("DEEPINFRA_API_KEY", "").strip()
        if not api_key:
            raise StructuredAnalysisError("DEEPINFRA_API_KEY is required; credential value is never printed or recorded")
        resource_config = _read_json(resource_config_path)
        validate_resource_config(resource_config)
        if (_live_lock_path or DEFAULT_LIVE_LOCK).exists():
            raise StructuredAnalysisError("the one final live execution was already started; no second execution is permitted")
    else:
        if resource_config_path is not None or freeze_sha is not None or confirm_final_run:
            raise StructuredAnalysisError("live-only flags cannot be used in offline mode")
        resource_config = None

    corpus = load_frozen_corpus(benchmark_dir, base_corpus_dir)
    release = build_release(corpus, revision, mode=mode)
    effective_command = command or f"python -m skills_vector structured-job-analysis --mode {mode}"
    release["acceptance"]["commands_run"][0]["command"] = effective_command
    output_dir = output_dir.resolve()
    receipt: dict[str, Any] = {
        "schema_version": "skills-vector.structured-analysis-run-receipt.v1",
        "run_id": release["run"]["run_id"],
        "candidate_revision": revision,
        "mode": mode,
        "corpus_snapshot_ids": list(SNAPSHOT_IDS),
        "manifest_sha256": corpus.benchmark_manifest_sha256,
        "base_manifest_sha256": corpus.base_manifest_sha256,
        "provider_calls": 0,
        "inference_cost_usd_estimate": 0.0,
        "retrieval_requests": 0,
        "commands_run": [{"command": effective_command, "exit_code": 0, "receipt_path": "run-receipt.json"}],
        "status": "passed_local_build" if mode == "offline" else "preflight_pending",
        "credential_handling": "DEEPINFRA_API_KEY read from environment only in live-final mode; value excluded from prompt, output, logs, and receipt.",
    }

    if mode == "offline":
        _write_release_files(output_dir, release)
        _write_json(output_dir / "run-receipt.json", receipt)
        return receipt

    assert resource_config is not None and api_key is not None
    body = _request_body(release, resource_config)
    try:
        request_costs, total_estimate = preflight_request_batch([body], resource_config)
    except StructuredAnalysisError as exc:
        receipt["status"] = "blocked_by_preflight_no_provider_call"
        receipt["failure"] = str(exc)
        receipt["commands_run"][0]["exit_code"] = 2
        _write_json(output_dir / "run-receipt.json", receipt)
        raise
    receipt["preflight"] = {
        "planned_request_count": 1,
        "per_request_estimates_usd": request_costs,
        "whole_run_estimate_usd": total_estimate,
        "hard_ceiling_usd": MAX_FINAL_SPEND_USD,
        "model": PINNED_MODEL_ID,
        "automatic_retry": False,
        "escalation_or_fallback": False,
    }
    release["run"]["model"] = PINNED_MODEL_ID
    release["run"]["provider"] = "deepinfra"
    release["run"]["sampling"] = resource_config["sampling"]
    release["run"]["config_hash"] = _sha256(_json_bytes(resource_config))
    release["run"]["resource_caps"] = resource_config["caps"]
    release["run"]["resource_ledger"]["inference_requests"] = 1
    release["run"]["resource_ledger"]["inference_cost_usd_estimate"] = total_estimate
    receipt["config_hash"] = release["run"]["config_hash"]
    receipt["planned_request_count"] = 1
    receipt["inference_cost_usd_estimate"] = total_estimate
    receipt["provider"] = "deepinfra"
    receipt["model"] = PINNED_MODEL_ID
    receipt["status"] = "started_one_shot_no_retry"

    lock_path = _live_lock_path or DEFAULT_LIVE_LOCK
    lock_metadata = {
        "status": "started",
        "candidate_revision": revision,
        "run_id": release["run"]["run_id"],
        "corpus_manifest_sha256": corpus.benchmark_manifest_sha256,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider_request_count": 1,
        "estimated_cost_usd": total_estimate,
        "provider": "deepinfra",
        "model": PINNED_MODEL_ID,
        "credential_value_recorded": False,
    }
    try:
        _acquire_final_lock(lock_path, lock_metadata)
    except StructuredAnalysisError as exc:
        receipt["status"] = "blocked_one_shot_already_consumed"
        receipt["failure"] = str(exc)
        receipt["commands_run"][0]["exit_code"] = 2
        _write_json(output_dir / "final-run-blocked-attempt.json", receipt)
        raise

    # The lock is durable before any provider action. A crash or provider error
    # consumes the sole attempt rather than opening a retry path.
    _write_release_files(output_dir, release)
    _write_json(output_dir / "run-receipt.json", receipt)
    try:
        provider_payload = (_provider_call or _call_provider_once)(body, api_key)
        _reject_secret_echo(provider_payload, api_key)
        review = validate_live_review(provider_payload, release, corpus)
        usage = review["usage"]
        actual = estimate_cost_usd(
            PINNED_MODELS["extract"],
            input_tokens=usage["input_tokens"] or len(_json_bytes(body)) * 2,
            output_tokens=usage["output_tokens"] or body["max_tokens"],
        )
        if actual > min(float(resource_config["caps"]["max_inference_cost_usd"]), MAX_FINAL_SPEND_USD):
            raise StructuredAnalysisError("provider-reported usage exceeded the preflight spend cap")
        receipt["provider_calls"] = 1
        receipt["inference_cost_usd_estimate"] = actual
        receipt["provider_usage"] = usage
        receipt["provider_review_status"] = review["status"]
        release["run"]["resource_ledger"]["inference_cost_usd_estimate"] = actual
        release["run"]["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        release["run"]["resource_ledger"]["wall_clock_minutes"] = round(
            max(0.0, (datetime.now(UTC) - datetime.fromisoformat(release["run"]["started_at"].replace("Z", "+00:00"))).total_seconds()) / 60,
            6,
        )
        _write_json(output_dir / "live-model-review-untrusted.json", review)
        receipt["status"] = "passed_one_live_review"
        _update_lock(lock_path, {"status": "completed", "finished_at": datetime.now(UTC).isoformat(timespec="seconds")})
    except BaseException as exc:
        receipt["provider_calls"] = 1
        receipt["status"] = "failed_after_one_shot_consumed"
        receipt["failure"] = _sanitized_failure(exc, api_key)
        receipt["commands_run"][0]["exit_code"] = 2
        release["acceptance"]["commands_run"][0]["exit_code"] = 2
        release["run"]["resource_ledger"]["inference_cost_usd_estimate"] = total_estimate
        release["run"]["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        _write_release_files(output_dir, release)
        _update_lock(lock_path, {
            "status": "failed_consumed_no_retry",
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "failure": receipt["failure"],
        })
        _write_json(output_dir / "run-receipt.json", receipt)
        raise StructuredAnalysisError(receipt["failure"]) from None

    _write_release_files(output_dir, release)
    receipt["commands_run"] = [{"command": effective_command, "exit_code": 0, "receipt_path": "run-receipt.json"}]
    _write_json(output_dir / "run-receipt.json", receipt)
    return receipt

