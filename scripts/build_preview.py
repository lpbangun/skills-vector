#!/usr/bin/env python3
"""Build a labeled fixture-backed static snapshot for Vercel preview.

This is not an approved production release. It exists so a PR can be browsed
without merging to main or calling models.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from skills_vector.catalog import CatalogStore
from skills_vector.occupational import HumanReview, OccupationId, ReviewDecision
from skills_vector.pipeline import build_offline_pipeline
from skills_vector.publication import publish_release

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "preview" / "release"


def _jsonl_to_array(path: Path) -> list:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "preview.sqlite"
        out = Path(tmp) / "releases"
        with CatalogStore(db) as store:
            pipeline = build_offline_pipeline(store)
            for occupation in OccupationId:
                run_id = pipeline.run(occupation.value, allow_fixtures=True)
                store.record_review(
                    run_id,
                    HumanReview(
                        ReviewDecision.APPROVED,
                        "preview-fixture",
                        datetime.now(UTC),
                        "Provisional fixture snapshot for Vercel PR preview only. Not practitioner review.",
                    ),
                )
            published = publish_release(store, out)
        if DEST.exists():
            shutil.rmtree(DEST)
        shutil.copytree(published, DEST)
        (DEST / "PREVIEW.md").write_text(
            "This directory is a **fixture-backed preview**. It is not a production "
            "benchmark, not practitioner-reviewed, and not the 30-role launch gate.\n",
            encoding="utf-8",
        )
        (DEST / "occupations.json").write_text(
            json.dumps(_jsonl_to_array(DEST / "occupations.jsonl"), indent=2) + "\n",
            encoding="utf-8",
        )
        (DEST / "tasks.json").write_text(
            json.dumps(_jsonl_to_array(DEST / "tasks.jsonl"), indent=2) + "\n",
            encoding="utf-8",
        )
        (DEST / "skills.json").write_text(
            json.dumps(_jsonl_to_array(DEST / "skills.jsonl"), indent=2) + "\n",
            encoding="utf-8",
        )
        (DEST / "requirements.json").write_text(
            json.dumps(_jsonl_to_array(DEST / "requirements.jsonl"), indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
