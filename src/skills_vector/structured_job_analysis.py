"""Round-three HR Generalist Structured Job Analysis over the frozen public dev corpus.

The occupational backbone is an authored, O*NET/OPM-anchored desk analysis. Posting
text is used only for counts-only demand observations and explicitly labeled context
adaptations. This module never retrieves sources and never treats model output as
validation.
"""

from __future__ import annotations

import base64
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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs/structured-job-analysis-round3"
DEFAULT_LIVE_LOCK = REPO_ROOT / ".poc-env/state/structured-job-analysis-final-live.json"
CORRECTIVE_R2_LIVE_LOCK = REPO_ROOT / ".poc-env/state/structured-job-analysis-corrective-round-2-live.json"
APPROVED_RESOURCE_CONFIG_PATH = Path(
    "/home/logani/oprun-evidence/skills-vector-poc-prep-74b159c/comparison-resource-config-v2.json"
)
APPROVED_RESOURCE_CONFIG_SHA256 = "684c37fc2ba5b94fcc65bb6f00274349cfe05832a066569594921ab891230847"
MATCHED_EVIDENCE_POLICY = "frozen-corpus-only"
MAX_PLANNED_PROVIDER_REQUESTS = 1
MAX_SERIALIZED_REQUEST_BYTES = 48 * 1024
MAX_INPUT_TOKENS_UPPER_BOUND = MAX_SERIALIZED_REQUEST_BYTES * 2
MAX_OUTPUT_TOKENS = 2048
MAX_PROVIDER_RESPONSE_BYTES = 128 * 1024
MATCHED_RESOURCE_CONFIG = {
    "model": "deepseek-ai/DeepSeek-V4.1-Flash",
    "provider": "deepinfra",
    "sampling": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048, "reasoning_effort": "none"},
    "caps": {
        "max_inference_requests": 8,
        "max_inference_cost_usd": 0.5,
        "max_retrieval_requests": 0,
        "max_wall_minutes": 30,
    },
}

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
    "Review only the supplied work units and their evidence_ref_ids in the evidence_catalog; "
    "cite the exact catalog source_id, locator, and quote. Do not add sources, duties, "
    "competencies, proficiency levels, or practitioner claims. Return one JSON object: "
    "{\"reviews\":[{\"unit_id\":string,\"stance\":\"supported\"|\"challenge\"|\"unclear\","
    "\"reason\":string,\"evidence\":[{\"source_id\":string,\"locator\":string,"
    "\"quote\":string}]}]}. A review is a model suggestion, not validation."
)


