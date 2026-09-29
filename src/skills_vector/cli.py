"""Unix-style commands. Research stays local; publication is static files."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

from .assessment import assess
from .budget import BudgetError, PINNED_MODELS
from .catalog import CatalogStore
from .config import Settings
from .interpret import DeepInfraInterpreter
from .occupational import HumanReview, OccupationId, ReviewDecision
from .pipeline import ResearchPipeline, build_offline_pipeline, default_fixture_root
from .publication import publish_release, rollback_release


def build_research_pipeline(store: CatalogStore, settings: Settings, args) -> ResearchPipeline:
    """Select the offline or live interpreter. Live needs an explicit flag plus a key."""
    live = bool(getattr(args, "live", False) or getattr(args, "escalate_hard", False)) or settings.runtime in {
        "live",
        "deepinfra",
    }
    if not live:
        return build_offline_pipeline(store)
    if getattr(args, "fixtures", False):
        raise ValueError("--live cannot be combined with --fixtures; live runs must not silently use fixtures")
    if not settings.deepinfra_api_key:
        raise ValueError(
            "DEEPINFRA_API_KEY is required for --live. Export it locally (never commit it); "
            "see .env.example. No key is needed on Vercel because the preview is static."
        )
    interpreter = DeepInfraInterpreter(
        store.budget,
        run_id="pending",
        api_key=settings.deepinfra_api_key,
        live=True,
        escalate_hard=bool(getattr(args, "escalate_hard", False)),
    )
    return ResearchPipeline(store, default_fixture_root(), interpreter)


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
    research.add_argument(
        "--live",
        action="store_true",
        help="use DeepInfra live interpretation (requires DEEPINFRA_API_KEY; disables fixture fallback)",
    )
    research.add_argument(
        "--escalate-hard",
        action="store_true",
        help="route challenge/reconciliation escalations to zai-org/GLM-5.3 (costs more; implies --live)",
    )

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

    structured = sub.add_parser(
        "structured-job-analysis",
        help="build POC A HR Generalist guide and machine release from the frozen dev corpus",
    )
    structured.add_argument("--mode", choices=("offline", "live-final"), default="offline")
    structured.add_argument("--benchmark-dir", type=Path)
    structured.add_argument("--base-corpus-dir", type=Path)
    structured.add_argument("--output-dir", type=Path)
    structured.add_argument("--resource-config", type=Path, help="parent-approved live resource/sampling JSON; live-final only")
    structured.add_argument("--freeze-sha", help="full candidate SHA frozen by the parent; live-final only")
    structured.add_argument("--confirm-final-run", action="store_true", help="explicitly authorize the one guarded final request")
    structured.add_argument(
        "--corrective-round-2",
        action="store_true",
        help="select the dedicated corrective round-2 one-shot lock; live-final only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "structured-job-analysis":
        from .structured_job_analysis import (
            DEFAULT_BASE_CORPUS_DIR,
            DEFAULT_BENCHMARK_DIR,
            DEFAULT_OUTPUT_DIR,
            StructuredAnalysisError,
            run_pipeline,
        )

        actual_argv = argv if argv is not None else sys.argv[1:]
        command = shlex.join([sys.executable, "-m", "skills_vector", *actual_argv])
        try:
            receipt = run_pipeline(
                benchmark_dir=args.benchmark_dir or DEFAULT_BENCHMARK_DIR,
                base_corpus_dir=args.base_corpus_dir or DEFAULT_BASE_CORPUS_DIR,
                output_dir=args.output_dir or DEFAULT_OUTPUT_DIR,
                mode=args.mode,
                resource_config_path=args.resource_config,
                freeze_sha=args.freeze_sha,
                confirm_final_run=args.confirm_final_run,
                corrective_round_2=args.corrective_round_2,
                command=command,
            )
        except StructuredAnalysisError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({
            "status": receipt["status"],
            "run_id": receipt["run_id"],
            "output_dir": str((args.output_dir or DEFAULT_OUTPUT_DIR).resolve()),
        }, ensure_ascii=False))
        return 0

    settings = Settings.from_env()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with CatalogStore(settings.database_path, monthly_cap_usd=settings.monthly_budget_usd) as store:
        if args.command == "research":
            try:
                pipeline = build_research_pipeline(store, settings, args)
            except ValueError as exc:
                print(json.dumps({"error": str(exc)}))
                return 2
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
