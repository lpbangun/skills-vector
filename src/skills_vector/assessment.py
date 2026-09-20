"""Local Jobsss assessment: occupational requirements vs personal evidence.

This module never stores personal evidence in the research catalog.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .catalog import CatalogStore
from .occupational import (
    AssessmentItem,
    AssessmentOutcome,
    AssessmentResult,
    PracticeTask,
    ProficiencyLevel,
    RoleRequirement,
)


LEVEL_RANK = {
    ProficiencyLevel.AWARENESS: 1,
    ProficiencyLevel.WORKING: 2,
    ProficiencyLevel.INDEPENDENT: 3,
    ProficiencyLevel.LEADING: 4,
}


RELATED = {
    "skl_python": ("skl_software",),
    "skl_sql": ("skl_analytics",),
}


def assess(
    store: CatalogStore,
    occupation_id: str,
    *,
    personal_evidence: tuple[dict, ...],
    resume_keywords: tuple[str, ...] = (),
    previous: AssessmentResult | None = None,
    benchmark_id: str,
    benchmark_version: str,
) -> AssessmentResult:
    requirements = store.requirements(occupation_id)
    evidence_by_skill = {item["skill_id"]: item for item in personal_evidence if "skill_id" in item}
    keyword_set = {keyword.casefold() for keyword in resume_keywords}
    items: list[AssessmentItem] = []
    for requirement in requirements:
        evidence = evidence_by_skill.get(requirement.skill_id)
        rubric = next((item for item in store.rubrics() if item.rubric_id == requirement.rubric_id), None)
        rubric_version = rubric.version if rubric else 0
        if evidence is None:
            transfer = _transfer(requirement, evidence_by_skill)
            if transfer:
                items.append(
                    AssessmentItem(
                        requirement.requirement_id,
                        requirement.skill_id,
                        AssessmentOutcome.POTENTIAL_TRANSFER,
                        "A related capability is documented; it still needs testing in this occupation's context.",
                        benchmark_version,
                        rubric_version,
                        PracticeTask(
                            f"Test transfer of {requirement.skill_id} in the target context",
                            (transfer,),
                            "Complete a scoped task using the target tools and constraints.",
                            "Work sample reviewed against the pinned rubric.",
                        ),
                    )
                )
                continue
            missing_keyword = requirement.skill_id.removeprefix("skl_").replace("_", " ")
            reason = "Insufficient comparable evidence for this requirement."
            if missing_keyword in keyword_set or requirement.skill_id.removeprefix("skl_") in keyword_set:
                reason += " A résumé keyword is present and was not treated as a gap."
            items.append(
                AssessmentItem(
                    requirement.requirement_id,
                    requirement.skill_id,
                    AssessmentOutcome.UNKNOWN,
                    reason,
                    benchmark_version,
                    rubric_version,
                    PracticeTask(
                        f"Gather evidence for {requirement.skill_id}",
                        (),
                        "Produce a comparable work sample or observation at the target level.",
                        "Artifact mapped to this requirement and rubric version.",
                    ),
                )
            )
            continue
        observed = ProficiencyLevel(evidence["level"])
        target = requirement.target_level
        if LEVEL_RANK[observed] >= LEVEL_RANK[target]:
            outcome = AssessmentOutcome.EXCEEDS if LEVEL_RANK[observed] > LEVEL_RANK[target] else AssessmentOutcome.MEETS
            items.append(
                AssessmentItem(
                    requirement.requirement_id,
                    requirement.skill_id,
                    outcome,
                    "Comparable evidence supports the required level in this context.",
                    benchmark_version,
                    rubric_version,
                )
            )
            continue
        items.append(
            AssessmentItem(
                requirement.requirement_id,
                requirement.skill_id,
                AssessmentOutcome.GAP,
                "Sufficient comparable evidence indicates performance below the target level.",
                benchmark_version,
                rubric_version,
                PracticeTask(
                    f"Practice {requirement.skill_id} to {target.value}",
                    (f"current:{observed.value}",),
                    (rubric.levels.get(target.value) if rubric else "Meet the pinned rubric level."),
                    "Before/after work sample against the same rubric version.",
                ),
            )
        )
    if previous:
        items = [_annotate_cause(item, previous) for item in items]
    return AssessmentResult(benchmark_id, benchmark_version, "rubric-set.v1", tuple(items), datetime.now(UTC), True)


def _transfer(requirement: RoleRequirement, evidence_by_skill: dict[str, dict]) -> str | None:
    for related in RELATED.get(requirement.skill_id, ()):
        if related in evidence_by_skill:
            return related
    return None


def _annotate_cause(item: AssessmentItem, previous: AssessmentResult) -> AssessmentItem:
    prior = next((old for old in previous.items if old.requirement_id == item.requirement_id), None)
    if prior is None:
        return item
    if prior.outcome is item.outcome:
        return item
    cause = "new_requirements" if prior.benchmark_version != item.benchmark_version else "personal_evidence"
    return AssessmentItem(
        item.requirement_id,
        item.skill_id,
        item.outcome,
        item.reason + f" Outcome changed because of {cause.replace('_', ' ')}.",
        item.benchmark_version,
        item.rubric_version,
        item.recommendation,
        cause,
    )
