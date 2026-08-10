"""Command-line operations mirroring every owner workflow in the dashboard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .application import SkillsVectorService
from .domain import Role
from .repository import SQLiteRepository


def default_database() -> str:
    return os.environ.get("SKILLS_VECTOR_DB", "data/skills_vector.db")


def service_for(path: str) -> SkillsVectorService:
    return SkillsVectorService(SQLiteRepository(path))


def compact_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"], "role": run["role_slug"], "role_name": run["role_name"],
        "status": run["status"], "started_at": run["started_at"],
        "artifact_stages": [item["stage"] for item in run["artifacts"]],
        "brief_version": run["brief"]["version"] if run.get("brief") else None,
        "approval": run.get("approval"),
    }


def run_acceptance(path: str, *, fresh: bool) -> int:
    target = Path(path)
    if fresh and target.exists():
        target.resolve().unlink()
    service = service_for(str(target))
    results: list[dict[str, Any]] = []
    required_sections = {"role_context", "what_is_changing", "task_shifts", "skill_shifts",
                         "durable_capabilities", "scenario", "sources", "counter_evidence"}
    for role in Role:
        draft = service.investigate(role.value)
        assert draft["status"] == "awaiting_approval"
        assert draft["brief"]["private"] is True
        assert required_sections.issubset(draft["brief"])
        assert any(item["stage"] == "human_approval_gate" for item in draft["artifacts"])
        approved = service.approve(draft["id"], "Local Owner", "Acceptance case approved.")
        assert approved["status"] == "approved"
        restarted = service_for(str(target)).run(draft["id"])
        assert restarted and restarted["status"] == "approved" and restarted["approval"]
        results.append({"role": role.value, "run_id": draft["id"], "status": "PASS"})
    print(json.dumps({"acceptance_streak": results, "consecutive_passes": 3, "database": str(target)}, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="skills-vector", description="Private local occupational intelligence")
    root.add_argument("--db", default=default_database(), help="SQLite database path")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize and seed the local database")
    commands.add_parser("serve", help="Start the local dashboard").add_argument("--port", type=int, default=8000)
    investigate = commands.add_parser("investigate", help="Run the real deterministic LangGraph investigation")
    investigate.add_argument("role", choices=[role.value for role in Role])
    inspect = commands.add_parser("inspect", help="Inspect a persisted run or the latest role run")
    inspect.add_argument("run_id", nargs="?")
    inspect.add_argument("--latest", choices=[role.value for role in Role])
    approve = commands.add_parser("approve", help="Explicitly approve an awaiting-review run")
    approve.add_argument("run_id", nargs="?")
    approve.add_argument("--latest", choices=[role.value for role in Role])
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", default="")
    acceptance = commands.add_parser("acceptance", help="Run the three consecutive role cases")
    acceptance.add_argument("--fresh", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn
        os.environ["SKILLS_VECTOR_DB"] = args.db
        uvicorn.run("skills_vector.web:create_app", factory=True, host="127.0.0.1", port=args.port)
        return 0
    if args.command == "acceptance":
        return run_acceptance(args.db, fresh=args.fresh)
    service = service_for(args.db)
    if args.command == "init":
        print(json.dumps({"database": args.db, "roles": len(service.roles()), "status": "ready"}, indent=2))
        return 0
    if args.command == "investigate":
        print(json.dumps(compact_run(service.investigate(args.role)), indent=2))
        return 0
    if args.command == "inspect":
        if bool(args.run_id) == bool(args.latest):
            raise SystemExit("inspect requires exactly one RUN_ID or --latest ROLE")
        run = service.run(args.run_id) if args.run_id else service.latest(args.latest)
        if run is None:
            raise SystemExit("run not found")
        print(json.dumps(run, indent=2))
        return 0
    if args.command == "approve":
        if bool(args.run_id) == bool(args.latest):
            raise SystemExit("approve requires exactly one RUN_ID or --latest ROLE")
        if args.latest:
            latest = service.latest(args.latest)
            if latest is None:
                raise SystemExit("run not found")
            args.run_id = latest["id"]
        print(json.dumps(compact_run(service.approve(args.run_id, args.reviewer, args.note)), indent=2))
        return 0
    return 2
