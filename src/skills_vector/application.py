"""Application service coordinating the workflow and repository boundaries."""

from __future__ import annotations

from datetime import date
from typing import Any

from .domain import Role
from .engine import execute_investigation
from .repository import Repository


class SkillsVectorService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.repository.initialize()

    def roles(self) -> list[dict[str, Any]]:
        return self.repository.list_roles()  # type: ignore[attr-defined]

    def investigate(self, role_slug: str, *, as_of: date | None = None) -> dict[str, Any]:
        role = Role(role_slug)
        run_id = self.repository.create_run(role)
        try:
            evidence = self.repository.evidence_for_role(role)
            result = execute_investigation(role, evidence, as_of=as_of)
            self.repository.save_draft(run_id, result["artifacts"], result["brief"])
        except Exception as exc:
            self.repository.fail_run(run_id, str(exc))  # type: ignore[attr-defined]
            raise
        run = self.repository.get_run(run_id)
        assert run is not None
        return run

    def run(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_run(run_id)

    def latest(self, role_slug: str) -> dict[str, Any] | None:
        return self.repository.latest_run_for_role(Role(role_slug))  # type: ignore[attr-defined]

    def approve(self, run_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
        return self.repository.approve(run_id, reviewer, note)
