"""Command-line entrypoint for the local Skills Vector application."""

from __future__ import annotations

import argparse
import json
from datetime import date

from fastapi.encoders import jsonable_encoder

from .api import create_app
from .config import Settings
from .domain import Role
from .service import InvestigationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skills-vector")
    subcommands = parser.add_subparsers(dest="command", required=True)

    api = subcommands.add_parser("api", help="serve the API and Evidence Atlas UI")
    api.add_argument("--reload", action="store_true", help="reload when local files change")

    run = subcommands.add_parser("run", help="run one private investigation")
    run.add_argument("role", choices=[role.value for role in Role])
    run.add_argument("--as-of", default=date.today().isoformat())
    run.add_argument("--full-refresh", action="store_true")
    run.add_argument("--open-disagreements", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.command == "api":
        import uvicorn

        target = "skills_vector.api:app" if args.reload else create_app(settings)
        uvicorn.run(
            target,
            host=settings.host,
            port=settings.port,
            reload=args.reload,
        )
        return 0

    service = InvestigationService(settings.database_path, runtime_mode=settings.runtime_mode)
    result = service.create_investigation(
        role=Role(args.role),
        as_of=date.fromisoformat(args.as_of),
        full_refresh=args.full_refresh,
        open_disagreements=args.open_disagreements,
    )
    print(json.dumps(jsonable_encoder(result), indent=2, sort_keys=True))
    return 0
