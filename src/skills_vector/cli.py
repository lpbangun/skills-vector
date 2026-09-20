"""Unix-style commands. Research stays local; publication is static files."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .assessment import assess
from .budget import BudgetError, PINNED_MODELS
from .catalog import CatalogStore
from .config import Settings
from .occupational import HumanReview, OccupationId, ReviewDecision
from .pipeline import build_offline_pipeline
from .publication import publish_release, rollback_release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skills-vector",
        description="Local occupational skills research and approved static exports.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    research = sub.add_parser("research", help="collect → extract → reconcile → challenge, then pause for review")
    research.add_argument("occupation", choices=[item.value for item in OccupationId])
    research.add_argument("--fixtures", action="store_true", help="use labeled offline fixtures (never implicit)")
    research.add_argument("--resume", dest="resume_run_id")
    research.add_argument("--include-forecast", action="store_true", help="optional; not required to publish")

    review = sub.add_parser("review", help="record a human review decision")
    review.add_argument("run_id")
    review.add_argument("decision", choices=[item.value for item in ReviewDecision])
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", default="")

    sub.add_parser("release", help="atomically publish approved occupations")
    rollback = sub.add_parser("rollback", help="restore the previous approved release pointer")
    rollback.add_argument("release_id")

    assess_cmd = sub.add_parser("assess", help="compare local personal evidence to a pinned benchmark")
    assess_cmd.add_argument("occupation", choices=[item.value for item in OccupationId])
    assess_cmd.add_argument("--evidence", type=Path, required=True)
    assess_cmd.add_argument("--benchmark-id", required=True)
    assess_cmd.add_argument("--benchmark-version", required=True)

    sub.add_parser("budget", help="show monthly inference remaining")
    sub.add_parser("schedule-notes", help="print local scheduling constraints")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with CatalogStore(settings.database_path, monthly_cap_usd=settings.monthly_budget_usd) as store:
        if args.command == "research":
            pipeline = build_offline_pipeline(store)
            pipeline.include_forecast = bool(args.include_forecast)
            try:
                run_id = pipeline.run(
                    args.occupation,
                    allow_fixtures=bool(args.fixtures),
                    resume_run_id=args.resume_run_id,
                )
            except BudgetError as exc:
                print(json.dumps({"queued": True, "error": str(exc)}))
                return 2
            run = store.run(run_id)
            print(json.dumps({"run_id": run_id, "status": run.status.value, "phase": run.phase.value, "thread_id": run.thread_id}))
            return 0
        if args.command == "review":
            review = store.record_review(
                args.run_id,
                HumanReview(ReviewDecision(args.decision), args.reviewer, datetime.now(UTC), args.note),
            )
            print(json.dumps({"decision": review.decision.value, "reviewer": review.reviewer}))
            return 0
        if args.command == "release":
            path = publish_release(store, settings.releases_path)
            print(json.dumps({"release": str(path)}))
            return 0
        if args.command == "rollback":
            previous = rollback_release(store, args.release_id, settings.releases_path)
            print(json.dumps({"current": previous}))
            return 0
        if args.command == "assess":
            payload = json.loads(args.evidence.read_text(encoding="utf-8"))
            result = assess(
                store,
                args.occupation,
                personal_evidence=tuple(payload.get("person_evidence") or ()),
                resume_keywords=tuple(payload.get("resume_keywords") or ()),
                benchmark_id=args.benchmark_id,
                benchmark_version=args.benchmark_version,
            )
            print(json.dumps({
                "benchmark_id": result.benchmark_id,
                "items": [
                    {
                        "requirement_id": item.requirement_id,
                        "skill_id": item.skill_id,
                        "outcome": item.outcome.value,
                        "reason": item.reason,
                        "cause": item.cause,
                    }
                    for item in result.items
                ],
            }, indent=2))
            return 0
        if args.command == "budget":
            print(json.dumps({
                "cap_usd": store.budget.monthly_cap_usd,
                "remaining_usd": store.budget.remaining(),
                "models": {key: price.model_id for key, price in PINNED_MODELS.items()},
            }))
            return 0
        if args.command == "schedule-notes":
            print(
                "Local weekly collection and monthly review are commands you run on this computer. "
                "The machine must be awake. This repository does not install a hosted runner, cron job, "
                "or GitHub Action scheduler."
            )
            return 0
    return 1
