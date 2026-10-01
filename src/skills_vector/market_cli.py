"""``skills-vector market`` commands: browse, query, research, publish, serve, MCP.

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

from .market.core import INSPECTOR_SECTIONS
from .market.guide import agent_guide_markdown, api_schema
from .market.mcp import LocalBackend, RemoteBackend, TOOLS
from .market.pipeline import OCCUPATION_CONFIG, ResearchConfig, run_research
from .market.publish import merge_slice, publish_release, rollback_current

DEFAULT_RELEASE_ROOT = Path("preview/release")
DEFAULT_EVIDENCE_ROOT = Path.home() / ".local" / "share" / "skills-vector-evidence"


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _backend(args: argparse.Namespace):
    if getattr(args, "deployed_url", None):
        return RemoteBackend(args.deployed_url)
    return LocalBackend(Path(getattr(args, "release_root", None) or DEFAULT_RELEASE_ROOT))


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

    occupation = sub.add_parser("occupation", help="role page bundle (foundations, demand, learning, lineage)")
    occupation.add_argument("slug")
    add_read_flags(occupation)

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

    research = sub.add_parser("research", help="local research authority (real subscription agent runs)")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    run = research_sub.add_parser(
        "run",
        help="run discovery -> retrieval -> candidate selection (feedback) -> admission -> reconciliation -> challenge -> release",
    )
    run.add_argument("--occupation", required=True, choices=sorted(OCCUPATION_CONFIG))
    run.add_argument("--evidence-root", type=Path, required=True)
    run.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    run.add_argument("--overlay", type=Path, required=True, help="externally supplied runtime overlay (fallback stays disabled)")
    run.add_argument("--question-file", type=Path, default=None, help="research handoff plan that triggered this run")
    run.add_argument("--board", action="append", default=[], help="operator candidate ats:token:employer (repeatable)")
    run.add_argument("--min-postings", type=int, default=8)
    run.add_argument("--max-boards", type=int, default=16)
    run.add_argument("--max-postings", type=int, default=60)
    run.add_argument("--title-hints", default=None, help="comma-separated title filters (overrides the built-in scope hints)")
    run.add_argument("--no-publish", action="store_true", help="validate and receipt only; keep last-good untouched")
    run.add_argument("--run-id", default=None)

    probe = research_sub.add_parser("probe", help="one bounded model call proving the pinned subscription model resolves")
    probe.add_argument("--evidence-root", type=Path, required=True)
    probe.add_argument("--overlay", type=Path, required=True)
    probe.add_argument("--run-id", default=None)

    publish = sub.add_parser("publish", help="validate and atomically publish a slice or a merged candidate release")
    publish.add_argument("--slice", type=Path, default=None, help="occupation slice directory (merged with the current release)")
    publish.add_argument("--candidate", type=Path, default=None, help="already-merged candidate release directory")
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

    mcp = sub.add_parser("mcp", help="MCP stdio JSON-RPC server (local or deployed)")
    mcp.add_argument("--release-root", type=Path, default=None)
    mcp.add_argument("--base-url", default=None)
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
    if command == "occupation":
        payload = _backend(args).occupation(args.slug)
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
        if args.research_command == "probe":
            from .market.pipeline import probe_runtime

            receipt = probe_runtime(
                evidence_root=args.evidence_root.expanduser(),
                overlay=args.overlay,
                run_id=args.run_id,
            )
            _print(receipt)
            return 0 if receipt.get("ok") else 2
        if args.research_command != "run":
            raise SystemExit("unknown research command")
        question = None
        if args.question_file is not None:
            question = json.loads(args.question_file.read_text(encoding="utf-8")).get("question")
        config = ResearchConfig(
            occupation=args.occupation,
            evidence_root=args.evidence_root.expanduser(),
            release_root=args.release_root,
            overlay=args.overlay,
            question=question,
            operator_boards=_parse_boards(args.board),
            min_postings=args.min_postings,
            max_boards=args.max_boards,
            max_postings=args.max_postings,
            publish=not args.no_publish,
            run_id=args.run_id,
            title_hints=tuple(h.strip() for h in args.title_hints.split(",") if h.strip()) if args.title_hints else None,
        )
        result = run_research(config)
        _print(result)
        return 0 if result["status"] == "published" else 1
    if command == "publish":
        if bool(args.slice) == bool(args.candidate):
            raise SystemExit("market publish requires exactly one of --slice or --candidate")
        candidate = args.candidate
        if args.slice is not None:
            candidate = merge_slice(args.release_root, args.slice)
        result = publish_release(args.release_root, candidate, min_postings=_parse_min_postings(args.min_postings))
        _print(result)
        return 0
    if command == "validate":
        from .market.publish import validate_candidate

        problems = validate_candidate(args.candidate, min_postings=_parse_min_postings(args.min_postings))
        payload = {"candidate": str(args.candidate), "problems": problems, "valid": not problems}
        _print(payload)
        return 0 if not problems else 1
    if command == "rollback":
        _print(rollback_current(args.release_root, args.release_id))
        return 0
    if command == "serve":
        from .market.server import build_server

        worktree = args.worktree.resolve()
        release_root = args.release_root.resolve() if args.release_root else worktree / "preview" / "release"
        server = build_server(worktree, release_root, args.host, args.port)
        print(f"skills-vector market server on http://{args.host}:{args.port} (release root {release_root})", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if command == "mcp":
        if args.print_tools:
            _print({"tools": TOOLS, "schema": api_schema()})
            return 0
        from .market.mcp import McpServer, build_backend

        backend = build_backend(
            release_root=str(args.release_root) if args.release_root else str(DEFAULT_RELEASE_ROOT),
            base_url=args.base_url,
        )
        return McpServer(backend).serve_stdio()
    if command == "agent-guide":
        print(agent_guide_markdown(base_url=args.deployed_url or ""))
        return 0
    raise SystemExit(f"unknown market command {command!r}")


def main(argv: list[str] | None = None) -> int:
    return run(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
