"""Checked, normalized public evidence bundled with the offline MVP."""

from __future__ import annotations

from .domain import EvidenceCategory, Role


ALL_ROLES = tuple(Role)
HR_ROLES = (Role.HR_COORDINATOR, Role.RECRUITER)


SEED_EVIDENCE: tuple[dict[str, object], ...] = (
    {
        "source_id": "bls-human-resources-specialists-2025",
        "category": EvidenceCategory.PUBLIC_LABOR_DATA,
        "title": "Human Resources Specialists — Occupational Outlook Handbook",
        "publisher": "U.S. Bureau of Labor Statistics",
        "url": "https://www.bls.gov/ooh/business-and-financial/human-resources-specialists.htm",
        "published_on": "2025-08-28",
        "relevant_excerpt": "BLS describes recruiting, screening, interviewing, placement, benefits, training, and employee-relations duties and projects 6% U.S. employment growth from 2024 to 2034.",
        "provenance": "Bundled from the public BLS Occupational Outlook Handbook; last-modified date and claim retained from the source page.",
        "role_connections": tuple(role.value for role in HR_ROLES),
    },
    {
        "source_id": "bls-training-development-specialists-2025",
        "category": EvidenceCategory.PUBLIC_LABOR_DATA,
        "title": "Training and Development Specialists — Occupational Outlook Handbook",
        "publisher": "U.S. Bureau of Labor Statistics",
        "url": "https://www.bls.gov/ooh/business-and-financial/training-and-development-specialists.htm",
        "published_on": "2025-08-28",
        "relevant_excerpt": "BLS says these specialists plan and administer employee skill and knowledge programs and projects 11% U.S. employment growth from 2024 to 2034.",
        "provenance": "Bundled from the public BLS Occupational Outlook Handbook; last-modified date and claim retained from the source page.",
        "role_connections": (Role.LEARNING_AND_DEVELOPMENT_SPECIALIST.value,),
    },
    {
        "source_id": "nber-generative-ai-at-work-2023",
        "category": EvidenceCategory.RESEARCH_PAPER,
        "title": "Generative AI at Work",
        "publisher": "National Bureau of Economic Research",
        "url": "https://www.nber.org/papers/w31161",
        "published_on": "2023-04-01",
        "relevant_excerpt": "A field study of 5,179 support agents found heterogeneous productivity gains from AI assistance, with larger gains for less-experienced workers.",
        "provenance": "Bundled metadata and abstract finding from NBER Working Paper 31161; the studied occupation is not an HR occupation.",
        "role_connections": tuple(role.value for role in ALL_ROLES),
    },
    {
        "source_id": "shrm-state-workplace-2024",
        "category": EvidenceCategory.CREDIBLE_REPORT,
        "title": "2023–2024 State of the Workplace Report",
        "publisher": "Society for Human Resource Management",
        "url": "https://www.shrm.org/content/dam/en/shrm/research/2023-2024-State-of-the-Workplace-Report.pdf",
        "published_on": "2024-01-01",
        "relevant_excerpt": "SHRM reports that AI can streamline HR work and identifies skills-gap analysis and tailored learning as potential applications, while adoption and outcomes vary.",
        "provenance": "Bundled from SHRM's public workplace research report; month-level publication was normalized to the first day of 2024.",
        "role_connections": tuple(role.value for role in ALL_ROLES),
    },
    {
        "source_id": "atlanta-fed-ai-job-postings-2024",
        "category": EvidenceCategory.JOB_POSTING_SIGNAL,
        "title": "Recent Trends in the Demand for AI Skills",
        "publisher": "Federal Reserve Bank of Atlanta",
        "url": "https://www.atlantafed.org/research-and-data/publications/policy-hub-macroblog/2024/10/15/recent-trends-in-demand-for-ai-skills",
        "published_on": "2024-10-15",
        "relevant_excerpt": "Analysis of Lightcast postings through August 2024 finds U.S. demand for AI skills rising and spreading across more occupations, industries, and labor markets.",
        "provenance": "Bundled from an Atlanta Fed analysis of selected online job postings; selection bias is retained as a limitation.",
        "role_connections": tuple(role.value for role in ALL_ROLES),
    },
    {
        "source_id": "dol-inclusive-ai-hiring-2024",
        "category": EvidenceCategory.OFFICIAL_POLICY,
        "title": "AI & Inclusive Hiring Framework announcement",
        "publisher": "U.S. Department of Labor",
        "url": "https://www.dol.gov/newsroom/releases/odep/odep20240924",
        "published_on": "2024-09-24",
        "relevant_excerpt": "DOL's framework calls for governance and accessibility practices intended to reduce disability discrimination risks in AI-enabled recruitment and hiring.",
        "provenance": "Bundled from an official U.S. Department of Labor release and linked public framework.",
        "role_connections": tuple(role.value for role in HR_ROLES),
    },
    {
        "source_id": "dol-ai-worker-principles-2024",
        "category": EvidenceCategory.OFFICIAL_POLICY,
        "title": "Principles for worker well-being in AI adoption",
        "publisher": "U.S. Department of Labor",
        "url": "https://www.dol.gov/newsroom/releases/osec/osec20240516",
        "published_on": "2024-05-16",
        "relevant_excerpt": "DOL principles emphasize worker input, transparency, rights protection, ethical development, governance, and using AI to enhance work.",
        "provenance": "Bundled from an official U.S. Department of Labor release; the page warns that administration-era material may later become outdated.",
        "role_connections": tuple(role.value for role in ALL_ROLES),
    },
)


ROLE_METADATA: dict[Role, dict[str, str]] = {
    Role.HR_COORDINATOR: {
        "name": "HR Coordinator",
        "context": "Coordinates employee records, onboarding, benefits and policy administration, and cross-functional People Operations workflows.",
    },
    Role.RECRUITER: {
        "name": "Recruiter",
        "context": "Translates workforce needs into sourcing, screening, candidate communication, selection coordination, and equitable hiring decisions.",
    },
    Role.LEARNING_AND_DEVELOPMENT_SPECIALIST: {
        "name": "Learning & Development Specialist",
        "context": "Diagnoses learning needs and designs, facilitates, and evaluates programs that improve workforce skills and knowledge.",
    },
}
