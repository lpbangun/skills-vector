"""Application service joining configuration, workflow, and persistence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from .domain import HumanReview, ReviewDecision, Role, RoleBrief
from .operating_loop import InvestigationRunResult, run_investigation
from .persistence import InvestigationStore, RunRecord
from .runtime import select_runtime


class InvestigationService:
    def __init__(self, database_path: str | Path, *, runtime_mode: str = "stub") -> None:
        self.database_path = Path(database_path)
        self.runtime_mode = runtime_mode

    def _store(self) -> InvestigationStore:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        return InvestigationStore(self.database_path)

    def create_investigation(
        self,
        *,
        role: Role,
        as_of: date,
        full_refresh: bool = False,
        open_disagreements: bool = False,
    ) -> InvestigationRunResult:
        runtime = select_runtime(self.runtime_mode)
        with self._store() as store:
            return run_investigation(
                store,
                role=role,
                as_of=as_of,
                runtime=runtime,
                full_refresh=full_refresh,
                open_disagreements=open_disagreements,
            )

    def list_runs(self, *, role: Role | None = None, limit: int = 50) -> tuple[RunRecord, ...]:
        with self._store() as store:
            return store.list_runs(role=role, limit=limit)

    def get_run(self, run_id: str) -> RunRecord:
        with self._store() as store:
            return store.run_record(run_id)

    def get_events(self, run_id: str):
        with self._store() as store:
            store.run_record(run_id)
            return store.events(run_id)

    def get_brief(self, run_id: str) -> RoleBrief:
        with self._store() as store:
            store.run_record(run_id)
            return store.role_brief(run_id)

    def review_run(
        self,
        run_id: str,
        *,
        decision: ReviewDecision,
        reviewer: str,
        note: str = "",
    ) -> HumanReview:
        review = HumanReview(decision, reviewer, datetime.now(UTC), note)
        with self._store() as store:
            return store.record_review(run_id, review)
