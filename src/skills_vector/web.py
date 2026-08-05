"""Local FastAPI interface for the private Skills Vector dashboard."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .application import SkillsVectorService
from .domain import Role
from .repository import SQLiteRepository


PACKAGE_DIR = Path(__file__).parent


class ApprovalInput(BaseModel):
    reviewer: str
    note: str = ""


def create_app(database_path: str | Path | None = None) -> FastAPI:
    configured = database_path or os.environ.get("SKILLS_VECTOR_DB", "data/skills_vector.db")
    repository = SQLiteRepository(configured)
    service = SkillsVectorService(repository)
    app = FastAPI(title="Skills Vector", version="0.2.0", docs_url="/api/docs")
    app.state.service = service
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="dashboard.html", context={"roles": service.roles()})

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "private": True, "role_count": repository.role_count()}

    @app.get("/api/roles")
    def list_roles() -> list[dict[str, object]]:
        return service.roles()

    @app.post("/api/roles/{role_slug}/investigations", status_code=201)
    def investigate(role_slug: str) -> dict[str, object]:
        try:
            return service.investigate(role_slug)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/roles/{role_slug}/latest")
    def latest(role_slug: str) -> dict[str, object]:
        try:
            run = service.latest(role_slug)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="role not found") from exc
        if run is None:
            raise HTTPException(status_code=404, detail="no investigation for this role")
        return run

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        run = service.run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/runs/{run_id}/trace")
    def get_trace(run_id: str) -> dict[str, object]:
        trace = service.trace(run_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="run not found")
        return trace

    @app.get("/api/runs/{run_id}/evidence")
    def get_evidence(
        run_id: str,
        cluster: str | None = None,
        stage: str | None = None,
        lens: str | None = None,
        status: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        evidence = service.evidence(
            run_id, cluster_key=cluster, stage_key=stage, lens=lens,
            status=status, limit=limit, offset=offset,
        )
        if evidence is None:
            raise HTTPException(status_code=404, detail="run not found")
        return evidence

    @app.get("/api/runs/{run_id}/invocations/{invocation_id}")
    def get_invocation(run_id: str, invocation_id: str) -> dict[str, object]:
        invocation = service.invocation(run_id, invocation_id)
        if invocation is None:
            raise HTTPException(status_code=404, detail="invocation not found")
        return invocation

    @app.post("/api/runs/{run_id}/approve")
    def approve(run_id: str, approval: ApprovalInput) -> dict[str, object]:
        try:
            return service.approve(run_id, approval.reviewer, approval.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app
