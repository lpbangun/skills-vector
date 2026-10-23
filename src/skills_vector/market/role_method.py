"""Canonical Method vocabulary; source timing is policy, never a selected-skill reading."""
from .role_atlas import SOURCE_RULES

SOURCE_GUIDE = {
    "postings": ("Job postings", "Current recorded demand in the admitted sample, by level and sector."),
    "postings_new_phrasing": ("New phrasing in postings", "Early wording changes; small samples may be noise."),
    "onet_ratings": ("O*NET ratings", "The durable task and proficiency bar; not current posting growth."),
    "onet_emerging_tasks": ("O*NET Emerging Tasks", "New worker-reported tasks in the official record before ratings change."),
    "onet_hot_technologies": ("O*NET Hot Technologies", "Named technologies derived from postings; presence is not a growth series."),
    "esco": ("ESCO", "Occupation and skill links; a lagging official map."),
    "hiring_lab": ("Indeed Hiring Lab", "Independent sector-level posting trends, not individual proficiency."),
    "economic_index": ("Economic Index", "Task-level AI-use context, not an employer hiring requirement."),
    "bls_projections": ("BLS projections", "Long-term occupational headcount context, not skill demand."),
    "bls_industry_matrix": ("BLS occupation × industry matrix", "Lagging employment context within native industry rows."),
    "careeronestop": ("CareerOneStop certifications", "Credential catalog membership, not proof of employer requirements."),
}


def method_policy() -> dict:
    return {"window_length": "quarterly", "forecast_horizon_quarters": 4,
            "sources": [{"source_kind": kind, "source": SOURCE_GUIDE[kind][0], "timing": timing,
                         "family": family, "good_for": SOURCE_GUIDE[kind][1]}
                        for kind, (timing, family) in SOURCE_RULES.items()]}
