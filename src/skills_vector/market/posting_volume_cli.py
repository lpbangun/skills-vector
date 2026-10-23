"""Local operator surface for the model-free posting lane."""
from pathlib import Path

from .posting_volume import VolumePolicy, write_snapshot
from .role_pipeline import EvidenceInputs, MAPPING_ROOT


def add_volume_commands(sub):
    parser = sub.add_parser("posting-volume", help="rules/1 quarterly posting lane; no models")
    actions = parser.add_subparsers(dest="volume_action", required=True)
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


def run_volume(args):
    if args.volume_action == "capture":
        return write_snapshot(VolumePolicy.read(args.policy), args.output, args.quarter,
                              payload_root=args.payload_root, retrieved_at=args.retrieved_at, live=args.live)
    inputs = EvidenceInputs.from_snapshots(args.snapshot, args.release_root, args.mapping_root, args.employer_map)
    audit = inputs.write(args.output)
    return {"release_id": audit["release_id"], "data_kind": inputs.data_kind,
            "roles": {slug: {k: v for k, v in report.items() if k not in ("postings", "dropped_edges")} for slug, report in audit["roles"].items()}}