class StructuredAnalysisError(RuntimeError):
    """A fail-closed corpus, citation, resource, or run-once error."""


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Bounded response capture kept separate from semantic review validation."""

    payload: Any
    raw_body: bytes
    body_bytes_observed: int
    body_sha256: str
    truncated: bool
    capture_source: str
    body_digest_scope: str
    parse_error: str | None = None
    http_status: int | None = None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_approved_resource_config(path: Path = APPROVED_RESOURCE_CONFIG_PATH) -> tuple[dict[str, Any], str]:
    raw = _verify_readonly_file(path, APPROVED_RESOURCE_CONFIG_SHA256)
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredAnalysisError("approved matched resource config is not valid JSON") from exc
    if not isinstance(config, dict) or config != MATCHED_RESOURCE_CONFIG:
        raise StructuredAnalysisError("resource config does not equal the parent-approved matched configuration")
    validate_resource_config(config)
    return config, _sha256(raw)


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
    {
        "unit_id": "task-eeo-policy-compliance",
        "statement": "Maintain working knowledge of applicable equal-employment and affirmative-action rules relevant to assigned HR work; this is not legal advice.",
        "quote": "Maintain current knowledge of Equal Employment Opportunity (EEO) and affirmative action guidelines and laws, such as the Americans with Disabilities Act (ADA).",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-hr-knowledge", "competency-law-policy", "competency-written-communication"],
        "links": {
            "competency-hr-knowledge": "The task expressly names EEO and affirmative-action rules; HR knowledge supports locating the relevant assigned guidance.",
            "competency-law-policy": "The task explicitly names guidelines and laws; the relationship is a direct desk-research link, not a legal qualification threshold.",
            "competency-written-communication": "Communicating applicable guidance is a cautious task-design link; the source does not set a writing standard.",
        },
    },
    {
        "unit_id": "task-hr-document-maintenance",
        "statement": "Maintain and update assigned HR documents, such as handbooks, organization charts, and performance forms, using approved local controls.",
        "quote": "Maintain and update human resources documents, such as organizational charts, employee handbooks or directories, or performance evaluation forms.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-administrative-records", "competency-written-communication", "competency-hr-knowledge"],
        "links": {
            "competency-administrative-records": "The task names maintaining and updating HR documents; file and record procedures are a direct directional link.",
            "competency-written-communication": "The task names written HR documents; clear writing is relevant, but no local standard or level is established.",
            "competency-hr-knowledge": "The documents concern HR policies and processes; this is a broad desk-research link, not an empirical task rating.",
        },
    },
    {
        "unit_id": "task-employee-exit-process",
        "statement": "When assigned, support employee exits through an exit interview and required termination records; local ownership varies.",
        "quote": "Conduct exit interviews and ensure that necessary employment termination paperwork is completed.",
        "locator": "O*NET OnLine 13-1071.00 › Tasks › Core",
        "competencies": ["competency-administrative-records", "competency-active-listening"],
        "links": {
            "competency-administrative-records": "The task expressly names termination paperwork; accurate records handling is a direct directional link.",
            "competency-active-listening": "An exit interview involves hearing an employee account; this cautious link is not a measured proficiency requirement.",
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
    "task-eeo-policy-compliance": ("eeo", "equal employment opportunity", "affirmative action", "employment law"),
    "task-hr-document-maintenance": ("employee handbook", "internal documentation", "employee records", "hr policies"),
    "task-employee-exit-process": ("offboarding", "employee exits", "termination paperwork", "employee lifecycle"),
    "task-manager-guidance": ("coach", "counsel", "advise", "performance management"),
    "task-new-hire-orientation": ("onboarding", "new employee orientations", "new hire onboarding"),
    "task-people-data-reporting": ("people metrics", "people data", "people insights", "employment-related data"),
    "task-context-scaled-organization-design": ("org design", "workforce planning", "people strategies"),
    "task-context-coordinator-operations": ("new hire onboarding", "offboarding", "employee life cycle"),
    "task-context-defense-access": ("u.s. person status is required", "must be a u.s. person"),
}

READER_ACTIONS_BY_UNIT: dict[str, dict[str, str]] = {
    "task-policy-guidance": {
        "ic_hr_practitioner": "Check the local policy source and escalation path before advising an employee or manager.",
        "job_seeker": "Prepare an example of explaining a policy and recognizing when to escalate; do not present this as a proficiency threshold.",
        "hiring_manager": "Define which policies this role interprets and its escalation boundary in a local job analysis before using the duty in hiring.",
    },
    "task-employee-relations": {
        "ic_hr_practitioner": "Check local intake, documentation, confidentiality, and referral procedures before handling a concern.",
        "job_seeker": "Reflect on a relevant listening or documentation example without sharing confidential case details or implying a required level.",
        "hiring_manager": "Confirm case-handling boundaries, support, and referral routes with appropriate internal reviewers before using this duty in selection.",
    },
    "task-employee-records": {
        "ic_hr_practitioner": "Confirm approved systems, access, correction, and retention procedures before changing employee records.",
        "job_seeker": "Prepare a non-confidential example of careful records or HR-system work; no particular tool is established here.",
        "hiring_manager": "Specify the records and systems actually in scope locally, rather than assuming a particular tool or proficiency level.",
    },
    "task-manager-guidance": {
        "ic_hr_practitioner": "Check current internal guidance and documentation practices before advising a manager on a performance process.",
        "job_seeker": "Choose an example of explaining a process or supporting a manager, and separate your own role from decision authority.",
        "hiring_manager": "Clarify the role's advisory versus decision-making responsibilities before treating this as a selection criterion.",
    },
    "task-new-hire-orientation": {
        "ic_hr_practitioner": "Compare orientation content with the current onboarding plan and identify who owns updates or follow-up.",
        "job_seeker": "Prepare an example of helping someone understand a process; this source does not set a training credential or threshold.",
        "hiring_manager": "Check whether this role schedules, delivers, or supports orientation in your organization before adding it to the job description.",
    },
    "task-people-data-reporting": {
        "ic_hr_practitioner": "Verify the measure definition, data source, access rules, and reporting purpose before sharing a figure.",
        "job_seeker": "Use a non-sensitive example of checking data and communicating a finding; no analytics tool or level is specified.",
        "hiring_manager": "Name the actual reports and locally required methods only after confirming them through job analysis.",
    },
    "task-eeo-policy-compliance": {
        "ic_hr_practitioner": "Use current, organization-approved guidance and the right escalation route; this guide is not legal advice.",
        "job_seeker": "Prepare a non-confidential example of locating and applying an approved rule, and explain when you would seek review.",
        "hiring_manager": "Define assigned compliance responsibilities and legal-review boundaries with qualified local reviewers before selection use.",
    },
    "task-hr-document-maintenance": {
        "ic_hr_practitioner": "Check document ownership, version control, access, retention, and approval steps before changing HR materials.",
        "job_seeker": "Describe a non-confidential example of maintaining accurate documents and following review controls.",
        "hiring_manager": "Identify which HR documents this role owns and the local approval controls before adding the duty to a role profile.",
    },
    "task-employee-exit-process": {
        "ic_hr_practitioner": "Follow local exit, confidentiality, referral, and records procedures; confirm who owns each step.",
        "job_seeker": "Use a hypothetical or anonymized example to describe careful handoffs and documentation without sharing case details.",
        "hiring_manager": "Confirm local responsibility for exit conversations and separation records; do not infer ownership from title alone.",
    },
    "task-context-scaled-organization-design": {
        "ic_hr_practitioner": "Check whether organization-design work belongs to this local role; the posting examples are context-specific.",
        "job_seeker": "Treat this as an optional context-specific example, not a universal HR Generalist requirement.",
        "hiring_manager": "Include this only if local responsibilities support it; do not generalize from the small posting sample.",
    },
    "task-context-coordinator-operations": {
        "ic_hr_practitioner": "Check local service-center, onboarding, and data-ownership boundaries; this is one coordinator-level example.",
        "job_seeker": "Treat these coordinator duties as one context example, not an entry-level standard for all HR roles.",
        "hiring_manager": "Confirm role level and local operational ownership before using this context addition in a job description.",
    },
    "task-context-defense-access": {
        "ic_hr_practitioner": "Verify any access-related conditions against the specific position and approved internal guidance; do not generalize them.",
        "job_seeker": "Read access conditions in the specific posting; this example is not a general HR qualification.",
        "hiring_manager": "Use only position-specific, reviewed requirements; this single-sector context is not a general HR qualification.",
    },
    "task-context-benefit-administration": {
        "ic_hr_practitioner": "Check the local division of benefit-plan work and relevant specialist or vendor handoffs.",
        "job_seeker": "Treat benefit administration as a possible local duty, not a universal requirement or proficiency rating.",
        "hiring_manager": "Verify benefit-plan responsibilities in your local job analysis before including this supplemental task.",
    },
    "competency-hr-knowledge": {
        "ic_hr_practitioner": "Use this as a topic checklist and consult current, organization-approved sources for assigned work.",
        "job_seeker": "Select a concrete learning or work example to discuss; this desk guide does not assign a knowledge level.",
        "hiring_manager": "Translate locally relevant HR topics into observable, job-related expectations through a separate job analysis.",
    },
    "competency-law-policy": {
        "ic_hr_practitioner": "Identify the applicable internal and authoritative sources for your assignment rather than relying on this summary as advice.",
        "job_seeker": "Prepare an example of finding or following an applicable rule; no legal expertise threshold is claimed.",
        "hiring_manager": "Define the specific local knowledge needs with qualified reviewers; do not infer a legal qualification from this construct.",
    },
    "competency-active-listening": {
        "ic_hr_practitioner": "Use a local conversation or intake checklist and reflect on whether you clarified the employee's account.",
        "job_seeker": "Prepare an example of clarifying what someone meant, without treating it as a scored assessment.",
        "hiring_manager": "If relevant locally, define job-related listening behaviors and assess them with a separately designed process.",
    },
    "competency-social-perceptiveness": {
        "ic_hr_practitioner": "Check your interpretation with the person or the appropriate process rather than assuming intent from reactions.",
        "job_seeker": "Use as a reflection prompt about noticing context, not as a claim that this guide assessed you.",
        "hiring_manager": "Avoid subjective inference; if relevant, define observable behaviors in a job analysis before evaluating candidates.",
    },
    "competency-judgment": {
        "ic_hr_practitioner": "Review available options and consult the appropriate escalation path for consequential decisions.",
        "job_seeker": "Prepare an example that explains options and trade-offs; this guide does not set an independent proficiency bar.",
        "hiring_manager": "Define decision authority and job-related examples locally before using judgment as a hiring criterion.",
    },
    "competency-administrative-records": {
        "ic_hr_practitioner": "Check the approved procedures and system controls used for the records you maintain.",
        "job_seeker": "Choose a non-confidential example of organized records work; no specific system is required by this guide.",
        "hiring_manager": "Identify the records and systems actually used locally rather than importing a generic tool requirement.",
    },
    "competency-instructing": {
        "ic_hr_practitioner": "Use approved process materials and confirm whether the learner can follow the relevant steps.",
        "job_seeker": "Prepare an example of making a process understandable; no credential or training proficiency is inferred.",
        "hiring_manager": "Confirm whether instruction is part of the local role and what observable work it entails.",
    },
    "competency-written-communication": {
        "ic_hr_practitioner": "Review a work product for audience, accuracy, confidentiality, and the intended next step.",
        "job_seeker": "Prepare a non-confidential writing example and explain its audience and purpose; no score is implied.",
        "hiring_manager": "Specify the writing tasks and audience locally before setting any job-related assessment.",
    },
    "competency-critical-thinking": {
        "ic_hr_practitioner": "Record the evidence and alternatives considered, and escalate decisions outside your authority.",
        "job_seeker": "Prepare an example of comparing options and explaining your reasoning without claiming a validated rating.",
        "hiring_manager": "Translate this broad construct into role-specific work examples before using it in selection.",
    },
}


def _reader_actions(unit_id: str) -> dict[str, str]:
    try:
        return dict(READER_ACTIONS_BY_UNIT[unit_id])
    except KeyError as exc:
        raise StructuredAnalysisError(f"missing audience-specific next actions for {unit_id}") from exc


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
            "reader_actions": _reader_actions(item["unit_id"]),
            "classification": item["context_label"],
            "evidence": evidence,
            "coverage_decision": {
                "status": "UNRESOLVED" if item["unit_id"] == "task-context-benefit-administration" else "INCLUDED",
                "rationale": (
                    "O*NET labels benefit-plan administration Supplemental and no admitted evidence establishes local HR Generalist ownership; it is shown only as an unresolved scope question."
                    if item["unit_id"] == "task-context-benefit-administration"
                    else "Included only as a labeled context example: the O*NET task is an occupational anchor and the quoted employer posting shows this local variation; neither posting frequency nor a single employer defines the common core."
                ),
                "evidence": evidence,
            },
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
            "reader_actions": _reader_actions(task["unit_id"]),
            "evidence": [task_quote],
            "coverage_decision": {
                "status": "INCLUDED",
                "rationale": "The admitted O*NET Human Resources Specialists profile lists this exact duty under Tasks › Core; it is retained as an occupational anchor, not as a posting-frequency or validated importance claim.",
                "evidence": [task_quote],
            },
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
            "reader_actions": _reader_actions(competency["unit_id"]),
            "evidence": [citation],
            "coverage_decision": {
                "status": "INCLUDED",
                "rationale": "The admitted O*NET occupation profile directly names this competency construct; its link to any specific task remains a separately labeled desk-research inference, not a measured task rating.",
                "evidence": [citation],
            },
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
        "reader_actions": _reader_actions(unit_id),
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
        "reader_actions": {
            "ic_hr_practitioner": "Use the counts only to locate wording in this dev sample; check actual local responsibilities and do not infer occupational importance or proficiency.",
            "job_seeker": "Use posting excerpts as examples for questions about a specific role, not as a prevalence estimate or a checklist of required qualifications.",
            "hiring_manager": "Use the local job analysis—not these small-sample counts—to define role scope, job-related criteria, and any selection process.",
        },
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


def _priority_evidence(corpus: FrozenCorpus, source_id: str, locator: str, quote: str) -> dict[str, Any]:
    return _source_citation(
        corpus,
        source_id,
        locator,
        quote,
        evidence_role="labeled_context_addition" if source_id.startswith("p-") else "occupational_anchor",
    )


def _build_skill_priorities(corpus: FrozenCorpus) -> list[dict[str, Any]]:
    """Build an authored learning order from direct occupational anchors, never posting counts."""
    occupational_locator = "O*NET OnLine 13-1071.00 › Tasks › Core"
    priorities = [
        {
            "rank": 1,
            "skill_id": "priority-policy-and-compliance-scope",
            "skill": "Interpret policy and compliance boundaries",
            "related_work_unit_ids": ["task-policy-guidance", "task-eeo-policy-compliance", "competency-law-policy"],
            "priority_rationale": "Placed first because the occupation profile directly lists policy/law interpretation and EEO-rule knowledge as core work; practitioners need this boundary before applying the other HR processes. This is an authored learning order, not a measured importance rank.",
            "evidence": [
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Interpret and explain human resources policies, procedures, laws, standards, or regulations."),
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Maintain current knowledge of Equal Employment Opportunity (EEO) and affirmative action guidelines and laws, such as the Americans with Disabilities Act (ADA)."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Knowledge › Law and Government", "Knowledge of laws, legal codes, court procedures, precedents, government regulations, executive orders, agency rules"),
            ],
            "practice_and_demonstration": {
                "ic_hr_practitioner": "Use an organization-approved source for a policy question, document the applicable rule and your role boundary, and identify the escalation route; do not use this guide as legal advice.",
                "job_seeker": "Prepare an anonymized example that names the authoritative rule you consulted, how you explained it, and when you sought review.",
                "hiring_manager": "Use a hypothetical policy scenario to check whether a candidate can find approved guidance, separate facts from interpretation, and describe a safe handoff; have qualified reviewers define any assessment.",
            },
            "context_variation": {
                "statement": "An Anduril People Business Partner posting explicitly includes HR-policy interpretation and separately states a U.S.-person condition in a defense-sector context. That access condition is position-specific; this small sample does not establish how compliance ownership varies by industry, employer size, or team shape.",
                "evidence": [
                    _priority_evidence(corpus, "p-andurilindustries-4835624007", "Employer posting People Business Partner › responsibilities", "provides hr policy guidance and interpretation"),
                    _priority_evidence(corpus, "p-andurilindustries-4835624007", "Employer posting People Business Partner › position-specific access condition", "u.s. person status is required"),
                ],
                "variation_limit": "One defense employer is not evidence of a general HR qualification or an industry-wide difference.",
            },
            "evidence_strength": {
                "label": "moderate: direct occupational wording, narrow source base",
                "occupational_anchor_source_count": 1,
                "occupational_anchor_source_ids": ["onet_hr_specialist"],
                "directness": "Direct O*NET core-task and knowledge descriptions; no HR-Generalist-specific task ratings or practitioner validation.",
            },
        },
        {
            "rank": 2,
            "skill_id": "priority-employee-relations-judgment",
            "skill": "Listen carefully and use judgment in employee-relations work",
            "related_work_unit_ids": ["task-employee-relations", "competency-active-listening", "competency-social-perceptiveness", "competency-judgment"],
            "priority_rationale": "The O*NET core task explicitly covers harassment allegations, complaints, and employee concerns. It follows policy scope in this learning order because the source names sensitive cases where careful intake and an appropriate next step matter; no relative-importance score is claimed.",
            "evidence": [
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Address employee relations issues, such as harassment allegations, work complaints, or other employee concerns."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Skills › Active Listening", "Giving full attention to what other people are saying, taking time to understand the points being made"),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Transferable Skills › Judgment and Decision Making", "Considering the relative costs and benefits of potential actions to choose the most appropriate one."),
            ],
            "practice_and_demonstration": {
                "ic_hr_practitioner": "Rehearse a local intake process with a fictional scenario: clarify what happened, record only necessary facts, check confidentiality boundaries, and identify a referral or escalation step.",
                "job_seeker": "Use a hypothetical or anonymized example to explain how you listened, checked assumptions, protected confidentiality, and knew when to escalate; do not disclose case details.",
                "hiring_manager": "If this work belongs in the role, define observable listening and escalation behaviors and use a consistent, job-related scenario reviewed by appropriate partners.",
            },
            "context_variation": {
                "statement": "Anduril People Business Partner postings describe employee-relations investigations and resolution. The examples are concentrated in one employer and do not show how case ownership varies across industries, employer sizes, or HR team structures.",
                "evidence": [
                    _priority_evidence(corpus, "p-andurilindustries-4835624007", "Employer posting People Business Partner › responsibilities", "manages and resolves complex employee relations issues"),
                    _priority_evidence(corpus, "p-andurilindustries-4855853007", "Employer posting Associate People Business Partner › responsibilities", "lead employee relations investigations"),
                ],
                "variation_limit": "The development sample is too small and employer-concentrated to support a comparative context claim.",
            },
            "evidence_strength": {
                "label": "moderate: direct task and skill wording, narrow source base",
                "occupational_anchor_source_count": 1,
                "occupational_anchor_source_ids": ["onet_hr_specialist"],
                "directness": "The task and named listening/judgment constructs are directly worded; linking the constructs to a local proficiency standard remains unvalidated.",
            },
        },
        {
            "rank": 3,
            "skill_id": "priority-records-and-document-control",
            "skill": "Maintain accurate people records and HR documents",
            "related_work_unit_ids": ["task-employee-records", "task-hr-document-maintenance", "task-employee-exit-process", "competency-administrative-records"],
            "priority_rationale": "O*NET directly names employee lifecycle records and HR document maintenance. This cross-cutting process skill follows employee-facing work in the learning order because it supports auditable transitions and records; no software or proficiency level is inferred.",
            "evidence": [
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Prepare or maintain employment records related to events, such as hiring, termination, leaves, transfers, or promotions, using human resources management system software."),
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Maintain and update human resources documents, such as organizational charts, employee handbooks or directories, or performance evaluation forms."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Knowledge › Administrative", "Knowledge of administrative and office procedures and systems such as word processing, managing files and records"),
            ],
            "practice_and_demonstration": {
                "ic_hr_practitioner": "Walk through an approved document update: verify the source of truth, access permissions, version and approval controls, then check the resulting record without exposing employee data.",
                "job_seeker": "Describe a non-confidential example of maintaining records, following version or privacy controls, and catching an error; name only tools the specific employer actually requires.",
                "hiring_manager": "Identify the records, documents, system access, retention rules, and decision authority local work requires before setting a selection exercise.",
            },
            "context_variation": {
                "statement": "One Datadog coordinator posting describes onboarding/offboarding administration, employee-data changes, document maintenance, and audits. This illustrates an operational role context; it does not establish that these duties belong to every HR Generalist or vary predictably by employer size.",
                "evidence": [
                    _priority_evidence(corpus, "p-datadog-7728298", "Employer posting People Solutions Coordinator › responsibilities", "own the administrative execution of new hire onboarding and offboarding"),
                    _priority_evidence(corpus, "p-datadog-7728298", "Employer posting People Solutions Coordinator › responsibilities", "organize and maintain internal documentation"),
                ],
                "variation_limit": "One coordinator-level posting is a context example, not a general entry-level or team-design standard.",
            },
            "evidence_strength": {
                "label": "moderate: direct task wording, narrow source base",
                "occupational_anchor_source_count": 1,
                "occupational_anchor_source_ids": ["onet_hr_specialist"],
                "directness": "O*NET directly names records and document tasks; the one posting example is secondary context, not a prevalence estimate.",
            },
        },
        {
            "rank": 4,
            "skill_id": "priority-manager-guidance",
            "skill": "Explain HR processes clearly to managers",
            "related_work_unit_ids": ["task-manager-guidance", "competency-instructing", "competency-written-communication"],
            "priority_rationale": "The O*NET core profile directly includes providing managers information or training about performance, counseling, and documentation. This follows core policy, case, and record practice as a communication application; posting counts are not used to rank it.",
            "evidence": [
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Provide management with information or training related to interviewing, performance appraisals, counseling techniques, or documentation of performance issues."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Transferable Skills › Instructing", "Teaching others how to do something."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Abilities › Written Expression", "The ability to communicate information and ideas in writing so others will understand."),
            ],
            "practice_and_demonstration": {
                "ic_hr_practitioner": "Turn an approved process into a short manager briefing: state the next step, documentation needed, boundary of your authority, and escalation route; ask the manager to explain it back.",
                "job_seeker": "Prepare a non-confidential example of making a process understandable, tailoring it to the audience, and clarifying what decision remained with the manager.",
                "hiring_manager": "If manager enablement is assigned locally, ask for a short explanation of a job-relevant process and evaluate accuracy, clarity, and escalation boundaries consistently.",
            },
            "context_variation": {
                "statement": "The dev ads show different audiences: a People Business Partner posting describes advice to managers, while a Stripe People Partner role emphasizes senior-leader partnership. They illustrate role-specific team relationships, not a reliable employer-size or industry pattern.",
                "evidence": [
                    _priority_evidence(corpus, "p-andurilindustries-4835624007", "Employer posting People Business Partner › responsibilities", "coach and advise for best practices within the team"),
                    _priority_evidence(corpus, "p-stripe-7466921", "Employer posting People Partner, Technology › responsibilities", "advise and coach leaders and managers on org design and strategy"),
                ],
                "variation_limit": "The seven-posting dev set cannot establish a representative team-shape or seniority distribution.",
            },
            "evidence_strength": {
                "label": "moderate: direct task and skill wording, narrow source base",
                "occupational_anchor_source_count": 1,
                "occupational_anchor_source_ids": ["onet_hr_specialist"],
                "directness": "The manager-training task and communication constructs are directly named; role-specific assessment criteria are not validated.",
            },
        },
        {
            "rank": 5,
            "skill_id": "priority-people-data-reasoning",
            "skill": "Check people data and explain what it can support",
            "related_work_unit_ids": ["task-people-data-reporting", "competency-critical-thinking", "competency-judgment"],
            "priority_rationale": "The O*NET core task explicitly names employment-data analysis and required reports. It is placed after core policy, employee-facing, record, and manager-guidance work as a separate analytic application; no tool, analytic depth, or prevalence is claimed.",
            "evidence": [
                _priority_evidence(corpus, "onet_hr_specialist", occupational_locator, "Analyze employment-related data and prepare required reports."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Skills › Critical Thinking", "Using logic and reasoning to identify the strengths and weaknesses of alternative solutions, conclusions, or approaches to problems."),
                _priority_evidence(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Transferable Skills › Judgment and Decision Making", "Considering the relative costs and benefits of potential actions to choose the most appropriate one."),
            ],
            "practice_and_demonstration": {
                "ic_hr_practitioner": "Use a non-sensitive reporting example to verify the measure definition, denominator, time window, access rules, and limitations before sharing a finding.",
                "job_seeker": "Explain a non-sensitive example of checking a data definition, comparing options, and communicating a finding with its limits; do not claim a tool is universally required.",
                "hiring_manager": "Name the actual local reports and access boundaries, then use a consistent work sample only if local job analysis supports it.",
            },
            "context_variation": {
                "statement": "A Coinbase HR Business Partner posting describes trend analysis and actionable leadership recommendations. This is one data-use example; the sample does not establish a universal analytics tool, reporting depth, or employer-size pattern.",
                "evidence": [
                    _priority_evidence(corpus, "p-coinbase-7997879", "Employer posting HR Business Partner › responsibilities", "drive data-informed people insights by analyzing trends and translating them into actionable recommendations for leadership"),
                ],
                "variation_limit": "One employer posting cannot establish a context-wide skill demand or technical requirement.",
            },
            "evidence_strength": {
                "label": "moderate: direct task and skill wording, narrow source base",
                "occupational_anchor_source_count": 1,
                "occupational_anchor_source_ids": ["onet_hr_specialist"],
                "directness": "Direct task/skill wording supports the construct; no task-rating record or tool-specific standard is in the admitted sources.",
            },
        },
    ]
    return priorities


def _decision_record(
    status: str,
    item: str,
    rationale: str,
    evidence: list[dict[str, Any]],
    *,
    work_unit_id: str | None = None,
) -> dict[str, Any]:
    if status not in {"INCLUDED", "EXCLUDED", "UNRESOLVED"} or not evidence:
        raise StructuredAnalysisError("every extraction decision needs a valid status and source evidence")
    record = {"status": status, "item": item, "rationale": rationale, "evidence": evidence}
    if work_unit_id:
        record["work_unit_id"] = work_unit_id
    return record


def _build_extraction_transparency(corpus: FrozenCorpus, units: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain evidence-backed inclusion, exclusion, and unresolved decisions by admitted source."""
    provenance = {row["source_id"]: row for row in _provenance(corpus)}
    admitted_sources: list[dict[str, Any]] = []
    posting_decision_terms = {
        "p-andurilindustries-4835624007": "provides hr policy guidance and interpretation",
        "p-andurilindustries-4836444007": "provides hr policy guidance and interpretation",
        "p-andurilindustries-4855853007": "support managers and employees throughout the entire employment lifecycle",
        "p-coinbase-7997879": "drive data-informed people insights by analyzing trends",
        "p-datadog-7728298": "own the administrative execution of new hire onboarding and offboarding",
        "p-stripe-7466921": "advise and coach leaders and managers on org design and strategy",
        "p-stripe-7704660": "advise and coach leaders and managers on org design and strategy",
    }

    for source_id, source in corpus.sources.items():
        meta = provenance[source_id]
        decisions: list[dict[str, Any]] = []
        if source_id == "onet_hr_specialist":
            for unit in units:
                source_evidence = [
                    citation for citation in unit["coverage_decision"]["evidence"]
                    if citation["source_id"] == source_id
                ]
                if not source_evidence:
                    continue
                decisions.append(_decision_record(
                    unit["coverage_decision"]["status"],
                    f"{unit['kind']} {unit['unit_id']}: {unit['statement']}",
                    unit["coverage_decision"]["rationale"],
                    source_evidence,
                    work_unit_id=unit["unit_id"],
                ))
            decisions.extend([
                _decision_record(
                    "EXCLUDED",
                    "Recruiting-only job scope from the HR Generalist common core",
                    "O*NET reports recruiter titles and recruiting activity in its broader Human Resources Specialists profile; recruiting-only roles remain out of this guide's stated HR Generalist IC scope. This excludes a role-scope category, not every hiring task an HR Generalist might perform.",
                    [
                        _source_citation(corpus, source_id, "O*NET OnLine 13-1071.00 › Sample of reported job titles", "Corporate Recruiter", evidence_role="extraction_decision_evidence"),
                        _source_citation(corpus, source_id, "O*NET OnLine 13-1071.00 › Tasks › Core", "Perform searches for qualified job candidates, using sources such as computer databases, networking, Internet recruiting resources, media advertisements, job fairs, recruiting firms, or employee referrals.", evidence_role="extraction_decision_evidence"),
                    ],
                ),
                _decision_record(
                    "UNRESOLVED",
                    "Whether hiring administration or applicant-selection work belongs in a particular HR Generalist role",
                    "The profile labels hiring paperwork and applicant selection as core to the broader occupation, but the admitted evidence does not establish local HR-Generalist ownership; the guide therefore does not silently promote it into its common core.",
                    [
                        _source_citation(corpus, source_id, "O*NET OnLine 13-1071.00 › Tasks › Core", "Hire employees and process hiring-related paperwork.", evidence_role="extraction_decision_evidence"),
                        _source_citation(corpus, source_id, "O*NET OnLine 13-1071.00 › Tasks › Core", "Select qualified job applicants or refer them to managers, making hiring recommendations when appropriate.", evidence_role="extraction_decision_evidence"),
                    ],
                ),
                _decision_record(
                    "UNRESOLVED",
                    "Employee benefit-plan administration as a local HR Generalist duty",
                    "O*NET labels this task Supplemental rather than Core; local ownership is not established, so it remains visible as unresolved context rather than a universal duty.",
                    [_source_citation(corpus, source_id, "O*NET OnLine 13-1071.00 › Tasks › Supplemental", "Administer employee benefit plans.", evidence_role="extraction_decision_evidence")],
                ),
            ])
        elif source_id == "onet_task_ratings_dictionary":
            decisions.append(_decision_record(
                "UNRESOLVED",
                "Relative task importance, task frequency, and numeric proficiency rankings for this HR Generalist guide",
                "The admitted source is the O*NET task-ratings data dictionary, not HR-specific task-rating records; its field description cannot supply role-specific ratings by itself.",
                [_source_citation(corpus, source_id, "O*NET 31.0 task_ratings data dictionary › description", "This table contains ratings which describe the importance, relevance, and frequency of occupation-specific tasks and duties performed in a job.", evidence_role="extraction_decision_evidence")],
            ))
        elif source_id == "opm_job_analysis":
            decisions.append(_decision_record(
                "INCLUDED",
                "Method anchor for documenting task–competency relationships",
                "Used as general job-analysis methodology only: OPM explicitly describes examining tasks, required competencies, and their connection. It does not supply HR Generalist-specific duties.",
                [_source_citation(corpus, source_id, "OPM Job Analysis › definition of job analysis", "the tasks performed in a job, the competencies required to perform those tasks, and the connection between the tasks and competencies.", evidence_role="method_anchor")],
            ))
            decisions.append(_decision_record(
                "EXCLUDED",
                "OPM page as an HR-specific duty source",
                "The quoted passage defines a general method rather than naming a Human Resources Specialist duty; no HR task is inferred from it.",
                [_source_citation(corpus, source_id, "OPM Job Analysis › use of job analysis data", "Job analysis is the foundation for all assessment and selection decisions.", evidence_role="extraction_decision_evidence")],
            ))
        elif source_id == "esco_essential_optional":
            decisions.append(_decision_record(
                "UNRESOLVED",
                "Whether any particular skill is universally essential for a U.S. HR Generalist",
                "ESCO defines essential and optional skills within occupational profiles, but the admitted page is not a U.S. HR-Generalist-specific profile; no ESCO essentiality label is transferred to this guide.",
                [_source_citation(corpus, source_id, "ESCO ESCOpedia › Essential", "ESCO distinguishes essential and optional knowledge, skills and competences in occupational profiles.", evidence_role="extraction_decision_evidence")],
            ))
        elif source_id == "esco_use_api":
            decisions.append(_decision_record(
                "EXCLUDED",
                "ESCO API or taxonomy-access details as job duties or competency evidence",
                "This source describes ways to access the ESCO classification, not an HR Generalist work profile or observed job task.",
                [_source_citation(corpus, source_id, "ESCO Use ESCO › access methods", "Access ESCO classification through two types of Application Program Interface (API):", evidence_role="extraction_decision_evidence")],
            ))
        elif source_id == "dacum_method":
            decisions.append(_decision_record(
                "INCLUDED",
                "DACUM-informed task and duty decomposition as a method boundary",
                "The source describes expert-worker task analysis; its structure informs desk research only, and this project did not run a DACUM panel.",
                [_source_citation(corpus, source_id, "DACUM International Training Center › process description", "a panel of expert workers and a skilled facilitator working together to precisely identify the duties and tasks performed in a job", evidence_role="method_anchor")],
            ))
            decisions.append(_decision_record(
                "EXCLUDED",
                "Claim that this guide is a completed or practitioner-validated DACUM study",
                "The cited method requires an expert-worker panel and facilitator; no such panel participated in this desk analysis.",
                [_source_citation(corpus, source_id, "DACUM International Training Center › process description", "a panel of expert workers and a skilled facilitator working together to precisely identify the duties and tasks performed in a job", evidence_role="extraction_decision_evidence")],
            ))
        elif source_id in posting_decision_terms:
            term = posting_decision_terms[source_id]
            if term not in corpus.source_texts[source_id]:
                raise StructuredAnalysisError(f"transparency quote not present in admitted source {source_id}")
            quote = term
            evidence = [_source_citation(
                corpus,
                source_id,
                f"Employer posting {source['title']} › responsibilities (source-use decision)",
                quote,
                evidence_role="labeled_context_addition",
            )]
            decisions.extend([
                _decision_record(
                    "INCLUDED",
                    "Employer-specific evidence as a counts-only observation or explicitly labeled context example",
                    "The quoted employer posting names its own role responsibility; it is retained as local context or a phrase-screen observation, not as the occupational backbone or a frequency-based priority.",
                    evidence,
                ),
                _decision_record(
                    "EXCLUDED",
                    "Generalizing this employer's quoted responsibility to all HR Generalist roles",
                    "A source-specific posting describes one employer's role; its wording is excluded from universal duty claims and is not pasted as a posting wall into the guide.",
                    evidence,
                ),
            ])
        else:
            raise StructuredAnalysisError(f"admitted source has no extraction-transparency decision: {source_id}")

        if not decisions or any(not decision["evidence"] or not decision["rationale"].strip() for decision in decisions):
            raise StructuredAnalysisError(f"admitted source decisions are not traceable: {source_id}")
        admitted_sources.append({
            "source_id": source_id,
            "source_kind": meta["source_kind"],
            "attribution": meta["attribution"],
            "source_url": meta["url"],
            "retrieved_at": meta["retrieved_at"],
            "extract_sha256": meta["sha256"],
            "role_class": source.get("role_class"),
            "decisions": decisions,
        })

    all_decisions = [decision for source in admitted_sources for decision in source["decisions"]]
    return {
        "schema_version": "structured-job-analysis.extraction-transparency.v1",
        "policy": "Every admitted source receives source-evidence-backed inclusion, exclusion, or unresolved decisions; job-posting observations never become occupational anchors solely through frequency.",
        "admitted_source_count": len(admitted_sources),
        "decision_counts": {
            status.lower(): sum(decision["status"] == status for decision in all_decisions)
            for status in ("INCLUDED", "EXCLUDED", "UNRESOLVED")
        },
        "admitted_sources": admitted_sources,
    }


