"""Local operator surface for the model-free posting lane."""
from pathlib import Path

from .posting_volume import VolumePolicy, write_snapshot
from .role_pipeline import EvidenceInputs, MAPPING_ROOT


def add_volume_commands(sub):
    parser = sub.add_parser("posting-volume", help="quarterly posting lane; no models")
    actions = parser.add_subparsers(dest="volume_action", required=True)
    roster = actions.add_parser("roster-probe", help="operator-only probe of candidate ATS tokens; produces an unreviewed draft")
    roster.add_argument("--candidates", type=Path, required=True)
    roster.add_argument("--output", type=Path, required=True)
    mode = roster.add_mutually_exclusive_group(required=True)
    mode.add_argument("--payload-root", type=Path)
    mode.add_argument("--live", action="store_true")
    capture = actions.add_parser("capture", help="capture allowlisted boards or saved native payloads")
    capture.add_argument("--policy", type=Path, required=True)
    capture.add_argument("--quarter", required=True)
    capture.add_argument("--output", type=Path, required=True, help="snapshot root; quarter subdirectory is created")
    mode = capture.add_mutually_exclusive_group(required=True)
    mode.add_argument("--payload-root", type=Path)
    mode.add_argument("--live", action="store_true", help="explicit live public-board retrieval; no paid search/model calls")
    capture.add_argument("--retrieved-at", help="recorded timestamp for local payloads; live retrieval records its own")
    project = actions.add_parser("project", help="M4 projection of one or more independent quarterly snapshots")
    project.add_argument("--snapshot", type=Path, action="append", required=True)
    project.add_argument("--release-root", type=Path, default=Path("preview/release"), help="read-only published foundation")
    project.add_argument("--mapping-root", type=Path, default=MAPPING_ROOT)
    project.add_argument("--employer-map", type=Path)
    project.add_argument("--output", type=Path, required=True)
    project.add_argument("--evidence-root", type=Path, help="out-of-tree audit store (default: operator evidence releases)")
    project.add_argument("--inline-audit", action="store_true", help="diagnostic scratch projection only; never commit full audits")
    project.add_argument("--legacy-grouping", action="store_true", help="diagnostic: M7 paragraph/category grouping without canonical tools/terms or publication spans")
    project.add_argument("--no-upgrade", action="store_true", help="diagnostic: retain original rules/1 extraction")
    project.add_argument("--no-grouping", action="store_true", help="diagnostic: disable O*NET 31 grouping")


def run_volume(args):
    if args.volume_action == "roster-probe":
        from .roster_probe import probe
        return probe(args.candidates, args.output, payload_root=args.payload_root, live=args.live)
    if args.volume_action == "capture":
        return write_snapshot(VolumePolicy.read(args.policy), args.output, args.quarter,
                              payload_root=args.payload_root, retrieved_at=args.retrieved_at, live=args.live)
    inputs = EvidenceInputs.from_snapshots(args.snapshot, args.release_root, args.mapping_root, args.employer_map, upgrade=not args.no_upgrade, grouping=not args.no_grouping, canonical=not args.legacy_grouping)
    if inputs.data_kind == "research" and not args.inline_audit:
        from .role_publication import EVIDENCE_ROOT, publish
        records, audit = inputs.build()
        publish([r.to_dict() for r in records], audit, inputs.snapshot_roots, args.output,
                args.evidence_root or EVIDENCE_ROOT, protected=(inputs.release_root, inputs.mapping_root))
    else:
        audit = inputs.write(args.output)
    return {"release_id": audit["release_id"], "data_kind": inputs.data_kind,
            "roles": {slug: {k: v for k, v in report.items() if k not in ("postings", "dropped_edges")} for slug, report in audit["roles"].items()}}
