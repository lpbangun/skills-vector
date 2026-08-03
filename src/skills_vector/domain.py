"""Typed product invariants for the first Skills Vector role brief."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from urllib.parse import urlparse


class Role(StrEnum):
    HR_COORDINATOR = "hr_coordinator"
    RECRUITER = "recruiter"
    LEARNING_AND_DEVELOPMENT_SPECIALIST = "learning_and_development_specialist"


APPROVED_ROLES = frozenset(Role)


class EvidenceCategory(StrEnum):
    PUBLIC_LABOR_DATA = "public_labor_data"
    RESEARCH_PAPER = "research_paper"
    CREDIBLE_REPORT = "credible_report"
    JOB_POSTING_SIGNAL = "job_posting_signal"
    OFFICIAL_POLICY = "official_policy"


class ClaimKind(StrEnum):
    CHANGE = "change"
    DURABLE_CAPABILITY = "durable_capability"
    UNCERTAINTY = "uncertainty"


class ClaimImpact(StrEnum):
    ROUTINE = "routine"
    HIGH = "high"


class ScenarioHorizon(StrEnum):
    NEAR_TERM = "0_to_12_months"
    MEDIUM_TERM = "12_to_36_months"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RoleBriefRequest:
    role: Role
    as_of: date
    geography: str = "US"
    domain: str = "people_operations_and_talent"

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role) or self.role not in APPROVED_ROLES:
            raise ValueError("role is outside the approved pilot")
        if self.geography != "US":
            raise ValueError("the MVP supports U.S. evidence only")
        if self.domain != "people_operations_and_talent":
            raise ValueError("the MVP is limited to People Operations & Talent")


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    source_id: str
    category: EvidenceCategory
    title: str
    publisher: str
    url: str
    published_on: date | None
    retrieved_at: datetime
    geography: str = "US"

    def __post_init__(self) -> None:
        if not isinstance(self.category, EvidenceCategory):
            raise ValueError("evidence category is not approved for this MVP")
        required = {
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
        }
        blank = [name for name, value in required.items() if not value.strip()]
        if blank:
            raise ValueError(f"blank evidence fields: {', '.join(blank)}")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("evidence must retain a valid source URL")
        if self.geography != "US":
            raise ValueError("evidence must be applicable to the U.S. pilot")


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    kind: ClaimKind
    statement: str
    evidence_ids: tuple[str, ...]
    impact: ClaimImpact = ClaimImpact.ROUTINE
    uncertainty_note: str = ""
    disagreement_note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ClaimKind):
            raise ValueError("claim kind is not part of the role-brief contract")
        if not isinstance(self.impact, ClaimImpact):
            raise ValueError("claim impact must use the controlled vocabulary")
        if not self.claim_id.strip() or not self.statement.strip():
            raise ValueError("claims require an id and statement")
        if not self.evidence_ids:
            raise ValueError("claims require at least one source")
        if not self.uncertainty_note.strip():
            raise ValueError("claims must make uncertainty visible")


@dataclass(frozen=True, slots=True)
class Scenario:
    horizon: ScenarioHorizon
    description: str
    evidence_ids: tuple[str, ...]
    uncertainty_note: str

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, ScenarioHorizon):
            raise ValueError("scenario horizon must be time-bound")
        if not self.description.strip() or not self.uncertainty_note.strip():
            raise ValueError("scenarios require a description and uncertainty")
        if not self.evidence_ids:
            raise ValueError("scenarios require evidence")


@dataclass(frozen=True, slots=True)
class HumanReview:
    decision: ReviewDecision
    reviewer: str
    reviewed_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ReviewDecision):
            raise ValueError("review decision must use the controlled vocabulary")
        if not self.reviewer.strip():
            raise ValueError("human review requires a reviewer")


@dataclass(frozen=True, slots=True)
class RoleBrief:
    request: RoleBriefRequest
    summary: str
    claims: tuple[Claim, ...]
    scenarios: tuple[Scenario, ...]
    sources: tuple[EvidenceSource, ...]
    review: HumanReview | None = None
    private: bool = True
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("a role brief requires a summary")
        errors = validate_brief(self)
        if errors:
            raise ValueError("; ".join(errors))

    @property
    def is_publication_ready(self) -> bool:
        return bool(
            self.review
            and self.review.decision is ReviewDecision.APPROVED
            and not self.private
        )


def validate_brief(brief: RoleBrief) -> tuple[str, ...]:
    """Return product-rule violations without making any external calls."""

    errors: list[str] = []
    sources_by_id = {source.source_id: source for source in brief.sources}
    if len(sources_by_id) != len(brief.sources):
        errors.append("evidence source ids must be unique")

    required_claim_kinds = {
        ClaimKind.CHANGE,
        ClaimKind.DURABLE_CAPABILITY,
        ClaimKind.UNCERTAINTY,
    }
    present_claim_kinds = {claim.kind for claim in brief.claims}
    missing_claim_kinds = required_claim_kinds - present_claim_kinds
    if missing_claim_kinds:
        errors.append(
            "role brief is missing required claim sections: "
            + ", ".join(sorted(kind.value for kind in missing_claim_kinds))
        )
    if not brief.scenarios:
        errors.append("role brief requires at least one time-bound scenario")

    referenced: list[tuple[str, tuple[str, ...]]] = [
        *((claim.claim_id, claim.evidence_ids) for claim in brief.claims),
        *((f"scenario:{scenario.horizon}", scenario.evidence_ids) for scenario in brief.scenarios),
    ]
    for item_id, evidence_ids in referenced:
        missing = sorted(set(evidence_ids) - sources_by_id.keys())
        if missing:
            errors.append(f"{item_id} references missing evidence: {', '.join(missing)}")

    for claim in brief.claims:
        if claim.impact is not ClaimImpact.HIGH:
            continue
        publishers = {
            sources_by_id[source_id].publisher.casefold()
            for source_id in claim.evidence_ids
            if source_id in sources_by_id
        }
        if len(publishers) < 2 and not claim.disagreement_note.strip():
            errors.append(
                f"high-impact claim {claim.claim_id} needs independent corroboration "
                "or a visible disagreement note"
            )

    if not brief.private and (
        brief.review is None or brief.review.decision is not ReviewDecision.APPROVED
    ):
        errors.append("a non-private brief requires explicit human approval")

    return tuple(errors)
