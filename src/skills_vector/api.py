"""HTTP API and same-origin static application for Skills Vector."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .domain import APPROVED_ROLES, ReviewDecision, Role
from .service import InvestigationService


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    as_of: date
    full_refresh: bool = False
    open_disagreements: bool = False


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: ReviewDecision
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=4000)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    service = InvestigationService(
        configured.database_path,
        runtime_mode=configured.runtime_mode,
    )
    app = FastAPI(
        title="Skills Vector API",
        version="0.1.0",
        description="Private-first occupational intelligence application boundary.",
    )
    app.state.settings = configured
    app.state.service = service

    if configured.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(configured.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "environment": configured.environment,
            "runtime": configured.runtime_mode,
        }

    @app.get("/api/v1/roles")
    async def roles() -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for role in sorted(APPROVED_ROLES, key=lambda item: item.value):
            runs = await run_in_threadpool(service.list_runs, role=role, limit=1)
            items.append(
                {
                    "role": role,
                    "latest_run": jsonable_encoder(runs[0]) if runs else None,
                }
            )
        return items

    @app.post("/api/v1/investigations", status_code=status.HTTP_201_CREATED)
    async def create_investigation(request: InvestigationCreate) -> object:
        try:
            result = await run_in_threadpool(
                service.create_investigation,
                role=request.role,
                as_of=request.as_of,
                full_refresh=request.full_refresh,
                open_disagreements=request.open_disagreements,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return jsonable_encoder(result)

    @app.get("/api/v1/investigations")
    async def list_investigations(
        role: Role | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> object:
        return jsonable_encoder(await run_in_threadpool(service.list_runs, role=role, limit=limit))

    @app.get("/api/v1/investigations/{run_id}")
    async def get_investigation(run_id: str) -> object:
        return jsonable_encoder(await _or_404(run_in_threadpool(service.get_run, run_id)))

    @app.get("/api/v1/investigations/{run_id}/events")
    async def get_events(run_id: str) -> object:
        return jsonable_encoder(await _or_404(run_in_threadpool(service.get_events, run_id)))

    @app.get("/api/v1/investigations/{run_id}/brief")
    async def get_brief(run_id: str) -> object:
        return jsonable_encoder(await _or_404(run_in_threadpool(service.get_brief, run_id)))

    @app.post("/api/v1/investigations/{run_id}/review")
    async def review_investigation(run_id: str, request: ReviewCreate) -> object:
        try:
            review = await run_in_threadpool(
                service.review_run,
                run_id,
                decision=request.decision,
                reviewer=request.reviewer,
                note=request.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="investigation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return jsonable_encoder(review)

    ui_directory = _resolve_ui_directory(configured.ui_directory)
    if ui_directory is not None:
        @app.get("/", include_in_schema=False)
        async def application_shell() -> FileResponse:
            return FileResponse(ui_directory / "app.html")

        app.mount("/", StaticFiles(directory=ui_directory), name="ui")

    return app


async def _or_404(awaitable):
    try:
        return await awaitable
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="investigation not found") from exc


def _resolve_ui_directory(value: Path | None) -> Path | None:
    if value is None:
        return None
    resolved = value.expanduser().resolve()
    if not (resolved / "app.html").is_file():
        return None
    return resolved


app = create_app()
