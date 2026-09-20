"""SQLite catalog: occupations, evidence, runs, checkpoints, reviews, releases."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .budget import BudgetLedger, DEFAULT_MONTHLY_CAP_USD
from .ids import canonical_json, content_hash, stable_id
from .occupational import (
    ChangeEvent,
    ChangeKind,
    Claim,
    ClaimStatus,
    HumanReview,
    Occupation,
    Passage,
    ProficiencyLevel,
    ProficiencyRubric,
    RecordState,
    ReviewDecision,
    RoleRequirement,
    RunPhase,
    RunStatus,
    Skill,
    SourceKind,
    SourceRecord,
    Task,
    TaskSkillLink,
    WorkContext,
    EvidenceWeight,
)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ResearchRun:
    run_id: str
    occupation_id: str
    status: RunStatus
    phase: RunPhase
    created_at: datetime
    updated_at: datetime
    thread_id: str
    allow_fixtures: bool
    forecast_included: bool = False


class CatalogStore:
    def __init__(self, path: str | Path = ":memory:", *, monthly_cap_usd: float = DEFAULT_MONTHLY_CAP_USD) -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self.budget = BudgetLedger(self._db, monthly_cap_usd=monthly_cap_usd)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "CatalogStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS occupations (
                occupation_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contexts (
                context_id TEXT PRIMARY KEY,
                occupation_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                occupation_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_skills (
                task_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                note TEXT NOT NULL,
                PRIMARY KEY (task_id, skill_id)
            );
            CREATE TABLE IF NOT EXISTS rubrics (
                rubric_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirements (
                requirement_id TEXT PRIMARY KEY,
                occupation_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                url TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(url, content_hash)
            );
            CREATE TABLE IF NOT EXISTS passages (
                passage_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                occupation_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS research_runs (
                run_id TEXT PRIMARY KEY,
                occupation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                thread_id TEXT NOT NULL UNIQUE,
                allow_fixtures INTEGER NOT NULL,
                forecast_included INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, phase)
            );
            CREATE TABLE IF NOT EXISTS reviews (
                run_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                note TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS change_events (
                change_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                supersedes TEXT,
                payload_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS releases (
                release_id TEXT PRIMARY KEY,
                previous_release_id TEXT,
                created_at TEXT NOT NULL,
                path TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                approved_run_ids TEXT NOT NULL,
                rolled_back INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS current_release (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                release_id TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    def start_run(
        self,
        occupation_id: str,
        *,
        allow_fixtures: bool,
        run_id: str | None = None,
        forecast_included: bool = False,
    ) -> ResearchRun:
        now = datetime.now(UTC)
        assigned = run_id or uuid4().hex
        self._db.execute(
            "INSERT INTO research_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assigned,
                occupation_id,
                RunStatus.RUNNING.value,
                RunPhase.COLLECT.value,
                assigned,
                int(allow_fixtures),
                int(forecast_included),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        self._db.commit()
        return self.run(assigned)

    def run(self, run_id: str) -> ResearchRun:
        row = self._db.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return ResearchRun(
            row["run_id"],
            row["occupation_id"],
            RunStatus(row["status"]),
            RunPhase(row["phase"]),
            datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["updated_at"]),
            row["thread_id"],
            bool(row["allow_fixtures"]),
            bool(row["forecast_included"]),
        )

    def set_run(self, run_id: str, *, status: RunStatus | None = None, phase: RunPhase | None = None) -> ResearchRun:
        current = self.run(run_id)
        next_status = status or current.status
        next_phase = phase or current.phase
        self._db.execute(
            "UPDATE research_runs SET status = ?, phase = ?, updated_at = ? WHERE run_id = ?",
            (next_status.value, next_phase.value, datetime.now(UTC).isoformat(), run_id),
        )
        self._db.commit()
        return self.run(run_id)

    def write_checkpoint(self, run_id: str, phase: RunPhase, payload: dict[str, Any]) -> None:
        encoded = canonical_json(_jsonable(payload))
        self._db.execute(
            """
            INSERT INTO checkpoints (run_id, phase, payload, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, phase) DO UPDATE SET payload = excluded.payload, created_at = excluded.created_at
            """,
            (run_id, phase.value, encoded, datetime.now(UTC).isoformat()),
        )
        self._db.commit()

    def checkpoint(self, run_id: str, phase: RunPhase) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT payload FROM checkpoints WHERE run_id = ? AND phase = ?",
            (run_id, phase.value),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_checkpoint(self, run_id: str) -> tuple[RunPhase, dict[str, Any]] | None:
        row = self._db.execute(
            "SELECT phase, payload FROM checkpoints WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunPhase(row["phase"]), json.loads(row["payload"])

    def upsert_occupation(self, occupation: Occupation) -> bool:
        payload = canonical_json(_jsonable(occupation))
        digest = content_hash(payload)
        existing = self._db.execute(
            "SELECT content_hash FROM occupations WHERE occupation_id = ?",
            (occupation.occupation_id,),
        ).fetchone()
        if existing and existing["content_hash"] == digest:
            return False
        if existing:
            self._record_change("occupation", occupation.occupation_id, ChangeKind.UPDATED, digest)
        else:
            self._record_change("occupation", occupation.occupation_id, ChangeKind.ADDED, digest)
        self._db.execute(
            """
            INSERT INTO occupations VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(occupation_id) DO UPDATE SET
                payload = excluded.payload, content_hash = excluded.content_hash,
                state = excluded.state, version = excluded.version
            """,
            (occupation.occupation_id, payload, digest, occupation.state.value, occupation.version),
        )
        self._db.commit()
        return True

    def upsert_task(self, task: Task) -> bool:
        return self._upsert_named("tasks", "task_id", task.task_id, task, task.occupation_id, task.state, task.version)

    def upsert_skill(self, skill: Skill) -> bool:
        payload = canonical_json(_jsonable(skill))
        digest = content_hash(payload)
        existing = self._db.execute("SELECT content_hash FROM skills WHERE skill_id = ?", (skill.skill_id,)).fetchone()
        if existing and existing["content_hash"] == digest:
            return False
        kind = ChangeKind.UPDATED if existing else ChangeKind.ADDED
        self._record_change("skill", skill.skill_id, kind, digest)
        self._db.execute(
            """
            INSERT INTO skills VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(skill_id) DO UPDATE SET
                payload = excluded.payload, content_hash = excluded.content_hash,
                state = excluded.state, version = excluded.version
            """,
            (skill.skill_id, payload, digest, skill.state.value, skill.version),
        )
        self._db.commit()
        return True

    def upsert_requirement(self, requirement: RoleRequirement) -> bool:
        return self._upsert_named(
            "requirements",
            "requirement_id",
            requirement.requirement_id,
            requirement,
            requirement.occupation_id,
            requirement.state,
            requirement.version,
        )

    def _upsert_named(
        self,
        table: str,
        key: str,
        entity_id: str,
        record: Any,
        occupation_id: str,
        state: RecordState,
        version: int,
    ) -> bool:
        payload = canonical_json(_jsonable(record))
        digest = content_hash(payload)
        existing = self._db.execute(f"SELECT content_hash FROM {table} WHERE {key} = ?", (entity_id,)).fetchone()
        if existing and existing["content_hash"] == digest:
            return False
        kind = ChangeKind.UPDATED if existing else ChangeKind.ADDED
        self._record_change(table.rstrip("s"), entity_id, kind, digest)
        self._db.execute(
            f"""
            INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT({key}) DO UPDATE SET
                payload = excluded.payload, content_hash = excluded.content_hash,
                state = excluded.state, version = excluded.version
            """,
            (entity_id, occupation_id, payload, digest, state.value, version),
        )
        self._db.commit()
        return True

    def put_context(self, context: WorkContext) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO contexts VALUES (?, ?, ?)",
            (context.context_id, context.occupation_id, canonical_json(_jsonable(context))),
        )
        self._db.commit()

    def put_rubric(self, rubric: ProficiencyRubric) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO rubrics VALUES (?, ?)",
            (rubric.rubric_id, canonical_json(_jsonable(rubric))),
        )
        self._db.commit()

    def put_task_skill(self, link: TaskSkillLink) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO task_skills VALUES (?, ?, ?, ?)",
            (link.task_id, link.skill_id, link.relationship, link.note),
        )
        self._db.commit()

    def put_source(self, source: SourceRecord) -> bool:
        payload = canonical_json(_jsonable(source))
        existing = self._db.execute(
            "SELECT source_id FROM sources WHERE url = ? AND content_hash = ?",
            (source.url, source.content_hash),
        ).fetchone()
        if existing:
            return False
        self._db.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?)",
            (source.source_id, source.content_hash, source.url, payload),
        )
        self._db.commit()
        return True

    def put_passage(self, passage: Passage) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO passages VALUES (?, ?, ?)",
            (passage.passage_id, passage.source_id, canonical_json(_jsonable(passage))),
        )
        self._db.commit()

    def put_claim(self, claim: Claim) -> None:
        payload = canonical_json(_jsonable(claim))
        self._db.execute(
            "INSERT OR REPLACE INTO claims VALUES (?, ?, ?, ?)",
            (claim.claim_id, claim.occupation_id, payload, content_hash(payload)),
        )
        self._db.commit()

    def occupation(self, occupation_id: str) -> Occupation:
        row = self._db.execute("SELECT payload FROM occupations WHERE occupation_id = ?", (occupation_id,)).fetchone()
        if row is None:
            raise KeyError(occupation_id)
        return self._from_json(row["payload"], Occupation)

    def tasks(self, occupation_id: str) -> tuple[Task, ...]:
        rows = self._db.execute("SELECT payload FROM tasks WHERE occupation_id = ?", (occupation_id,)).fetchall()
        return tuple(self._from_json(row["payload"], Task) for row in rows)

    def skills(self) -> tuple[Skill, ...]:
        rows = self._db.execute("SELECT payload FROM skills").fetchall()
        return tuple(self._from_json(row["payload"], Skill) for row in rows)

    def requirements(self, occupation_id: str) -> tuple[RoleRequirement, ...]:
        rows = self._db.execute("SELECT payload FROM requirements WHERE occupation_id = ?", (occupation_id,)).fetchall()
        return tuple(self._from_json(row["payload"], RoleRequirement) for row in rows)

    def sources(self) -> tuple[SourceRecord, ...]:
        rows = self._db.execute("SELECT payload FROM sources").fetchall()
        return tuple(self._from_json(row["payload"], SourceRecord) for row in rows)

    def passages(self) -> tuple[Passage, ...]:
        rows = self._db.execute("SELECT payload FROM passages").fetchall()
        return tuple(self._from_json(row["payload"], Passage) for row in rows)

    def claims(self, occupation_id: str | None = None) -> tuple[Claim, ...]:
        if occupation_id:
            rows = self._db.execute("SELECT payload FROM claims WHERE occupation_id = ?", (occupation_id,)).fetchall()
        else:
            rows = self._db.execute("SELECT payload FROM claims").fetchall()
        return tuple(self._from_json(row["payload"], Claim) for row in rows)

    def contexts(self, occupation_id: str) -> tuple[WorkContext, ...]:
        rows = self._db.execute("SELECT payload FROM contexts WHERE occupation_id = ?", (occupation_id,)).fetchall()
        return tuple(self._from_json(row["payload"], WorkContext) for row in rows)

    def rubrics(self) -> tuple[ProficiencyRubric, ...]:
        rows = self._db.execute("SELECT payload FROM rubrics").fetchall()
        return tuple(self._from_json(row["payload"], ProficiencyRubric) for row in rows)

    def task_skills(self) -> tuple[TaskSkillLink, ...]:
        rows = self._db.execute("SELECT * FROM task_skills").fetchall()
        return tuple(TaskSkillLink(row["task_id"], row["skill_id"], row["relationship"], row["note"]) for row in rows)

    def record_review(self, run_id: str, review: HumanReview) -> HumanReview:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            record = self.run(run_id)
            existing = self._db.execute("SELECT * FROM reviews WHERE run_id = ?", (run_id,)).fetchone()
            if existing:
                same = (
                    existing["decision"] == review.decision.value
                    and existing["reviewer"] == review.reviewer
                    and existing["note"] == review.note
                )
                if same:
                    self._db.rollback()
                    return review
                if ReviewDecision(existing["decision"]) is ReviewDecision.CHANGES_REQUESTED:
                    self._db.execute("DELETE FROM reviews WHERE run_id = ?", (run_id,))
                else:
                    self._db.rollback()
                    raise ValueError("run has a terminal review")
            if record.status not in {RunStatus.AWAITING_REVIEW, RunStatus.CHANGES_REQUESTED, RunStatus.APPROVED}:
                raise ValueError(f"run is not reviewable: {record.status.value}")
            if record.status is RunStatus.APPROVED and review.decision is not ReviewDecision.APPROVED:
                raise ValueError("approved run cannot be rejected in place; create a new run")
            status = {
                ReviewDecision.APPROVED: RunStatus.APPROVED,
                ReviewDecision.CHANGES_REQUESTED: RunStatus.CHANGES_REQUESTED,
                ReviewDecision.REJECTED: RunStatus.REJECTED,
            }[review.decision]
            self._db.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
                (run_id, review.decision.value, review.reviewer, review.note, review.reviewed_at.isoformat()),
            )
            self._db.execute(
                "UPDATE research_runs SET status = ?, phase = ?, updated_at = ? WHERE run_id = ?",
                (status.value, RunPhase.REVIEW.value, datetime.now(UTC).isoformat(), run_id),
            )
            self._db.commit()
            return review
        except Exception:
            self._db.rollback()
            raise

    def review(self, run_id: str) -> HumanReview | None:
        row = self._db.execute("SELECT * FROM reviews WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return HumanReview(
            ReviewDecision(row["decision"]),
            row["reviewer"],
            datetime.fromisoformat(row["reviewed_at"]),
            row["note"],
        )

    def approved_runs(self) -> tuple[ResearchRun, ...]:
        rows = self._db.execute(
            "SELECT run_id FROM research_runs WHERE status = ? ORDER BY updated_at",
            (RunStatus.APPROVED.value,),
        ).fetchall()
        return tuple(self.run(row["run_id"]) for row in rows)

    def add_release(self, release_id: str, path: Path, manifest_hash: str, run_ids: Iterable[str], previous: str | None) -> None:
        self._db.execute(
            "INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                release_id,
                previous,
                datetime.now(UTC).isoformat(),
                str(path),
                manifest_hash,
                json.dumps(list(run_ids)),
            ),
        )
        self._db.execute("INSERT OR REPLACE INTO current_release VALUES (1, ?)", (release_id,))
        self._db.commit()

    def current_release_id(self) -> str | None:
        row = self._db.execute("SELECT release_id FROM current_release WHERE singleton = 1").fetchone()
        return row["release_id"] if row else None

    def release_row(self, release_id: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM releases WHERE release_id = ?", (release_id,)).fetchone()
        if row is None:
            raise KeyError(release_id)
        return row

    def rollback_release(self, release_id: str) -> str:
        row = self.release_row(release_id)
        previous = row["previous_release_id"]
        if not previous:
            raise ValueError("cannot roll back the first release without a predecessor")
        self._db.execute("UPDATE releases SET rolled_back = 1 WHERE release_id = ?", (release_id,))
        self._db.execute("INSERT OR REPLACE INTO current_release VALUES (1, ?)", (previous,))
        self._db.commit()
        return previous

    def change_events(self) -> tuple[ChangeEvent, ...]:
        rows = self._db.execute("SELECT * FROM change_events ORDER BY occurred_at, change_id").fetchall()
        return tuple(
            ChangeEvent(
                row["change_id"],
                row["entity_type"],
                row["entity_id"],
                ChangeKind(row["kind"]),
                datetime.fromisoformat(row["occurred_at"]),
                row["supersedes"],
                row["payload_hash"],
            )
            for row in rows
        )

    def _record_change(self, entity_type: str, entity_id: str, kind: ChangeKind, payload_hash: str) -> None:
        occurred = datetime.now(UTC)
        change_id = stable_id("chg", entity_type, entity_id, kind.value, payload_hash, occurred.isoformat())
        self._db.execute(
            "INSERT OR IGNORE INTO change_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (change_id, entity_type, entity_id, kind.value, occurred.isoformat(), None, payload_hash),
        )

    def _from_json(self, payload: str, cls: type):
        raw = json.loads(payload)
        return _coerce(cls, raw)


def _coerce(cls: type, raw: dict[str, Any]):
    if cls is Occupation:
        return Occupation(
            raw["occupation_id"],
            raw["slug"],
            raw["title"],
            _enum(raw["family"]),
            raw.get("geography", "US"),
            raw.get("onet_code"),
            raw.get("onet_title"),
            raw.get("esco_code"),
            tuple(raw.get("aliases") or ()),
            RecordState(raw.get("state", RecordState.PROVISIONAL.value)),
            raw.get("version", 1),
        )
    if cls is Task:
        return Task(
            raw["task_id"],
            raw["occupation_id"],
            raw["statement"],
            raw["output"],
            raw["success_criteria"],
            raw.get("frequency", "recurring"),
            raw.get("criticality", "core"),
            tuple(raw.get("dependencies") or ()),
            raw.get("context_id"),
            RecordState(raw.get("state", RecordState.PROVISIONAL.value)),
            raw.get("version", 1),
        )
    if cls is Skill:
        return Skill(
            raw["skill_id"],
            raw["name"],
            raw["description"],
            raw.get("onet_element_id"),
            raw.get("esco_uri"),
            RecordState(raw.get("state", RecordState.PROVISIONAL.value)),
            raw.get("version", 1),
        )
    if cls is RoleRequirement:
        return RoleRequirement(
            raw["requirement_id"],
            raw["occupation_id"],
            raw["skill_id"],
            tuple(raw["task_ids"]),
            ProficiencyLevel(raw["target_level"]),
            raw["rubric_id"],
            raw.get("context_id"),
            RecordState(raw.get("state", RecordState.PROVISIONAL.value)),
            raw.get("version", 1),
        )
    if cls is SourceRecord:
        return SourceRecord(
            raw["source_id"],
            SourceKind(raw["kind"]),
            EvidenceWeight(raw["weight"]),
            raw["title"],
            raw["publisher"],
            raw["url"],
            datetime.fromisoformat(raw["retrieved_at"]),
            raw["content_hash"],
            raw.get("parser_version", "retrieval.v1"),
            date.fromisoformat(raw["published_on"]) if raw.get("published_on") else None,
            raw.get("rights", "retain_attribution"),
            bool(raw.get("fixture", False)),
            bool(raw.get("retrieval_ok", True)),
            raw.get("failure_note", ""),
        )
    if cls is Passage:
        return Passage(raw["passage_id"], raw["source_id"], raw["locator"], raw["text"], raw.get("applicable_context", "US startup"))
    if cls is Claim:
        return Claim(
            raw["claim_id"],
            raw["occupation_id"],
            raw["statement"],
            raw["claim_type"],
            tuple(raw["passage_ids"]),
            tuple(raw["source_ids"]),
            ClaimStatus(raw.get("status", ClaimStatus.PROPOSED.value)),
            raw.get("uncertainty_note", ""),
            raw.get("disagreement_note", ""),
            bool(raw.get("model_agreement", False)),
            bool(raw.get("independent_corroboration", False)),
            raw.get("prompt_version", ""),
            raw.get("model_id", ""),
        )
    if cls is WorkContext:
        return WorkContext(
            raw["context_id"],
            raw["occupation_id"],
            raw["startup_stage"],
            raw.get("industry", "software"),
            raw.get("geography", "US"),
            raw.get("seniority", "ic"),
            raw.get("autonomy", "high"),
            tuple(raw.get("tools") or ()),
            raw.get("working_conditions", "small_team"),
            raw.get("organizational_scope", "company_wide"),
        )
    if cls is ProficiencyRubric:
        return ProficiencyRubric(
            raw["rubric_id"],
            raw["skill_id"],
            raw["occupation_id"],
            raw["version"],
            raw["levels"],
            bool(raw.get("provisional", True)),
        )
    raise TypeError(cls)


def _enum(value: str):
    from .occupational import JobFamily

    return JobFamily(value)
