"""Atomic public releases. Never export fixtures, private evidence, or checkpoints."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .catalog import CatalogStore
from .ids import canonical_json, content_hash
from .occupational import SCHEMA_VERSION, SourceKind


AGENT_GUIDE = """# Skills Vector agent usage

This directory is an approved occupational snapshot. Browsing and downloading it must not trigger model calls.

- Occupations, tasks, and skills use stable IDs. Treat `change_id` as the incremental cursor key.
- `state` is explicit: provisional, reviewed, superseded, or removed.
- Job advertisements in provenance are stated demand, not proof of work.
- Jobsss should download a release and compare locally. Do not send private profiles here.
- Assessment input/output: see assessment_contract.json. Outcomes are unknown, gap, meets, exceeds, or potential_transfer.
- Missing résumé keywords are never automatically a gap. Embedding similarity never establishes proficiency.
- Forecast files, if present, are optional and are not current requirements.
"""


ASSESSMENT_CONTRACT = {
    "schema": "skills-vector.assessment.v1",
    "input": {
        "benchmark_id": "release id",
        "benchmark_version": "manifest.schema_version",
        "person_evidence": [
            {
                "skill_id": "skl_...",
                "kind": "work_sample | observation | credential | self_report",
                "level": "1_awareness | 2_working | 3_independent | 4_leading",
                "notes": "local only",
            }
        ],
        "resume_keywords": ["never sufficient to mark a gap"],
    },
    "output": {
        "outcomes": ["unknown", "gap", "meets", "exceeds", "potential_transfer"],
        "cause": ["new_requirements", "personal_evidence"],
        "privacy": "results stay with Jobsss; this package contains no personal data",
    },
}


def publish_release(store: CatalogStore, destination: Path, *, include_unapproved: bool = False) -> Path:
    runs = store.approved_runs()
    if not runs and not include_unapproved:
        raise ValueError("publication requires at least one approved occupation run")
    previous = store.current_release_id()
    release_id = datetime.now(UTC).strftime("rel-%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    staging = destination / f".staging-{release_id}"
    final = destination / release_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    occupations = []
    seen_occ = set()
    for run in runs:
        occ = store.occupation(run.occupation_id)
        if occ.occupation_id not in seen_occ:
            occupations.append(occ)
            seen_occ.add(occ.occupation_id)

    public_sources = [source for source in store.sources() if not source.fixture and source.kind is not SourceKind.OFFLINE_FIXTURE]
    public_source_ids = {source.source_id for source in public_sources}
    # Approved fixture-backed pilots still publish provenance metadata with fixture flag omitted
    # only when the run used fixtures: those records stay out of the approved public corpus.
    fixture_runs = [run for run in runs if run.allow_fixtures]
    coverage = {
        "occupations": len(occupations),
        "families_represented": sorted({occ.family.value for occ in occupations}),
        "fixture_backed_runs": [run.run_id for run in fixture_runs],
        "public_sources": len(public_sources),
        "provisional_rubrics": sum(1 for rubric in store.rubrics() if rubric.provisional),
        "freshness": "pilot; weekly collection is a local command, not a hosted scheduler",
    }

    tasks = [task for occ in occupations for task in store.tasks(occ.occupation_id)]
    requirements = [req for occ in occupations for req in store.requirements(occ.occupation_id)]
    claims = [
        claim
        for occ in occupations
        for claim in store.claims(occ.occupation_id)
        if set(claim.source_ids) <= public_source_ids or not public_source_ids
    ]
    if fixture_runs:
        claims = []
        public_sources = []

    _write_jsonl(staging / "occupations.jsonl", occupations)
    _write_jsonl(staging / "tasks.jsonl", tasks)
    _write_jsonl(staging / "skills.jsonl", store.skills())
    _write_jsonl(staging / "requirements.jsonl", requirements)
    _write_jsonl(staging / "sources.jsonl", public_sources)
    _write_jsonl(staging / "claims.jsonl", claims)
    _write_jsonl(staging / "changes.jsonl", store.change_events())
    (staging / "coverage.json").write_text(canonical_json(coverage) + "\n", encoding="utf-8")
    (staging / "assessment_contract.json").write_text(canonical_json(ASSESSMENT_CONTRACT) + "\n", encoding="utf-8")
    (staging / "AGENT.md").write_text(AGENT_GUIDE, encoding="utf-8")

    manifest = {
        "release_id": release_id,
        "schema_version": SCHEMA_VERSION,
        "previous_release_id": previous,
        "created_at": datetime.now(UTC).isoformat(),
        "approved_run_ids": [run.run_id for run in runs],
        "occupation_count": len(occupations),
        "includes_forecasts": False,
        "includes_fixtures": False,
        "includes_checkpoints": False,
        "includes_private_evidence": False,
    }
    encoded = canonical_json(manifest)
    (staging / "manifest.json").write_text(encoded + "\n", encoding="utf-8")
    os.replace(staging, final)
    store.add_release(release_id, final, content_hash(encoded), [run.run_id for run in runs], previous)
    (final.parent / "CURRENT").write_text(release_id + "\n", encoding="utf-8")
    return final


def rollback_release(store: CatalogStore, release_id: str, destination: Path) -> str:
    previous = store.rollback_release(release_id)
    marker = destination / "CURRENT"
    marker.write_text(previous + "\n", encoding="utf-8")
    return previous


def _write_jsonl(path: Path, rows) -> None:
    from dataclasses import asdict, is_dataclass
    from datetime import date, datetime
    from enum import StrEnum

    def convert(value):
        if is_dataclass(value) and not isinstance(value, type):
            return {k: convert(v) for k, v in asdict(value).items()}
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [convert(v) for v in value]
        return value

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(convert(row), sort_keys=True) + "\n")