def render_evidence_transparency(release: dict[str, Any]) -> str:
    transparency = release["extraction_transparency"]
    lines = [
        "# Source-by-source evidence and extraction decisions",
        "",
        "**Status:** This is the secondary evidence view for the deterministic HR Generalist desk analysis. It records source-specific decisions; it is not practitioner validation.",
        "",
        transparency["policy"],
        "",
        f"Admitted sources: {transparency['admitted_source_count']}. Decisions: {transparency['decision_counts']['included']} INCLUDED, {transparency['decision_counts']['excluded']} EXCLUDED, {transparency['decision_counts']['unresolved']} UNRESOLVED.",
        "",
    ]
    for source in transparency["admitted_sources"]:
        lines.extend([
            f"## `{source['source_id']}` — {source['attribution']}",
            "",
            f"Source type: `{source['source_kind']}`. Extract SHA-256: `{source['extract_sha256']}`. Retrieved {source['retrieved_at']}.",
            "",
        ])
        for decision in source["decisions"]:
            lines.extend([
                f"### {decision['status']}: {decision['item']}",
                "",
                decision["rationale"],
                "",
            ])
            for evidence in decision["evidence"]:
                lines.append(f"- Evidence: “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
            lines.append("")
    lines.extend([
        "## Limits",
        "",
        "O*NET provides a broad Human Resources Specialists profile, not a role-specific HR Generalist validation. Posting evidence remains employer-specific; counts are descriptive phrase screens only. Benefit-plan administration and local ownership of hiring work remain unresolved. No held-out content or unadmitted source was used.",
        "",
    ])
    return "\n".join(lines)


def build_release(corpus: FrozenCorpus, candidate_revision: str, *, mode: str = "offline") -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{7,40}", candidate_revision):
        raise StructuredAnalysisError("candidate_revision must be a git SHA")
    started = time.monotonic()
    units = _build_work_units(corpus)
    demand = _fill_demand(corpus, units)
    skill_priorities = _build_skill_priorities(corpus)
    extraction_transparency = _build_extraction_transparency(corpus, units)
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
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Maintain current knowledge of Equal Employment Opportunity (EEO) and affirmative action guidelines and laws, such as the Americans with Disabilities Act (ADA).", evidence_role="role_definition_support"),
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Maintain and update human resources documents, such as organizational charts, employee handbooks or directories, or performance evaluation forms.", evidence_role="role_definition_support"),
        _source_citation(corpus, "onet_hr_specialist", "O*NET OnLine 13-1071.00 › Tasks › Core", "Conduct exit interviews and ensure that necessary employment termination paperwork is completed.", evidence_role="role_definition_support"),
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
    execution_model = "deterministic-structured-job-analysis.v1" if mode == "offline" else PINNED_MODEL_ID
    execution_provider = "local" if mode == "offline" else "deepinfra"
    config_hash = APPROVED_RESOURCE_CONFIG_SHA256
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "schema_version": "skills-vector.poc-output.v1",
        "method": "structured-job-analysis",
        "run": {
            "run_id": f"sja-a-corrective-r1-{_sha256(_json_bytes([candidate_revision, corpus.benchmark_manifest_sha256, mode]))[:16]}",
            "round": 1,
            "corrective_round": 1,
            "candidate_revision": candidate_revision,
            "corpus_snapshot_ids": list(SNAPSHOT_IDS),
            "model": execution_model,
            "provider": execution_provider,
            "config_hash": config_hash,
            "resource_config_scope": "parent-approved shared A/B live-run configuration; offline execution remains local and deterministic",
            "planned_resource_config": {
                "model": MATCHED_RESOURCE_CONFIG["model"],
                "provider": MATCHED_RESOURCE_CONFIG["provider"],
                "sampling": dict(MATCHED_RESOURCE_CONFIG["sampling"]),
                "caps": dict(MATCHED_RESOURCE_CONFIG["caps"]),
                "evidence_policy": MATCHED_EVIDENCE_POLICY,
            },
            "execution_label": (
                "offline deterministic generator; zero model inference and zero provider calls"
                if mode == "offline"
                else "offline-generated guide awaiting one guarded live model review; review suggestions are not validation"
            ),
            "publication_status": "offline_guide" if mode == "offline" else "offline_guide_pending_model_review",
            "model_review_status": "not_requested" if mode == "offline" else "pending",
            "model_review_accepted": False,
            "started_at": now,
            "finished_at": now,
            "resource_ledger": {
                "inference_requests": 0,
                "inference_cost_usd_estimate": 0.0,
                "inference_cost_usd_upper_bound": 0.0,
                "inference_cost_basis": "No provider inference was requested; zero inference calls and zero inference cost for this offline build.",
                "measured_provider_cost_usd": None,
                "provider_usage": {"status": "not_applicable", "input_tokens": None, "output_tokens": None, "reasoning_tokens": None},
                "provider_usage_cost_estimate_usd": None,
                "inference_cost_usd_reservation_status": "not_applicable",
                "retrieval_requests": 0,
                "wall_clock_minutes": round((time.monotonic() - started) / 60, 6),
            },
            "mode": mode,
            "resource_caps": dict(MATCHED_RESOURCE_CONFIG["caps"]),
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
            "definition": "A cross-industry HR individual contributor who interprets policy, supports employee relations, maintains people records and HR documents, guides managers, supports employee transitions, and reports employment-related data; local ownership varies.",
            "definition_evidence": role_definition_evidence,
            "reader_actions": {
                "ic_hr_practitioner": "Compare this scope with your organization's role boundaries and document local responsibilities or referrals.",
                "job_seeker": "Use the definition to compare a specific posting with your experience; it is a desk-research summary, not a universal qualification list.",
                "hiring_manager": "Confirm local work, decision authority, and exclusions through a separate job analysis before using this role summary.",
            },
            "title_variant_validation": {
                **title_validation,
                "reader_actions": {
                    "ic_hr_practitioner": "Check title equivalence against actual responsibilities in your organization rather than relying on labels alone.",
                    "job_seeker": "Read each employer's responsibilities; similar titles or functions do not establish identical scope.",
                    "hiring_manager": "Validate the title against local responsibilities; these cited examples do not establish market-wide title equivalence.",
                },
            },
        },
        "guide": {"path": "guide.md", "format": "markdown", "html_path": "guide.html", "secondary_evidence_path": "evidence-transparency.md"},
        "work_units": units,
        "skill_priorities": skill_priorities,
        "extraction_transparency": extraction_transparency,
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
                {
                    "area": unit["unit_id"],
                    "represented": True,
                    "decision": unit["coverage_decision"]["status"],
                    "rationale": unit["coverage_decision"]["rationale"],
                    "evidence": unit["coverage_decision"]["evidence"],
                    "anchor_source_id": unit["method_fields"]["onet_anchor_ids"][0],
                }
                for unit in units if unit["kind"] == "task" and unit["method_fields"].get("classification") == "common_core"
            ],
            "duty_dispositions": [
                decision
                for source in extraction_transparency["admitted_sources"]
                if source["source_id"] == "onet_hr_specialist"
                for decision in source["decisions"]
                if decision["status"] != "INCLUDED"
            ],
            "decision_traceability": "Every included work unit carries an evidence-backed decision; excluded and unresolved O*NET-sourced role-scope decisions retain exact source quotes in duty_dispositions and extraction_transparency.",
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
        decision = unit.get("coverage_decision")
        if (
            not isinstance(decision, dict)
            or decision.get("status") not in {"INCLUDED", "EXCLUDED", "UNRESOLVED"}
            or not isinstance(decision.get("rationale"), str)
            or not decision["rationale"].strip()
            or not decision.get("evidence")
        ):
            raise StructuredAnalysisError(f"missing evidence-backed coverage decision on {unit['unit_id']}")
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

    priorities = release.get("skill_priorities")
    if not isinstance(priorities, list) or not priorities or [row.get("rank") for row in priorities] != list(range(1, len(priorities) + 1)):
        raise StructuredAnalysisError("skill priorities must use stable, contiguous authored ranks")
    for priority in priorities:
        strength = priority.get("evidence_strength", {})
        anchor_ids = strength.get("occupational_anchor_source_ids")
        if (
            not priority.get("priority_rationale", "").strip()
            or not priority.get("context_variation", {}).get("variation_limit", "").strip()
            or not priority.get("context_variation", {}).get("evidence")
            or not priority.get("practice_and_demonstration")
            or strength.get("occupational_anchor_source_count") != len(anchor_ids or [])
            or not anchor_ids
        ):
            raise StructuredAnalysisError(f"skill-priority rationale or evidence is incomplete: {priority.get('skill_id')}")
        if not set(priority["related_work_unit_ids"]) <= ids:
            raise StructuredAnalysisError(f"skill-priority references an unknown work unit: {priority.get('skill_id')}")

    transparency = release.get("extraction_transparency", {})
    source_rows = transparency.get("admitted_sources", [])
    source_ids = {row.get("source_id") for row in source_rows}
    if len(source_rows) != len(corpus.sources) or source_ids != known:
        raise StructuredAnalysisError("extraction transparency must account for every admitted source exactly once")
    for source in source_rows:
        for decision in source.get("decisions", []):
            if (
                decision.get("status") not in {"INCLUDED", "EXCLUDED", "UNRESOLVED"}
                or not decision.get("rationale", "").strip()
                or not decision.get("evidence")
                or any(item.get("source_id") != source["source_id"] for item in decision["evidence"])
            ):
                raise StructuredAnalysisError(f"untraceable extraction decision for source {source.get('source_id')}")


