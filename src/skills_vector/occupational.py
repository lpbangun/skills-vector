"""Occupational skills, evidence, and assessment contracts.

This is the product data model for U.S. startup roles. The People Operations
role-brief types in ``domain.py`` remain the earlier investigation experiment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from urllib.parse import urlparse


SCHEMA_VERSION = "skills-vector.occupational.v1"
PARSER_VERSION = "retrieval.v1"
PROMPT_VERSIONS = {
    "extract": "extract.v1",
    "reconcile": "reconcile.v1",
    "challenge": "challenge.v1",
}


class JobFamily(StrEnum):
    ENGINEERING_AI = "engineering_ai"
    PRODUCT_DESIGN = "product_design"
    GO_TO_MARKET_OPERATIONS = "go_to_market_operations"


class OccupationId(StrEnum):
    FOUNDING_ENGINEER = "occ_founding_engineer"
    PRODUCT_MANAGER = "occ_product_manager"
    GROWTH_OPERATOR = "occ_growth_operator"


PILOT_OCCUPATIONS = frozenset(OccupationId)

FAMILY_BY_OCCUPATION = {
    OccupationId.FOUNDING_ENGINEER: JobFamily.ENGINEERING_AI,
    OccupationId.PRODUCT_MANAGER: JobFamily.PRODUCT_DESIGN,
    OccupationId.GROWTH_OPERATOR: JobFamily.GO_TO_MARKET_OPERATIONS,
}

ONET_BASELINE = {
    OccupationId.FOUNDING_ENGINEER: ("15-1252.00", "Software Developers"),
    OccupationId.PRODUCT_MANAGER: ("15-1299.09", "Information Technology Project Managers"),
    OccupationId.GROWTH_OPERATOR: ("13-1161.00", "Market Research Analysts and Marketing Specialists"),
}


class RecordState(StrEnum):
    DRAFT = "draft"
    PROVISIONAL = "provisional"
    REVIEWED = "reviewed"
    SUPERSEDED = "superseded"
    REMOVED = "removed"
    REJECTED = "rejected"


class EvidenceWeight(StrEnum):
    """How a source may be used. Job ads never prove actual work."""

    OCCUPATIONAL_BASELINE = "occupational_baseline"
    FIRST_PARTY_JOB = "first_party_job"
    STATED_DEMAND = "stated_demand"
    PRACTITIONER_INPUT = "practitioner_input"
    EMERGING_PRACTICE = "emerging_practice"
    OFFICIAL_POLICY = "official_policy"
    RESEARCH_PUBLICATION = "research_publication"


class SourceKind(StrEnum):
    ONET = "onet"
    ESCO = "esco"
    FIRST_PARTY_JOB = "first_party_job"
    JOB_ADVERTISEMENT = "job_advertisement"
    PRACTITIONER_NOTE = "practitioner_note"
    SOCIAL_POST = "social_post"
    OFFICIAL_POLICY = "official_policy"
    RESEARCH_PUBLICATION = "research_publication"
    OFFLINE_FIXTURE = "offline_fixture"


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ProficiencyLevel(StrEnum):
    AWARENESS = "1_awareness"
    WORKING = "2_working"
    INDEPENDENT = "3_independent"
    LEADING = "4_leading"


class AssessmentOutcome(StrEnum):
    UNKNOWN = "unknown"
    GAP = "gap"
    MEETS = "meets"
    EXCEEDS = "exceeds"
    POTENTIAL_TRANSFER = "potential_transfer"


class ChangeKind(StrEnum):
    ADDED = "added"
    UPDATED = "updated"
    SUPERSEDED = "superseded"
    REMOVED = "removed"


class RunPhase(StrEnum):
    COLLECT = "collect"
    EXTRACT = "extract"
    RECONCILE = "reconcile"
    CHALLENGE = "challenge"
    REVIEW = "review"
    RELEASE = "release"


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"
    APPROVED = "approved"
    RELEASED = "released"
    QUEUED_BUDGET = "queued_budget"
    FAILED = "failed"


def _require_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "fixture"} or not parsed.netloc:
        raise ValueError("source must retain a valid URL or labeled fixture URI")


@dataclass(frozen=True, slots=True)
class Occupation:
    occupation_id: str
    slug: str
    title: str
    family: JobFamily
    geography: str = "US"
    onet_code: str | None = None
    onet_title: str | None = None
    esco_code: str | None = None
    aliases: tuple[str, ...] = ()
    state: RecordState = RecordState.PROVISIONAL
    version: int = 1


@dataclass(frozen=True, slots=True)
class WorkContext:
    context_id: str
    occupation_id: str
    startup_stage: str
    industry: str = "software"
    geography: str = "US"
    seniority: str = "ic"
    autonomy: str = "high"
    tools: tuple[str, ...] = ()
    working_conditions: str = "small_team"
    organizational_scope: str = "company_wide"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    occupation_id: str
    statement: str
    output: str
    success_criteria: str
    frequency: str = "recurring"
    criticality: str = "core"
    dependencies: tuple[str, ...] = ()
    context_id: str | None = None
    state: RecordState = RecordState.PROVISIONAL
    version: int = 1


@dataclass(frozen=True, slots=True)
class Skill:
    skill_id: str
    name: str
    description: str
    onet_element_id: str | None = None
    esco_uri: str | None = None
    state: RecordState = RecordState.PROVISIONAL
    version: int = 1


@dataclass(frozen=True, slots=True)
class TaskSkillLink:
    task_id: str
    skill_id: str
    relationship: str = "required"
    note: str = ""


@dataclass(frozen=True, slots=True)
class ProficiencyRubric:
    rubric_id: str
    skill_id: str
    occupation_id: str
    version: int
    levels: dict[str, str]
    provisional: bool = True


@dataclass(frozen=True, slots=True)
class RoleRequirement:
    requirement_id: str
    occupation_id: str
    skill_id: str
    task_ids: tuple[str, ...]
    target_level: ProficiencyLevel
    rubric_id: str
    context_id: str | None = None
    state: RecordState = RecordState.PROVISIONAL
    version: int = 1


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    kind: SourceKind
    weight: EvidenceWeight
    title: str
    publisher: str
    url: str
    retrieved_at: datetime
    content_hash: str
    parser_version: str = PARSER_VERSION
    published_on: date | None = None
    rights: str = "retain_attribution"
    fixture: bool = False
    retrieval_ok: bool = True
    failure_note: str = ""

    def __post_init__(self) -> None:
        _require_url(self.url)
        if not self.source_id.strip() or not self.title.strip() or not self.publisher.strip():
            raise ValueError("sources require id, title, and publisher")
        if self.kind is SourceKind.OFFLINE_FIXTURE and not self.fixture:
            raise ValueError("offline fixtures must be labeled fixture=True")
        if self.kind is SourceKind.JOB_ADVERTISEMENT and self.weight is not EvidenceWeight.STATED_DEMAND:
            raise ValueError("job advertisements are stated demand, not proof of work")
        if self.kind is SourceKind.SOCIAL_POST and self.weight is not EvidenceWeight.EMERGING_PRACTICE:
            raise ValueError("social posts can only suggest emerging practice")


@dataclass(frozen=True, slots=True)
class Passage:
    passage_id: str
    source_id: str
    locator: str
    text: str
    applicable_context: str = "US startup"


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    occupation_id: str
    statement: str
    claim_type: str
    passage_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    status: ClaimStatus = ClaimStatus.PROPOSED
    uncertainty_note: str = ""
    disagreement_note: str = ""
    model_agreement: bool = False
    independent_corroboration: bool = False
    prompt_version: str = ""
    model_id: str = ""


@dataclass(frozen=True, slots=True)
class HumanReview:
    decision: ReviewDecision
    reviewer: str
    reviewed_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not self.reviewer.strip():
            raise ValueError("human review requires a reviewer")


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    change_id: str
    entity_type: str
    entity_id: str
    kind: ChangeKind
    occurred_at: datetime
    supersedes: str | None = None
    payload_hash: str = ""


@dataclass(frozen=True, slots=True)
class PracticeTask:
    title: str
    prerequisites: tuple[str, ...]
    success_criteria: str
    expected_evidence: str


@dataclass(frozen=True, slots=True)
class AssessmentItem:
    requirement_id: str
    skill_id: str
    outcome: AssessmentOutcome
    reason: str
    benchmark_version: str
    rubric_version: int
    recommendation: PracticeTask | None = None
    cause: str = "personal_evidence"


@dataclass(frozen=True, slots=True)
class AssessmentResult:
    benchmark_id: str
    benchmark_version: str
    rubric_set_version: str
    items: tuple[AssessmentItem, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    private: bool = True


def validate_claim_support(claim: Claim, passages: dict[str, Passage], sources: dict[str, SourceRecord]) -> tuple[str, ...]:
    errors: list[str] = []
    if not claim.passage_ids:
        errors.append(f"{claim.claim_id} has no supporting passages")
    missing_passages = sorted(set(claim.passage_ids) - passages.keys())
    if missing_passages:
        errors.append(f"{claim.claim_id} references missing passages: {', '.join(missing_passages)}")
    missing_sources = sorted(set(claim.source_ids) - sources.keys())
    if missing_sources:
        errors.append(f"{claim.claim_id} references missing sources: {', '.join(missing_sources)}")
    for passage_id in claim.passage_ids:
        passage = passages.get(passage_id)
        if passage and passage.source_id not in claim.source_ids:
            errors.append(f"{claim.claim_id} passage {passage_id} is not tied to a cited source")
        if passage and passage.text.strip() and passage.text.strip() not in _joined_statement_space(claim):
            if not _passage_supports_statement(claim.statement, passage.text):
                errors.append(f"{claim.claim_id} is not supported by passage {passage_id}")
    if claim.model_agreement and claim.independent_corroboration:
        errors.append(f"{claim.claim_id} treats model agreement as independent corroboration")
    job_ad_only = bool(claim.source_ids) and all(
        sources[sid].kind is SourceKind.JOB_ADVERTISEMENT for sid in claim.source_ids if sid in sources
    )
    if job_ad_only and claim.claim_type == "requirement":
        errors.append(f"{claim.claim_id} cannot establish a requirement from job advertisements alone")
    social_only = bool(claim.source_ids) and all(
        sources[sid].kind is SourceKind.SOCIAL_POST for sid in claim.source_ids if sid in sources
    )
    if social_only and claim.claim_type == "requirement":
        errors.append(f"{claim.claim_id} cannot establish a requirement from social posts alone")
    return tuple(errors)


def _joined_statement_space(claim: Claim) -> str:
    return claim.statement.casefold()


def _passage_supports_statement(statement: str, passage: str) -> bool:
    tokens = [token for token in statement.casefold().replace("/", " ").split() if len(token) > 4]
    if not tokens:
        return True
    haystack = passage.casefold()
    overlap = sum(1 for token in tokens if token in haystack)
    return overlap >= max(1, len(tokens) // 4)
