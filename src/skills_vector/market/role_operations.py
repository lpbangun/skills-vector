"""Offline Role Atlas operator commands registered under the existing market CLI."""

from __future__ import annotations

from pathlib import Path

from .role_artifacts import read_json, write_json
from .role_ledger import run_ledger
from .role_signals import KINDS, import_signals, merge_signals


def add_operator_commands(sub) -> None:
    signals = sub.add_parser("signals", help="import/merge already-downloaded official files; never downloads")
    actions = signals.add_subparsers(dest="signals_action", required=True)
    importer = actions.add_parser("import", help="read one local upstream table/JSON response")
    importer.add_argument("kind", choices=tuple(KINDS))
    importer.add_argument("--input", type=Path, required=True)
    importer.add_argument("--output", type=Path, required=True)
    importer.add_argument("--retrieved-at", required=True, help="actual ISO retrieval timestamp with timezone")
    importer.add_argument("--sample", action="store_true", help="mark authored/illustrative input; cannot merge into research")
    importer.add_argument("--soc", help="original CareerOneStop query occupation code")
    importer.add_argument("--naics", help="original CareerOneStop query industry code, if any")
    importer.add_argument("--year", type=int, default=2024, help="BLS matrix base year")
    merge = actions.add_parser("merge", help="merge anchor context; skill links need explicit bindings")
    merge.add_argument("--roles-root", type=Path, required=True)
    merge.add_argument("--release-id")
    merge.add_argument("--bundle", type=Path, action="append", required=True)
    merge.add_argument("--bindings", type=Path)
    merge.add_argument("--output", type=Path, required=True)

    ledger = sub.add_parser("forecast-ledger", help="create, backtest, resolve or inspect a local forecast ledger")
    actions = ledger.add_subparsers(dest="ledger_action", required=True)
    for name in ("create", "resolve", "backtest", "show", "score"):
        action = actions.add_parser(name)
        action.add_argument("--ledger", type=Path, required=True)
        if name in ("create", "resolve", "show", "score"):
            action.add_argument("--roles-root", type=Path, required=True)
            action.add_argument("--release-id")
        if name == "create":
            action.add_argument("--events", type=Path, help="explicit exact-claim event bindings; no prose inference")
        if name == "backtest":
            action.add_argument("--archive", type=Path, help="directory of historical research role/2 snapshots")
            action.add_argument("--output", type=Path, help="optional deterministic report artifact")


def run_operator_command(args) -> dict:
    if args.market_command == "forecast-ledger":
        return run_ledger(args)
    if args.signals_action == "import":
        payload = import_signals(args.kind, args.input, args.retrieved_at, sample=args.sample,
                                 soc=args.soc, naics=args.naics, year=args.year)
        write_json(args.output, payload, inputs=(args.input,))
        return payload
    bundles = [read_json(path) for path in args.bundle]
    return merge_signals(args.roles_root, bundles, args.output,
                         bindings=read_json(args.bindings) if args.bindings else None,
                         release_id=args.release_id,
                         protected_inputs=tuple(args.bundle) + ((args.bindings,) if args.bindings else ()))