def _reader_action_lines(actions: dict[str, str]) -> list[str]:
    labels = {
        "ic_hr_practitioner": "IC HR practitioner",
        "job_seeker": "Job seeker",
        "hiring_manager": "Hiring manager",
    }
    return [
        "**Next steps (practical prompts, not proficiency ratings):**",
        *(f"- **{labels[audience]}:** {actions[audience]}" for audience in labels),
        "",
    ]


def render_markdown(release: dict[str, Any]) -> str:
    role = release["role"]
    publication_notices = {
        "offline_guide": "> **Publication status: offline guide.** This is a deterministic offline build; no model review was requested.",
        "offline_guide_pending_model_review": "> **Publication status: offline guide.** A live model review is pending; this guide is not a model-reviewed release.",
        "offline_guide_after_failed_live_review": "> **Publication status: offline guide after failed live attempt.** No provider response was received; this guide is not model-reviewed.",
        "offline_guide_after_rejected_review": "> **Publication status: offline guide after rejected review.** The deterministic guide is retained for reference; the rejected model response is not published as a review.",
        "model_reviewed_release": "> **Publication status: model-reviewed release.** The offline guide was reviewed under the strict contract; model suggestions remain untrusted and are not practitioner validation.",
    }
    publication_notice = publication_notices.get(
        release["run"].get("publication_status"),
        "> **Publication status: unverified.** This release has no recognized accepted model-review marker.",
    )
    lines = [
        "# HR Generalist / People Operations — structured job analysis",
        "",
        "**Corrective round 1 · POC A · US individual-contributor scope**",
        f"Candidate `{release['run']['candidate_revision']}` · run `{release['run']['run_id']}`",
        f"Evidence snapshots: {', '.join(release['run']['corpus_snapshot_ids'])}",
        "",
        publication_notice,
        "",
        "> **Evidence boundary.** This is DACUM-informed desk research, not a DACUM study and not a practitioner-validated analysis. O*NET/OPM anchor the task–competency backbone; employer postings are kept in separate counts-only demand observations or clearly labeled context additions.",
        "",
        "## How to use this guide",
        "",
        "Start with the skill practice sequence below, then compare the task statements with local responsibilities. The ordering is an evidence-based learning sequence, not a posting-frequency, importance, or proficiency ranking.",
        "",
        *_reader_action_lines(role["reader_actions"]),
        "## Role and scope",
        "",
        role["definition"],
        "",
        *_reader_action_lines(role["reader_actions"]),
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
        "",
        *_reader_action_lines(role["title_variant_validation"]["reader_actions"]),
        f"**Exact-title negative finding:** {role['title_variant_validation']['negative_title_scan']}",
        "",
        "**Supporting title/responsibility excerpts:**",
    ])
    for evidence in role["title_variant_validation"]["supporting_related_title_evidence"]:
        lines.append(f"- “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
    for evidence in role["title_variant_validation"]["evidence"]:
        lines.append(f"- “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']} (responsibility-based People Operations variant).")
    lines.extend(["", "## Prioritized skill practice", ""])
    for priority in release["skill_priorities"]:
        lines.extend([
            f"### {priority['rank']}. {priority['skill']}",
            "",
            priority["priority_rationale"],
            "",
        ])
        for evidence in priority["evidence"]:
            lines.append(f"- Occupational anchor: “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
        strength = priority["evidence_strength"]
        lines.extend([
            "",
            f"**Evidence strength:** {strength['label']}. {strength['directness']} Occupational anchor sources: {strength['occupational_anchor_source_count']} ({', '.join(strength['occupational_anchor_source_ids'])}).",
            "",
            f"**Context variation:** {priority['context_variation']['statement']}",
        ])
        for evidence in priority["context_variation"]["evidence"]:
            lines.append(f"- Context example: “{evidence['quote']}” — `{evidence['source_id']}`, {evidence['locator']}.")
        lines.extend([
            f"**Context limit:** {priority['context_variation']['variation_limit']}",
            "",
            "**Practice or demonstrate:**",
            f"- **IC HR practitioner:** {priority['practice_and_demonstration']['ic_hr_practitioner']}",
            f"- **Job seeker:** {priority['practice_and_demonstration']['job_seeker']}",
            f"- **Hiring manager:** {priority['practice_and_demonstration']['hiring_manager']}",
            "",
        ])
    lines.extend(["## Task–competency backbone", ""])
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
            *_reader_action_lines(unit["reader_actions"]),
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
        lines.extend(_reader_action_lines(unit["reader_actions"]))
    lines.extend(["", "## Context adaptations — do not generalize", ""])
    for unit in release["work_units"]:
        if unit["method_fields"].get("context_adaptation") is not True:
            continue
        lines.append(f"### {unit['statement']}")
        lines.append("")
        lines.extend(_reader_action_lines(unit["reader_actions"]))
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
        *_reader_action_lines(demand["reader_actions"]),
        "| Work-unit phrase screen | Dev posting matches / 7 | Evidence excerpts | Audience-specific next steps |",
        "|---|---:|---|---|",
    ])
    for row in demand["coverage_by_unit"]:
        excerpts = "<br>".join(
            f"{item['source_id']}: “{item['quote']}”" for item in row["matches"]
        ) or "No configured phrase hit; absence is not inferred."
        action_cell = "<br>".join(
            f"{audience.replace('_', ' ')}: {action}"
            for audience, action in row["reader_actions"].items()
        )
        lines.append(f"| `{row['unit_id']}` | {row['matched_dev_postings']} / {row['dev_posting_denominator']} | {excerpts} | {action_cell} |")
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
    lines.extend([
        "",
        "## Source-by-source evidence decisions",
        "",
        "The secondary human-readable view records admitted-source decisions, exact evidence, exclusions, and unresolved scope questions: [open evidence-transparency.md](evidence-transparency.md).",
        "",
        "## Machine release",
        "",
        "Read `release.json` for stable unit ids, exact citations, uncertainty labels, prioritized skills, source decisions, separate posting counts, publication status, and the run/resource ledger.",
        "",
    ])
    return "\n".join(lines)


