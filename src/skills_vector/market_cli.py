"""``skills-vector market`` commands: browse, query, search, refine, compare, research, publish, serve, MCP.

Read commands use the same backends as the MCP server (local release tree or the
deployed API), so CLI JSON and MCP tool results are identical. Research and
publish commands are local operator authority only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .market.core import (
    EXPECTATION_BASIS_VALUES,
    EXPECTATION_DIMENSION_VALUES,
    EXPECTATION_PROFICIENCY_VALUES,
    FACET_DIMENSIONS,
    INSPECTOR_SECTIONS,
    REGISTERED_ROLES,
    RESPONSIBILITY_BAND_VALUES,
    WORK_LEVEL_VALUES,
    CatalogStore,
)
from .market.guide import agent_guide_markdown, api_schema

DEFAULT_RELEASE_ROOT = Path("preview/release")
DEFAULT_EVIDENCE_ROOT = Path.home() / ".local" / "share" / "skills-vector-evidence"


def _backend(args: argparse.Namespace):
    if getattr(args, "deployed_url", None):
        from .market.remote import RemoteCatalogStore

        return RemoteCatalogStore(args.deployed_url)
    return CatalogStore(Path(getattr(args, "release_root", None) or DEFAULT_RELEASE_ROOT))

def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))



def _parse_min_postings(raw: str | None) -> dict[str, int] | None:
    if not raw:
        return None
    result: dict[str, int] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        slug, _, value = chunk.partition("=")
        if not slug or not value.isdigit():
            raise SystemExit(f"--min-postings expects slug=N pairs, got {chunk!r}")
        result[slug] = int(value)
    return result or None


def _parse_boards(values: list[str] | None) -> list[tuple[str, str, str]]:
    boards: list[tuple[str, str, str]] = []
    for raw in values or []:
        parts = raw.split(":")
        if len(parts) != 3 or not all(parts):
            raise SystemExit(f"--board expects ats:token:employer, got {raw!r}")
        boards.append((parts[0], parts[1], parts[2]))
    return boards


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skills-vector market", description=__doc__)
    sub = parser.add_subparsers(dest="market_command", required=True)

    def add_read_flags(target: argparse.ArgumentParser) -> None:
        target.add_argument("--release-root", type=Path, default=None, help="local release tree (default preview/release)")
        target.add_argument("--deployed-url", default=None, help="read the deployed API instead of local files")
        target.add_argument("--json", action="store_true", help="JSON output (default; flag accepted for explicit pipelines)")

    browse = sub.add_parser("browse", help="list published roles and statistics")
    add_read_flags(browse)

    query = sub.add_parser("query", help="cited answer over stored evidence")
    query.add_argument("query")
    query.add_argument("--occupation", default=None)
    query.add_argument("--limit", type=int, default=5)
    query.add_argument("--handoff-out", type=Path, default=None, help="write the bounded research handoff plan here")
    add_read_flags(query)

    search = sub.add_parser("search", help="search stored claims and admitted postings")
    search.add_argument("query")
    search.add_argument("--occupation", default=None)
    search.add_argument("--limit", type=int, default=20)
    add_read_flags(search)

    refine = sub.add_parser("refine", help="filter admitted postings by recorded dimensions and exact source identity")
    refine.add_argument("--occupation", default=None)
    refine.add_argument("--query", default="")
    refine.add_argument("--work-level", choices=WORK_LEVEL_VALUES)
    refine.add_argument("--responsibility-band", choices=RESPONSIBILITY_BAND_VALUES)
    refine.add_argument("--expectation-dimension", choices=EXPECTATION_DIMENSION_VALUES)
    refine.add_argument("--expectation-basis", choices=EXPECTATION_BASIS_VALUES)
    refine.add_argument("--expectation-proficiency", choices=EXPECTATION_PROFICIENCY_VALUES)
    refine.add_argument("--expectation-identity", default=None, help="exact producer-supplied identity_id, not an edge or expectation_id")
    refine.add_argument("--context-dimension", choices=FACET_DIMENSIONS)
    refine.add_argument("--context-value", default=None)
    refine.add_argument("--context-status", choices=("present", "unknown"))
    refine.add_argument("--experience-status", choices=("present", "unknown"))
    refine.add_argument("--experience", default=None)
    refine.add_argument("--limit", type=int, default=50)
    add_read_flags(refine)

    compare = sub.add_parser("compare", help="compare registered roles by admitted counts and exact source identities")
    compare.add_argument("occupations", nargs="+")
    add_read_flags(compare)

    occupation = sub.add_parser("occupation", help="role page bundle (foundations, demand, learning, lineage)")
    occupation.add_argument("slug")
    add_read_flags(occupation)

    component = sub.add_parser("component", help="retrieve one immutable chart component by stable id")
    component.add_argument("component_id")
    component.add_argument("--release", default=None, help="immutable release id (defaults to current)")
    add_read_flags(component)

    claim = sub.add_parser("claim", help="stored claim with verified quote and citation url")
    claim.add_argument("claim_id")
    add_read_flags(claim)

    evidence = sub.add_parser("evidence", help="evidence inspector rows")
    evidence.add_argument("--section", required=True, choices=[*INSPECTOR_SECTIONS, "all"])
    evidence.add_argument("--occupation", default=None)
    evidence.add_argument("--limit", type=int, default=50)
    evidence.add_argument("--no-text", action="store_true")
    add_read_flags(evidence)

    release_info = sub.add_parser("release-info", help="current release metadata and versions")
    add_read_flags(release_info)

    research_plan = sub.add_parser("research-plan", help="bounded local research plan for a question")
    research_plan.add_argument("query")
    research_plan.add_argument("--occupation", default=None)
    research_plan.add_argument("--out", type=Path, default=None)
    add_read_flags(research_plan)

    research = sub.add_parser("research", help="local bounded DeepInfra public-source research")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    run = research_sub.add_parser(
        "run",
        help="discover, retrieve, admit and freeze matched primary-only/challenger candidate arms",
    )
    run.add_argument("--config", type=Path, required=True, help="operator-owned DeepInfra config outside this worktree")
    run.add_argument("--occupation", required=True, choices=sorted(str(row["slug"]) for row in REGISTERED_ROLES))
    run.add_argument("--evidence-root", type=Path, required=True)
    run.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    run.add_argument("--question-file", type=Path, default=None)
    run.add_argument("--board", action="append", default=[], help="operator candidate ats:token:employer (repeatable)")
    run.add_argument("--min-postings", type=int, default=8)
    run.add_argument("--max-boards", type=int, default=16)
    run.add_argument("--max-postings", type=int, default=24)
    run.add_argument("--title-hints", default=None, help="comma-separated retrieval-priority hints")
    run.add_argument("--run-id", default=None)

    refresh = research_sub.add_parser("refresh", help="one operator-only bounded four-role refresh; installs no schedule")
    refresh.add_argument("--once", action="store_true", required=True, help="execute one due refresh batch and exit")
    refresh.add_argument("--config", type=Path, required=True, help="operator-owned DeepInfra config outside this worktree")
    refresh.add_argument("--policy", type=Path, required=True, help="external cadence and fixed retrieval-cap policy")
    refresh.add_argument("--retention-record", type=Path, required=True, help="independently adjudicated four-role retained-stage record")
    refresh.add_argument("--evidence-root", type=Path, required=True)
    refresh.add_argument("--checkpoint", type=Path, required=True, help="external durable refresh checkpoint")
    refresh.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)

    probe = research_sub.add_parser("probe", help="one billed, pre-reserved DeepInfra connectivity probe")
    probe.add_argument("--config", type=Path, required=True)
    probe.add_argument("--evidence-root", type=Path, required=True)
    probe.add_argument("--run-id", default=None)
    probe.add_argument(
        "--model-role",
        choices=("primary", "challenger", "escalation"),
        default="primary",
        help="pinned runtime model to probe (one inference call)",
    )

    budget = research_sub.add_parser("budget", help="read the durable mission reservation and attempt ledger")
    budget.add_argument("--config", type=Path, required=True)

    settle = research_sub.add_parser("settle", help="explicitly reconcile the mission after all usage is verified")
    settle.add_argument("--config", type=Path, required=True)
    reconcile = research_sub.add_parser(
        "reconcile",
        help="reconcile one unknown billed attempt only from an identical retained provider response",
    )
    reconcile.add_argument("--config", type=Path, required=True)
    reconcile.add_argument("--evidence-root", type=Path, required=True)
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--attempt-id", required=True)
    reconcile.add_argument("--response", type=Path, required=True)

    candidate = sub.add_parser("candidate", help="materialize one frozen arm after independent adjudication")
    candidate.add_argument("--comparison", type=Path, required=True)
    candidate.add_argument("--adjudication", type=Path, required=True)
    candidate.add_argument("--out", type=Path, required=True)

    publish = sub.add_parser("publish", help="publish only an adjudicated frozen arm to the local release tree")
    publish.add_argument("--comparison", type=Path, required=True)
    publish.add_argument("--adjudication", type=Path, required=True)
    publish.add_argument("--candidate-out", type=Path, required=True)
    publish.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    publish.add_argument("--min-postings", default=None, help="slug=N pairs")

    validate = sub.add_parser("validate", help="structural validation of a release candidate directory")
    validate.add_argument("candidate", type=Path)
    validate.add_argument("--min-postings", default=None, help="slug=N pairs")
    validate.add_argument("--json", action="store_true", help="JSON output (default)")

    rollback = sub.add_parser("rollback", help="point current.json back to an existing release")
    rollback.add_argument("release_id")
    rollback.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)

    serve = sub.add_parser("serve", help="local read-only server (API + static preview)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--worktree", type=Path, default=Path(__file__).resolve().parents[2])
    serve.add_argument("--release-root", type=Path, default=None)

    mcp = sub.add_parser("mcp", help="maintained MCP SDK server (stdio or stateless Streamable HTTP)")
    mcp.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    mcp.add_argument("--base-url", default=None)
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8787)
    mcp.add_argument("--print-tools", action="store_true")

    guide = sub.add_parser("agent-guide", help="agent guide and machine-readable schema")
    guide.add_argument("--deployed-url", default=None)

    return parser


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    command = args.market_command
    if command == "browse":
        _print(_backend(args).list_occupations())
        return 0
    if command == "query":
        payload = _backend(args).query(args.query, args.occupation, max(1, min(args.limit, 200)))
        _print(payload)
        if args.handoff_out is not None and payload.get("handoff"):
            args.handoff_out.write_text(json.dumps(payload["handoff"], indent=2) + "\n", encoding="utf-8")
        return 0
    if command == "search":
        if len(args.query) > 400:
            raise SystemExit("search query exceeds 400 characters")
        _print(_backend(args).search(args.query, args.occupation, max(1, min(args.limit, 100))))
        return 0
    if command == "refine":
        if len(args.query) > 400:
            raise SystemExit("refinement query exceeds 400 characters")
        if len(args.expectation_identity or "") > 160:
            raise SystemExit("--expectation-identity exceeds 160 characters")
        if len(args.context_value or "") > 100 or len(args.experience or "") > 100:
            raise SystemExit("--context-value and --experience are limited to 100 characters")
        if args.context_value and not args.context_dimension:
            raise SystemExit("--context-value requires --context-dimension")
        filters = {
            name: value
            for name, value in {
                "work_level": args.work_level,
                "responsibility_band": args.responsibility_band,
                "expectation_dimension": args.expectation_dimension,
                "expectation_basis": args.expectation_basis,
                "expectation_proficiency": args.expectation_proficiency,
                "expectation_identity": args.expectation_identity,
                "context_dimension": args.context_dimension,
                "context_value": args.context_value,
                "context_status": args.context_status,
                "experience_status": args.experience_status,
                "experience": args.experience,
            }.items()
            if value
        }
        _print(
            _backend(args).refine(
                occupation=args.occupation,
                filters=filters,
                query=args.query,
                limit=max(1, min(args.limit, 200)),
            )
        )
        return 0
    if command == "compare":
        _print(_backend(args).compare(args.occupations))
        return 0
    if command == "occupation":
        payload = _backend(args).occupation(args.slug)
        _print(payload)
        return 0 if payload.get("status") == "ok" else 1
    if command == "component":
        payload = _backend(args).get_component(args.component_id, args.release)
        _print(payload)
        return 0 if payload.get("status") == "ok" else 1
    if command == "claim":
        payload = _backend(args).claim(args.claim_id)
        _print(payload)
        return 0 if payload.get("status") == "ok" else 1
    if command == "evidence":
        payload = _backend(args).evidence(args.section, args.occupation, max(1, min(args.limit, 200)), not args.no_text)
        _print(payload)
        return 0
    if command == "release-info":
        _print(_backend(args).release_info())
        return 0
    if command == "research-plan":
        payload = _backend(args).research_plan(args.query, args.occupation)
        _print(payload)
        if args.out is not None:
            args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return 0
    if command == "research":
        from .market.budgeting import MissionBudget
        from .market.research_config import load_research_config

        runtime = load_research_config(
            args.config,
            product_root=Path(__file__).resolve().parents[2],
        )
        budget_ledger = MissionBudget(runtime)
        try:
            if args.research_command == "refresh":
                from .market.refresh import load_refresh_policy, run_refresh_once

                policy = load_refresh_policy(
                    args.policy,
                    product_root=Path(__file__).resolve().parents[2],
                )
                result = run_refresh_once(
                    runtime=runtime,
                    mission_budget=budget_ledger,
                    evidence_root=args.evidence_root.expanduser(),
                    release_root=args.release_root,
                    checkpoint_path=args.checkpoint.expanduser(),
                    retention_record=args.retention_record.expanduser(),
                    policy=policy,
                )
                _print(result)
                return 0 if result.get("status") in {
                    "published", "unchanged", "skipped_not_due", "recovered_last_good",
                } else 1
            if args.research_command == "budget":
                _print(budget_ledger.summary())
                return 0
            if args.research_command == "reconcile":
                from .market.agent import DeepInfraRunner

                run_dir = args.evidence_root.expanduser() / "runs" / args.run_id
                runner = DeepInfraRunner(
                    run_id=args.run_id,
                    run_dir=run_dir,
                    config=runtime,
                    budget=budget_ledger,
                )
                _print(runner.reconcile_unknown(args.attempt_id, args.response))
                return 0
            if args.research_command == "probe":
                from .market.pipeline import probe_runtime

                receipt = probe_runtime(
                    evidence_root=args.evidence_root.expanduser(),
                    runtime=runtime,
                    mission_budget=budget_ledger,
                    run_id=args.run_id,
                    model_role=args.model_role,
                )
                _print(receipt)
                return 0 if receipt.get("ok") else 2
            if args.research_command != "run":
                raise SystemExit("unknown research command")
            from .market.pipeline import ResearchConfig, run_research

            question = None
            if args.question_file is not None:
                question_data = json.loads(args.question_file.read_text(encoding="utf-8"))
                question = str(question_data.get("question") or "") if isinstance(question_data, dict) else None
            config = ResearchConfig(
                occupation=args.occupation,
                evidence_root=args.evidence_root.expanduser(),
                release_root=args.release_root,
                runtime=runtime,
                mission_budget=budget_ledger,
                question=question,
                operator_boards=_parse_boards(args.board),
                min_postings=args.min_postings,
                max_boards=args.max_boards,
                max_postings=args.max_postings,
                run_id=args.run_id,
                title_hints=tuple(h.strip() for h in args.title_hints.split(",") if h.strip()) if args.title_hints else None,
            )
            result = run_research(config)
            _print(result)
            return 0 if result["status"] == "comparison_ready" else 1
        finally:
            budget_ledger.close()
    if command == "candidate":
        from .market.comparison import materialize_reviewed_candidate

        _print(
            materialize_reviewed_candidate(
                args.comparison,
                args.adjudication,
                args.out,
            )
        )
        return 0
    if command == "publish":
        from .market.comparison import materialize_reviewed_candidate
        from .market.publish import merge_slice, publish_release

        reviewed = materialize_reviewed_candidate(
            args.comparison,
            args.adjudication,
            args.candidate_out,
        )
        candidate = merge_slice(args.release_root, Path(reviewed["candidate_path"]))
        result = publish_release(
            args.release_root,
            candidate,
            min_postings=_parse_min_postings(args.min_postings),
        )
        result["independent_adjudication"] = {
            "comparison_sha256": reviewed["comparison_sha256"],
            "reviewer_id": reviewed["reviewer_id"],
            "selected_arm": reviewed["selected_arm"],
            "retained_supported_misses": reviewed["retained_supported_misses"],
            "receipt": reviewed["adjudication_path"],
        }
        _print(result)
        return 0
    if command == "validate":
        from .market.publish import validate_candidate

        problems = validate_candidate(args.candidate, min_postings=_parse_min_postings(args.min_postings))
        payload = {"candidate": str(args.candidate), "problems": problems, "valid": not problems}
        _print(payload)
        return 0 if not problems else 1
    if command == "rollback":
        from .market.publish import rollback_current

        _print(rollback_current(args.release_root, args.release_id))
        return 0
    if command == "serve":
        from .market.server import run_server

        worktree = args.worktree.resolve()
        release_root = args.release_root.resolve() if args.release_root else worktree / "preview" / "release"
        return run_server(worktree, release_root, args.host, args.port)
    if command == "mcp":
        from .market.mcp import build_backend, create_mcp_server, tool_schemas

        backend = build_backend(
            release_root=str(args.release_root) if args.release_root else None,
            base_url=args.base_url,
        )
        server = create_mcp_server(backend)
        if args.print_tools:
            _print({"tools": tool_schemas(server), "schema": api_schema()})
            return 0
        if args.transport == "stdio":
            server.run("stdio")
        else:
            server.run("streamable-http", host=args.host, port=args.port, stateless_http=True)
        return 0
    if command == "agent-guide":
        print(agent_guide_markdown(base_url=args.deployed_url or ""))
        return 0
    raise SystemExit(f"unknown market command {command!r}")


def main(argv: list[str] | None = None) -> int:
    return run(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
