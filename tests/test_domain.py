from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from skills_vector.domain import (
    Claim,
    ClaimImpact,
    ClaimKind,
    EvidenceCategory,
    EvidenceSource,
    HumanReview,
    ReviewDecision,
    Role,
    RoleBrief,
    RoleBriefRequest,
    Scenario,
    ScenarioHorizon,
)


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


def source(source_id: str, publisher: str = "BLS") -> EvidenceSource:
    return EvidenceSource(
        source_id=source_id,
        category=EvidenceCategory.PUBLIC_LABOR_DATA,
        title="Occupational data",
        publisher=publisher,
        url=f"https://example.gov/{source_id}",
        published_on=date(2026, 1, 1),
        retrieved_at=NOW,
    )


def claim(**overrides: object) -> Claim:
    values: dict[str, object] = {
        "claim_id": "c1",
        "kind": ClaimKind.CHANGE,
        "statement": "A sourced role-level observation.",
        "evidence_ids": ("s1",),
        "uncertainty_note": "The signal may not generalize across employers.",
    }
    values.update(overrides)
    return Claim(**values)  # type: ignore[arg-type]


def brief(**overrides: object) -> RoleBrief:
    values: dict[str, object] = {
        "request": RoleBriefRequest(Role.RECRUITER, date(2026, 8, 3)),
        "summary": "Private evidence-led draft.",
        "claims": (
            claim(),
            claim(
                claim_id="c2",
                kind=ClaimKind.DURABLE_CAPABILITY,
                statement="A durable capability supported by the evidence.",
            ),
            claim(
                claim_id="c3",
                kind=ClaimKind.UNCERTAINTY,
                statement="A material uncertainty in the available evidence.",
            ),
        ),
        "scenarios": (
            Scenario(
                horizon=ScenarioHorizon.NEAR_TERM,
                description="A role-level near-term scenario.",
                evidence_ids=("s1",),
                uncertainty_note="Timing varies by employer and sector.",
            ),
        ),
        "sources": (source("s1"),),
    }
    values.update(overrides)
    return RoleBrief(**values)  # type: ignore[arg-type]


class DomainContractTests(unittest.TestCase):
    def test_scope_is_us_people_operations_and_talent(self) -> None:
        with self.assertRaisesRegex(ValueError, "U.S."):
            RoleBriefRequest(Role.RECRUITER, date(2026, 8, 3), geography="CA")
        with self.assertRaisesRegex(ValueError, "People Operations"):
            RoleBriefRequest(Role.RECRUITER, date(2026, 8, 3), domain="software")

    def test_only_three_roles_can_be_constructed(self) -> None:
        self.assertEqual(len(Role), 3)
        with self.assertRaises(ValueError):
            Role("software_engineer")

    def test_claims_require_sources_and_uncertainty(self) -> None:
        with self.assertRaisesRegex(ValueError, "source"):
            claim(evidence_ids=())
        with self.assertRaisesRegex(ValueError, "uncertainty"):
            claim(uncertainty_note="")

    def test_unapproved_evidence_category_is_rejected_at_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "not approved"):
            EvidenceSource(
                source_id="benchmark",
                category="model_benchmark_news",  # type: ignore[arg-type]
                title="A model benchmark headline",
                publisher="Example",
                url="https://example.com/benchmark",
                published_on=date(2026, 1, 1),
                retrieved_at=NOW,
            )

    def test_missing_source_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            brief(
                claims=(
                    claim(evidence_ids=("absent",)),
                    claim(claim_id="c2", kind=ClaimKind.DURABLE_CAPABILITY),
                    claim(claim_id="c3", kind=ClaimKind.UNCERTAINTY),
                )
            )

    def test_high_impact_claim_requires_corroboration_or_disagreement(self) -> None:
        with self.assertRaisesRegex(ValueError, "independent corroboration"):
            brief(
                claims=(
                    claim(impact=ClaimImpact.HIGH),
                    claim(claim_id="c2", kind=ClaimKind.DURABLE_CAPABILITY),
                    claim(claim_id="c3", kind=ClaimKind.UNCERTAINTY),
                )
            )

        corroborated = brief(
            claims=(
                claim(impact=ClaimImpact.HIGH, evidence_ids=("s1", "s2")),
                claim(claim_id="c2", kind=ClaimKind.DURABLE_CAPABILITY),
                claim(claim_id="c3", kind=ClaimKind.UNCERTAINTY),
            ),
            sources=(source("s1"), source("s2", publisher="Census Bureau")),
        )
        self.assertTrue(corroborated.private)

        disputed = brief(
            claims=(
                claim(impact=ClaimImpact.HIGH, disagreement_note="Evidence conflicts."),
                claim(claim_id="c2", kind=ClaimKind.DURABLE_CAPABILITY),
                claim(claim_id="c3", kind=ClaimKind.UNCERTAINTY),
            )
        )
        self.assertTrue(disputed.private)

    def test_complete_brief_requires_each_output_section(self) -> None:
        with self.assertRaisesRegex(ValueError, "durable_capability"):
            brief(
                claims=(
                    claim(),
                    claim(claim_id="c3", kind=ClaimKind.UNCERTAINTY),
                )
            )
        with self.assertRaisesRegex(ValueError, "time-bound scenario"):
            brief(scenarios=())

    def test_non_private_brief_requires_human_approval(self) -> None:
        with self.assertRaisesRegex(ValueError, "human approval"):
            brief(private=False)

        approved = brief(
            private=False,
            review=HumanReview(ReviewDecision.APPROVED, "reviewer@example.com", NOW),
        )
        self.assertTrue(approved.is_publication_ready)


if __name__ == "__main__":
    unittest.main()