def _inline_html(text: str) -> str:
    safe = html.escape(text)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', safe)
    safe = re.sub(r"\[([^\]]+)\]\(([A-Za-z0-9_.-]+\.md)\)", r'<a href="\2">\1</a>', safe)
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
.meta{{font: .75rem/1.4 \"IBM Plex Mono\",monospace;color:var(--muted);overflow-wrap:anywhere}}a{{color:var(--present)}}code{{font-family:\"IBM Plex Mono\",monospace;font-size:.88em;overflow-wrap:anywhere}}li{{overflow-wrap:anywhere}}blockquote,.warning{{background:#efe6e0;border:1px solid #cbb9ae;padding:.9rem;margin:1rem 0}}blockquote{{color:#163049}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{border:1px solid var(--rule);padding:.55rem;text-align:left;vertical-align:top}}th{{background:var(--mineral)}}
@media(max-width:640px){{main{{padding:1rem}}.meta{{font-size:.68rem}}}}
</style>
</head>
<body><main><header><p class=\"meta\">CORRECTIVE ROUND 1 · POC A · LOCAL EVIDENCE GUIDE</p><h1>{html.escape(title)}</h1>
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
    if sampling.get("reasoning_effort") != "none":
        raise StructuredAnalysisError('sampling.reasoning_effort must be exactly "none"')
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens != MAX_OUTPUT_TOKENS:
        raise StructuredAnalysisError(f"sampling.max_tokens must be exactly {MAX_OUTPUT_TOKENS}")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise StructuredAnalysisError("caps.max_inference_requests must be a positive integer")
    if not math.isfinite(max_cost) or not 0 < max_cost <= MAX_FINAL_SPEND_USD:
        raise StructuredAnalysisError("caps.max_inference_cost_usd must be positive and no greater than USD 0.50")
    if isinstance(retrieval_cap, bool) or retrieval_cap != 0:
        raise StructuredAnalysisError("live final mode is frozen-corpus-only; max_retrieval_requests must be zero")
    if not math.isfinite(wall_cap) or wall_cap <= 0:
        raise StructuredAnalysisError("caps.max_wall_minutes must be positive")


def _request_body(release: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    evidence_catalog: list[dict[str, str]] = []
    reference_ids: dict[tuple[str, str, str], int] = {}

    def reference_id(citation: dict[str, Any]) -> int:
        key = (citation["source_id"], citation["locator"], citation["quote"])
        if key not in reference_ids:
            reference_ids[key] = len(evidence_catalog)
            evidence_catalog.append({
                "source_id": key[0],
                "locator": key[1],
                "quote": key[2],
            })
        return reference_ids[key]

    work_units = []
    for unit in release["work_units"]:
        links = []
        for link in unit["method_fields"]["task_competency_links"]:
            links.append({
                "competency_unit_id": link["competency_unit_id"],
                "direction": link["direction"],
                "justification": link["justification"],
                "evidence_ref_ids": [reference_id(item) for item in link["evidence"]],
            })
        work_units.append({
            "unit_id": unit["unit_id"],
            "kind": unit["kind"],
            "statement": unit["statement"],
            "evidence_ref_ids": [reference_id(item) for item in unit["evidence"]],
            "task_competency_links": links,
            "uncertainty": unit["uncertainty"],
        })

    evidence = {
        "run_id": release["run"]["run_id"],
        "method": release["method"],
        "work_units": work_units,
        "evidence_catalog": evidence_catalog,
        "limits": [
            "Only inspect citations in each work unit's evidence_ref_ids and its links' evidence_ref_ids; no web retrieval or extra source allowed.",
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
        "reasoning_effort": sampling["reasoning_effort"],
        "response_format": {"type": "json_object"},
    }


def _serialized_request_bytes(body: dict[str, Any]) -> bytes:
    """Serialize exactly as the DeepInfra HTTP request body is serialized."""
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def estimate_request_token_ceilings(body: dict[str, Any]) -> dict[str, Any]:
    """Return fail-closed input/output/reasoning ceilings and their priced upper bound."""
    if body.get("model") != PINNED_MODEL_ID:
        raise StructuredAnalysisError("provider request model differs from the pinned model")
    if body.get("reasoning_effort") != "none":
        raise StructuredAnalysisError('provider request reasoning_effort must be exactly "none"')
    output_tokens = body.get("max_tokens")
    if (
        isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens != MAX_OUTPUT_TOKENS
    ):
        raise StructuredAnalysisError(f"provider request max_tokens must be exactly {MAX_OUTPUT_TOKENS}")
    serialized = _serialized_request_bytes(body)
    input_bytes = len(serialized)
    if input_bytes > MAX_SERIALIZED_REQUEST_BYTES:
        raise StructuredAnalysisError(
            f"serialized provider request is {input_bytes} bytes; ceiling is {MAX_SERIALIZED_REQUEST_BYTES}"
        )
    # Two input tokens per UTF-8 request byte is intentionally conservative.
    input_tokens = input_bytes * 2
    if input_tokens > MAX_INPUT_TOKENS_UPPER_BOUND:
        raise StructuredAnalysisError(
            f"request input-token upper bound {input_tokens} exceeds {MAX_INPUT_TOKENS_UPPER_BOUND}"
        )
    # DeepInfra documents max_tokens as the generated-token ceiling. Reasoning is
    # billable output, so it shares this ceiling rather than receiving an additive
    # allowance; reasoning_effort=none is sent explicitly as an additional control.
    cost_upper_bound = estimate_cost_usd(
        PINNED_MODELS["extract"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return {
        "request_sha256": _sha256(serialized),
        "input_bytes": input_bytes,
        "input_tokens_upper_bound": input_tokens,
        "output_tokens_upper_bound_including_reasoning": output_tokens,
        "reasoning_effort": "none",
        "reasoning_tokens_additional_allowance": 0,
        "total_input_plus_output_including_reasoning_tokens_upper_bound": input_tokens + output_tokens,
        "reasoning_accounting": "any generated/billable reasoning is included in max_tokens output ceiling; no separate allowance",
        "cost_upper_bound_usd": cost_upper_bound,
    }


def estimate_request_cost(body: dict[str, Any]) -> float:
    """Conservative serialized-input bound plus max_tokens including reasoning."""
    return float(estimate_request_token_ceilings(body)["cost_upper_bound_usd"])


def preflight_request_batch(requests: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[float], float]:
    """Preflight the one planned request before any provider call; retries are forbidden."""
    validate_resource_config(config)
    caps = config["caps"]
    if len(requests) != MAX_PLANNED_PROVIDER_REQUESTS:
        raise StructuredAnalysisError(
            f"live final mode requires exactly {MAX_PLANNED_PROVIDER_REQUESTS} planned provider request"
        )
    if len(requests) > caps["max_inference_requests"]:
        raise StructuredAnalysisError("planned request batch exceeds the approved inference-request cap")
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
    matched_config, config_hash = _read_approved_resource_config()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "release.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = render_markdown(release)
    (output_dir / "guide.md").write_text(markdown, encoding="utf-8")
    (output_dir / "guide.html").write_text(render_html(markdown, release), encoding="utf-8")
    (output_dir / "evidence-transparency.md").write_text(render_evidence_transparency(release), encoding="utf-8")
    ledger = release["run"]["resource_ledger"]
    manifest = {
        "model": matched_config["model"],
        "provider": matched_config["provider"],
        "sampling": matched_config["sampling"],
        "caps": matched_config["caps"],
        "corpus_snapshot_ids": release["run"]["corpus_snapshot_ids"],
        "evidence_policy": MATCHED_EVIDENCE_POLICY,
        "config_hash": config_hash,
        "execution": {
            "mode": release["run"]["mode"],
            "model": release["run"]["model"],
            "provider": release["run"]["provider"],
            "inference_requests": ledger["inference_requests"],
            "inference_cost_usd_estimate": ledger["inference_cost_usd_estimate"],
            "inference_cost_usd_upper_bound": ledger.get("inference_cost_usd_upper_bound"),
            "publication_status": release["run"]["publication_status"],
            "model_review_status": release["run"]["model_review_status"],
            "model_review_accepted": release["run"]["model_review_accepted"],
        },
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


def _response_from_bytes(
    raw_body: bytes,
    *,
    capture_source: str,
    body_digest_scope: str,
    http_status: int | None = None,
    digest_bytes: bytes | None = None,
    body_bytes_observed: int | None = None,
) -> ProviderResponse:
    truncated = len(raw_body) > MAX_PROVIDER_RESPONSE_BYTES
    retained = raw_body[:MAX_PROVIDER_RESPONSE_BYTES]
    observed = body_bytes_observed if body_bytes_observed is not None else len(raw_body)
    digest_input = digest_bytes if digest_bytes is not None else raw_body
    body_sha256 = _sha256(digest_input)
    parse_error: str | None = None
    payload: Any = None
    if truncated:
        parse_error = f"provider response exceeded the {MAX_PROVIDER_RESPONSE_BYTES}-byte retention limit"
    else:
        try:
            payload = json.loads(retained.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            parse_error = f"provider response JSON could not be parsed ({type(exc).__name__})"
        if parse_error is None and not isinstance(payload, dict):
            parse_error = "provider response JSON root was not an object"
    return ProviderResponse(
        payload=payload,
        raw_body=retained,
        body_bytes_observed=observed,
        body_sha256=body_sha256,
        truncated=truncated,
        capture_source=capture_source,
        body_digest_scope=body_digest_scope,
        parse_error=parse_error,
        http_status=http_status,
    )


def _provider_response_from_adapter(value: Any) -> ProviderResponse:
    if isinstance(value, ProviderResponse):
        return value
    try:
        raw_body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raw_body = repr(value).encode("utf-8", errors="replace")
    captured = _response_from_bytes(
        raw_body,
        capture_source="adapter_object_json_serialization",
        body_digest_scope="complete_adapter_serialization",
    )
    # A permissioned mock adapter already supplied the parsed object; retain that
    # exact object rather than round-tripping it through the diagnostic encoding.
    if not captured.truncated and isinstance(value, dict):
        return ProviderResponse(
            payload=value,
            raw_body=captured.raw_body,
            body_bytes_observed=captured.body_bytes_observed,
            body_sha256=captured.body_sha256,
            truncated=False,
            capture_source=captured.capture_source,
            body_digest_scope=captured.body_digest_scope,
        )
    return captured


def _read_provider_body(response: Any, *, http_status: int | None = None) -> ProviderResponse:
    raw_body = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    return _response_from_bytes(
        raw_body,
        capture_source="http_response_bytes",
        body_digest_scope="complete_response" if len(raw_body) <= MAX_PROVIDER_RESPONSE_BYTES else "observed_prefix_only",
        http_status=http_status,
        digest_bytes=raw_body,
        body_bytes_observed=len(raw_body),
    )


def _provider_response_record(
    response: ProviderResponse | None,
    api_key: str,
    *,
    failure_note: str | None = None,
) -> dict[str, Any]:
    if response is None:
        return {
            "schema_version": "skills-vector.provider-response-evidence.v1",
            "response_available": False,
            "capture_source": "unavailable",
            "retention_status": "unavailable_before_response",
            "body_bytes_observed": 0,
            "captured_bytes": 0,
            "body_complete": False,
            "raw_body_encoding": "base64",
            "raw_body_base64": "",
            "body_sha256": None,
            "body_digest_scope": None,
            "provider_usage": {
                "status": "unavailable",
                "input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
            },
            "usage_note": "No provider response body was received; provider usage is unavailable.",
            "failure_note": failure_note or "Provider call failed before a response body was received.",
            "credential_echo_detected": False,
            "http_status": None,
        }

    raw_body = response.raw_body
    secret = api_key.encode("utf-8") if api_key else b""
    credential_echo = bool(secret and secret in raw_body)
    safe_body = raw_body.replace(secret, b"[REDACTED]") if credential_echo else raw_body
    usage = _reported_usage(response.payload)
    return {
        "schema_version": "skills-vector.provider-response-evidence.v1",
        "response_available": True,
        "capture_source": response.capture_source,
        "retention_status": (
            "bounded_truncated_with_credential_redaction" if response.truncated and credential_echo
            else "bounded_truncated" if response.truncated
            else "credential_echo_redacted" if credential_echo
            else "retained"
        ),
        "body_bytes_observed": response.body_bytes_observed,
        "captured_bytes": len(safe_body),
        "body_complete": not response.truncated,
        "raw_body_encoding": "base64",
        "raw_body_base64": base64.b64encode(safe_body).decode("ascii"),
        "body_sha256": response.body_sha256,
        "retained_body_sha256": _sha256(safe_body),
        "body_digest_scope": response.body_digest_scope,
        "provider_usage": usage,
        "usage_note": (
            "Provider response contained usage metadata; normalized values preserve all valid reported token counts."
            if usage["status"] in {"reported", "partial", "invalid"}
            else "Provider response did not report token usage; no measured usage or usage-based cost is inferred."
        ),
        "parse_error": response.parse_error,
        "credential_echo_detected": credential_echo,
        "credential_echo_handling": "Echoed credential bytes are redacted from retained content; the pre-redaction body digest is retained for integrity diagnostics." if credential_echo else "No exact credential echo was detected in captured response bytes.",
        "http_status": response.http_status,
    }


def _write_provider_response_evidence(
    output_dir: Path,
    response: ProviderResponse | None,
    api_key: str,
    *,
    failure_note: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _provider_response_record(response, api_key, failure_note=failure_note)
    path = output_dir / "provider-response.json"
    _write_json(path, record)
    metadata = {
        "path": path.name,
        "artifact_sha256": _sha256(path.read_bytes()),
        "response_available": record["response_available"],
        "retention_status": record["retention_status"],
        "body_sha256": record["body_sha256"],
        "body_digest_scope": record["body_digest_scope"],
        "body_bytes_observed": record["body_bytes_observed"],
        "captured_bytes": record["captured_bytes"],
        "provider_usage": record["provider_usage"],
        "credential_echo_detected": record["credential_echo_detected"],
    }
    return record, metadata


def _call_provider_once(body: dict[str, Any], api_key: str, opener: Callable[..., Any] | None = None) -> ProviderResponse:
    request = urllib.request.Request(
        LIVE_ENDPOINT,
        data=_serialized_request_bytes(body),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    open_request = opener or urllib.request.urlopen
    # No loop, retry, model escalation, or fixture fallback. The body read is capped.
    try:
        response = open_request(request, timeout=90)
    except urllib.error.HTTPError as error:
        return _read_provider_body(error, http_status=error.code)
    with response:
        return _read_provider_body(response, http_status=getattr(response, "status", None))


def _reported_usage(payload: Any) -> dict[str, Any]:
    empty = {
        "status": "missing",
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
    }
    if not isinstance(payload, dict) or "usage" not in payload or payload["usage"] is None:
        return empty
    raw = payload["usage"]
    if not isinstance(raw, dict):
        return {**empty, "status": "invalid"}

    invalid_value = False
    values: dict[str, int | None] = {}
    for field, key in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
        value = raw.get(key)
        if value is None:
            values[field] = None
        elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values[field] = value
        else:
            values[field] = None
            invalid_value = True

    details = raw.get("completion_tokens_details")
    if details is not None and not isinstance(details, dict):
        invalid_value = True
        details = None
    reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else raw.get("reasoning_tokens")
    if reasoning_tokens is not None and (
        not isinstance(reasoning_tokens, int) or isinstance(reasoning_tokens, bool) or reasoning_tokens < 0
    ):
        reasoning_tokens = None
        invalid_value = True
    values["reasoning_tokens"] = reasoning_tokens

    known_input_output = [values["input_tokens"], values["output_tokens"]]
    if invalid_value:
        status = "invalid"
    elif all(value is not None for value in known_input_output):
        status = "reported"
    elif any(value is not None for value in known_input_output):
        status = "partial"
    else:
        status = "missing"
    return {"status": status, **values}


def _validate_reported_usage_within_ceilings(
    payload: dict[str, Any], usage: dict[str, Any], ceilings: dict[str, Any]
) -> None:
    input_tokens = usage["input_tokens"]
    output_tokens = usage["output_tokens"]
    if input_tokens is not None and input_tokens > ceilings["input_tokens_upper_bound"]:
        raise StructuredAnalysisError("provider-reported input usage exceeded the reserved input-token ceiling")
    output_ceiling = ceilings["output_tokens_upper_bound_including_reasoning"]
    if output_tokens is not None and output_tokens > output_ceiling:
        raise StructuredAnalysisError("provider-reported output usage exceeded max_tokens including reasoning")
    reasoning_tokens = usage["reasoning_tokens"]
    if reasoning_tokens is not None:
        if reasoning_tokens > output_ceiling:
            raise StructuredAnalysisError("provider-reported reasoning usage exceeded max_tokens output ceiling")
        if output_tokens is not None and reasoning_tokens > output_tokens:
            raise StructuredAnalysisError("provider reasoning usage exceeds aggregate completion-token usage")

    raw = payload.get("usage")
    if isinstance(raw, dict) and "total_tokens" in raw:
        total_tokens = raw["total_tokens"]
        total_ceiling = ceilings["total_input_plus_output_including_reasoning_tokens_upper_bound"]
        if isinstance(total_tokens, bool) or not isinstance(total_tokens, int) or total_tokens < 0:
            raise StructuredAnalysisError("provider-reported total token usage is invalid")
        if total_tokens > total_ceiling:
            raise StructuredAnalysisError("provider-reported total usage exceeded input-plus-output ceiling")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _parse_review_content(content: str) -> Any:
    text = content.strip()
    if text.startswith("```") or text.endswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.IGNORECASE | re.DOTALL)
        if match is None:
            raise StructuredAnalysisError("model review code fence is malformed")
        text = match.group(1).strip()
    try:
        return json.loads(text, object_pairs_hook=_unique_json_object)
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise StructuredAnalysisError("single provider response was not valid review JSON") from exc


def validate_live_review(payload: dict[str, Any], release: dict[str, Any], corpus: FrozenCorpus) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise StructuredAnalysisError("provider response must contain exactly one completion choice")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise StructuredAnalysisError("provider completion must contain string message content")
    parsed = _parse_review_content(message["content"])
    if not isinstance(parsed, dict) or set(parsed) != {"reviews"}:
        raise StructuredAnalysisError("model review must be an object containing only the reviews field")

    allowed_citations_by_unit: dict[str, set[tuple[str, str, str]]] = {}
    for unit in release["work_units"]:
        unit_id = unit["unit_id"]
        references = list(unit["evidence"])
        for link in unit["method_fields"]["task_competency_links"]:
            references.extend(link["evidence"])
        allowed_citations_by_unit[unit_id] = {
            (reference["source_id"], reference["locator"], reference["quote"])
            for reference in references
        }

    reviews = parsed["reviews"]
    if not isinstance(reviews, list) or not reviews:
        raise StructuredAnalysisError("single provider response omitted a non-empty reviews array")
    normalized_reviews: list[dict[str, Any]] = []
    seen_units: set[str] = set()
    for index, review in enumerate(reviews):
        if not isinstance(review, dict) or set(review) != {"unit_id", "stance", "reason", "evidence"}:
            raise StructuredAnalysisError(f"model review has an unexpected shape at index {index}")
        unit_id = review["unit_id"]
        if not isinstance(unit_id, str) or unit_id not in allowed_citations_by_unit:
            raise StructuredAnalysisError(f"unresolved model review unit at index {index}")
        if unit_id in seen_units:
            raise StructuredAnalysisError(f"duplicate model review unit at index {index}")
        seen_units.add(unit_id)
        stance = review["stance"]
        if not isinstance(stance, str) or stance not in {"supported", "challenge", "unclear"}:
            raise StructuredAnalysisError(f"invalid model review stance at index {index}")
        reason = review["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1500:
            raise StructuredAnalysisError(f"empty or overlong model review rationale at index {index}")
        evidence = review["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise StructuredAnalysisError(f"missing model review citations at index {index}")
        citations: list[dict[str, str]] = []
        seen_citations: set[tuple[str, str, str]] = set()
        for citation in evidence:
            if not isinstance(citation, dict) or set(citation) != {"source_id", "locator", "quote"}:
                raise StructuredAnalysisError(f"malformed model review citation at index {index}")
            source_id, locator, quote = citation["source_id"], citation["locator"], citation["quote"]
            if (
                not isinstance(source_id, str)
                or source_id not in corpus.source_texts
                or not isinstance(locator, str)
                or not locator.strip()
                or not isinstance(quote, str)
                or not quote
                or quote not in corpus.source_texts[source_id]
            ):
                raise StructuredAnalysisError(f"model review citation does not resolve at index {index}")
            reference = (source_id, locator, quote)
            if reference not in allowed_citations_by_unit[unit_id]:
                raise StructuredAnalysisError(f"model review citation was not supplied for unit {unit_id}")
            if reference in seen_citations:
                raise StructuredAnalysisError(f"duplicate model review citation at index {index}")
            seen_citations.add(reference)
            citations.append({"source_id": source_id, "locator": locator, "quote": quote})
        normalized_reviews.append({
            "unit_id": unit_id,
            "stance": stance,
            "reason": reason,
            "evidence": citations,
        })
    return {
        "status": "untrusted_model_review_not_validation",
        "model": PINNED_MODEL_ID,
        "provider": "deepinfra",
        "review_count": len(normalized_reviews),
        "reviews": normalized_reviews,
        "usage": _reported_usage(payload),
        "notice": "Suggestions are not practitioner validation and are excluded from the occupational backbone and guide.",
    }


def _update_lock(lock_path: Path, update: dict[str, Any]) -> None:
    try:
        current = _read_json(lock_path)
    except StructuredAnalysisError:
        current = {}
    current.update(update)
    _write_json(lock_path, current)


def _set_pipeline_timing(release: dict[str, Any], pipeline_started: float) -> None:
    release["run"]["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    release["run"]["resource_ledger"]["wall_clock_minutes"] = round(
        max(0.0, time.monotonic() - pipeline_started) / 60,
        6,
    )


def run_pipeline(
    *,
    benchmark_dir: Path = DEFAULT_BENCHMARK_DIR,
    base_corpus_dir: Path = DEFAULT_BASE_CORPUS_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    mode: str = "offline",
    resource_config_path: Path | None = None,
    freeze_sha: str | None = None,
    confirm_final_run: bool = False,
    corrective_round_2: bool = False,
    candidate_revision: str | None = None,
    command: str | None = None,
    api_key: str | None = None,
    _live_lock_path: Path | None = None,
    _provider_call: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build local artifacts; live-final adds exactly one guarded model review request."""
    pipeline_started = time.monotonic()
    pipeline_started_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if mode not in {"offline", "live-final"}:
        raise StructuredAnalysisError("mode must be offline or live-final")
    revision = candidate_revision or _git_revision()
    live_lock_path = _live_lock_path or (CORRECTIVE_R2_LIVE_LOCK if corrective_round_2 else DEFAULT_LIVE_LOCK)
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
        resource_config, resource_config_hash = _read_approved_resource_config(resource_config_path)
        if live_lock_path.exists():
            raise StructuredAnalysisError("the one final live execution was already started; no second execution is permitted")
    else:
        if resource_config_path is not None or freeze_sha is not None or confirm_final_run or corrective_round_2:
            raise StructuredAnalysisError("live-only flags cannot be used in offline mode")
        resource_config = None
        resource_config_hash = APPROVED_RESOURCE_CONFIG_SHA256

    corpus = load_frozen_corpus(benchmark_dir, base_corpus_dir)
    release = build_release(corpus, revision, mode=mode)
    release["run"]["started_at"] = pipeline_started_at
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
        "inference_cost_usd_upper_bound": 0.0,
        "measured_provider_cost_usd": None,
        "provider_usage": {"status": "not_applicable", "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None},
        "retrieval_requests": 0,
        "commands_run": [{"command": effective_command, "exit_code": 0, "receipt_path": "run-receipt.json"}],
        "status": "passed_local_build" if mode == "offline" else "preflight_pending",
        "credential_handling": "DEEPINFRA_API_KEY read from environment only in live-final mode; value excluded from prompt, output, logs, and receipt.",
    }

    if mode == "offline":
        _write_release_files(output_dir, release)
        _set_pipeline_timing(release, pipeline_started)
        _write_json(output_dir / "release.json", release)
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
    token_ceilings = [estimate_request_token_ceilings(request) for request in [body]]
    reservation = {
        "status": "reserved_before_provider_call",
        "basis": (
            "whole-run two-input-tokens-per-serialized-byte ceiling plus max_tokens=2048 output ceiling; "
            "reasoning_effort=none is requested and any billable reasoning remains included in max_tokens "
            "with no separate/additive allowance"
        ),
        "planned_request_count": len(token_ceilings),
        "request_token_ceilings": token_ceilings,
        "reserved_cost_upper_bound_usd": total_estimate,
        "approved_cap_usd": min(float(resource_config["caps"]["max_inference_cost_usd"]), MAX_FINAL_SPEND_USD),
        "unused_reservation_released_for_reuse": False,
    }
    receipt["preflight"] = {
        "planned_request_count": len(token_ceilings),
        "per_request_estimates_usd": request_costs,
        "whole_run_cost_upper_bound_usd": total_estimate,
        "whole_run_estimate_upper_bound_usd": total_estimate,
        "request_token_ceilings": token_ceilings,
        "hard_ceiling_usd": MAX_FINAL_SPEND_USD,
        "model": PINNED_MODEL_ID,
        "reasoning_effort": "none",
        "reasoning_accounting": "all billable reasoning is included in the max_tokens=2048 output ceiling",
        "automatic_retry": False,
        "escalation_or_fallback": False,
    }
    receipt["cost_reservation"] = reservation
    release["run"]["model"] = PINNED_MODEL_ID
    release["run"]["provider"] = "deepinfra"
    release["run"]["sampling"] = resource_config["sampling"]
    release["run"]["config_hash"] = resource_config_hash
    release["run"]["resource_caps"] = resource_config["caps"]
    release["run"]["resource_ledger"]["inference_requests"] = 1
    release["run"]["resource_ledger"]["inference_cost_usd_estimate"] = None
    release["run"]["resource_ledger"]["inference_cost_usd_upper_bound"] = total_estimate
    release["run"]["resource_ledger"]["inference_cost_basis"] = "Reserved conservative upper bound from serialized-byte input ceiling and max_tokens output ceiling; not measured or usage-settled cost."
    release["run"]["resource_ledger"]["measured_provider_cost_usd"] = None
    release["run"]["resource_ledger"]["provider_usage"] = {"status": "pending", "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None}
    release["run"]["resource_ledger"]["provider_usage_cost_estimate_usd"] = None
    receipt["config_hash"] = release["run"]["config_hash"]
    receipt["planned_request_count"] = 1
    receipt["inference_cost_usd_estimate"] = None
    receipt["inference_cost_usd_upper_bound"] = total_estimate
    receipt["reserved_cost_upper_bound_usd"] = total_estimate
    receipt["measured_provider_cost_usd"] = None
    receipt["provider_usage"] = {"status": "pending", "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None}
    receipt["provider"] = "deepinfra"
    receipt["model"] = PINNED_MODEL_ID
    receipt["status"] = "started_one_shot_no_retry"

    lock_path = live_lock_path
    lock_metadata = {
        "status": "started",
        "candidate_revision": revision,
        "run_id": release["run"]["run_id"],
        "corpus_manifest_sha256": corpus.benchmark_manifest_sha256,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider_request_count": MAX_PLANNED_PROVIDER_REQUESTS,
        "request_sha256": token_ceilings[0]["request_sha256"],
        "reserved_cost_upper_bound_usd": total_estimate,
        "reservation_status": "reserved_before_provider_call",
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
    provider_response: ProviderResponse | None = None
    response_record: dict[str, Any] | None = None
    response_metadata: dict[str, Any] | None = None
    usage: dict[str, Any] = {"status": "unavailable", "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None}
    accepted_review_path = output_dir / "live-model-review-untrusted.json"
    try:
        adapter_result = (_provider_call or _call_provider_once)(body, api_key)
        provider_response = _provider_response_from_adapter(adapter_result)
        receipt["provider_calls"] = 1
        response_record, response_metadata = _write_provider_response_evidence(output_dir, provider_response, api_key)
        usage = response_record["provider_usage"]
        receipt["provider_usage"] = usage
        receipt["provider_usage_cost_estimate_usd"] = None
        receipt["measured_provider_cost_usd"] = None
        receipt["provider_response"] = response_metadata
        receipt["provider_response_validation"] = "not_started"
        release["run"]["provider_response_evidence"] = response_metadata
        release["run"]["resource_ledger"]["provider_usage"] = usage
        _write_json(output_dir / "run-receipt.json", receipt)

        if usage["status"] == "reported":
            reported_cost_estimate = estimate_cost_usd(
                PINNED_MODELS["extract"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
            receipt["provider_usage_cost_estimate_usd"] = reported_cost_estimate
            receipt["inference_cost_usd_estimate"] = reported_cost_estimate
            release["run"]["resource_ledger"]["provider_usage_cost_estimate_usd"] = reported_cost_estimate
            release["run"]["resource_ledger"]["inference_cost_usd_estimate"] = reported_cost_estimate
            release["run"]["resource_ledger"]["inference_cost_basis"] = "Cost estimate calculated from provider-reported prompt/completion tokens using the pinned public price; not an invoice or measured provider charge."

        if response_record["credential_echo_detected"]:
            raise StructuredAnalysisError("provider response echoed credential material; sanitized raw response retained")
        if provider_response.http_status is not None and not 200 <= provider_response.http_status < 300:
            raise StructuredAnalysisError(f"provider returned HTTP {provider_response.http_status}")
        if provider_response.parse_error is not None or not isinstance(provider_response.payload, dict):
            raise StructuredAnalysisError(provider_response.parse_error or "provider response was not a JSON object")

        _validate_reported_usage_within_ceilings(provider_response.payload, usage, token_ceilings[0])
        if usage["status"] == "reported":
            if reported_cost_estimate > min(float(resource_config["caps"]["max_inference_cost_usd"]), MAX_FINAL_SPEND_USD):
                raise StructuredAnalysisError("provider-reported usage-based cost estimate exceeded the approved whole-run spend cap")
            if reported_cost_estimate > total_estimate:
                raise StructuredAnalysisError("provider-reported usage-based cost estimate exceeded the reserved upper bound")

        receipt["provider_response_validation"] = "raw_response_and_usage_retained_before_semantic_validation"
        review = validate_live_review(provider_response.payload, release, corpus)
        review["provider_response_evidence"] = response_metadata
        review["semantic_validation"] = "accepted_strict_shape_citations_and_references"
        _write_json(accepted_review_path, review)

        release["run"]["model_review_status"] = "accepted"
        release["run"]["model_review_accepted"] = True
        release["run"]["publication_status"] = "model_reviewed_release"
        release["run"]["execution_label"] = "deterministic offline guide plus one accepted strict model review; suggestions remain untrusted and do not change guide claims"
        receipt["provider_review_status"] = "accepted_strict_semantics"
        if usage["status"] == "reported":
            receipt["status"] = "passed_one_live_review"
            reservation["status"] = "reconciled_from_provider_reported_usage_estimate"
            _update_lock(lock_path, {"status": "completed", "provider_usage_status": usage["status"], "finished_at": datetime.now(UTC).isoformat(timespec="seconds")})
        else:
            receipt["status"] = "passed_one_live_review_usage_unreported" if usage["status"] == "missing" else "passed_one_live_review_usage_incomplete"
            receipt["usage_note"] = "Strict model review accepted; response retained, but complete provider usage is unavailable, so no usage-based cost estimate is inferred and the conservative reservation remains consumed."
            reservation["status"] = "consumed_usage_unreported"
            _update_lock(lock_path, {"status": "completed_usage_unreported", "provider_usage_status": usage["status"], "finished_at": datetime.now(UTC).isoformat(timespec="seconds")})
        receipt["cost_reservation"] = reservation
        release["run"]["resource_ledger"]["inference_cost_usd_reservation_status"] = reservation["status"]
        receipt["provider_response_validation"] = "accepted_strict_semantics"
        _set_pipeline_timing(release, pipeline_started)
    except BaseException as exc:
        sanitized_failure = _sanitized_failure(exc, api_key)
        if response_record is None:
            response_record, response_metadata = _write_provider_response_evidence(
                output_dir,
                None,
                api_key,
                failure_note="Provider call failed before a response body was received; no raw response or usage was available.",
            )
            usage = response_record["provider_usage"]
            receipt["provider_usage"] = usage
            receipt["provider_response"] = response_metadata
            release["run"]["provider_response_evidence"] = response_metadata
            release["run"]["resource_ledger"]["provider_usage"] = usage
        receipt["provider_calls"] = 1
        receipt["status"] = "failed_after_one_shot_consumed"
        receipt["failure"] = sanitized_failure
        receipt["provider_review_status"] = "unavailable" if not response_record["response_available"] else "rejected"
        receipt["provider_response_validation"] = "unavailable_before_response" if not response_record["response_available"] else "rejected"
        receipt["commands_run"][0]["exit_code"] = 2
        release["acceptance"]["commands_run"][0]["exit_code"] = 2
        release["run"]["model_review_status"] = "unavailable_before_response" if not response_record["response_available"] else "rejected"
        release["run"]["model_review_accepted"] = False
        release["run"]["publication_status"] = "offline_guide_after_failed_live_review" if not response_record["response_available"] else "offline_guide_after_rejected_review"
        release["run"]["execution_label"] = "deterministic offline guide retained; live model review failed or was rejected and is not published as reviewed"
        reservation["status"] = "consumed_after_provider_failure"
        receipt["cost_reservation"] = reservation
        release["run"]["resource_ledger"]["inference_cost_usd_reservation_status"] = reservation["status"]
        _set_pipeline_timing(release, pipeline_started)
        accepted_review_path.unlink(missing_ok=True)
        _write_release_files(output_dir, release)
        _update_lock(lock_path, {
            "status": "failed_consumed_no_retry",
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "failure": sanitized_failure,
            "provider_usage_status": usage["status"],
            "provider_response_available": bool(response_record["response_available"]),
        })
        _write_json(output_dir / "run-receipt.json", receipt)
        raise StructuredAnalysisError(sanitized_failure) from None

    _write_release_files(output_dir, release)
    receipt["commands_run"] = [{"command": effective_command, "exit_code": 0, "receipt_path": "run-receipt.json"}]
    _write_json(output_dir / "run-receipt.json", receipt)
    return receipt

