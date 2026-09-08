"""SQLite persistence for private investigation runs and the evidence backlog."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .domain import (
    Claim,
    ClaimImpact,
    ClaimKind,
    EvidenceCategory,
    EvidenceSource,
    HumanReview,
    ReviewDecision,
    Role,
    RoleBrief,
    RoleBriefRequest,
    Scenario,
    ScenarioHorizon,
)


class BacklogState(StrEnum):
    QUEUED = "queued"
    RESEARCHING = "researching"
    INCORPORATED = "incorporated"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RetrievalFingerprint:
    url: str
    publisher: str
    published_on: date | None
    content_hash: str

    @property
    def digest(self) -> str:
        value = "\x1f".join(
            (self.url, self.publisher.casefold(), self.published_on.isoformat() if self.published_on else "", self.content_hash)
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    role: Role
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    node: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NodeEvent:
    run_id: str
    sequence: int
    node: str
    status: str
    artifact_ids: tuple[str, ...]
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BacklogItem:
    candidate_id: str
    role: Role
    lens: EvidenceCategory
    source: EvidenceSource
    fingerprint: RetrievalFingerprint
    state: BacklogState
    first_seen_at: datetime
    updated_at: datetime


_ALLOWED_TRANSITIONS = {
    BacklogState.QUEUED: {BacklogState.RESEARCHING, BacklogState.REJECTED},
    BacklogState.RESEARCHING: {BacklogState.INCORPORATED, BacklogState.REJECTED, BacklogState.QUEUED},
    BacklogState.INCORPORATED: {BacklogState.QUEUED},
    BacklogState.REJECTED: {BacklogState.QUEUED},
}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
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


def _source_to_json(source: EvidenceSource) -> str:
    return json.dumps(_jsonable(source), sort_keys=True, separators=(",", ":"))


def _source_from_json(value: str) -> EvidenceSource:
    raw = json.loads(value)
    return EvidenceSource(
        source_id=raw["source_id"],
        category=EvidenceCategory(raw["category"]),
        title=raw["title"],
        publisher=raw["publisher"],
        url=raw["url"],
        published_on=date.fromisoformat(raw["published_on"]) if raw["published_on"] else None,
        retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
        geography=raw["geography"],
    )


class InvestigationStore:
    """Small persistence boundary; callers own its lifetime."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "InvestigationStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, role TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                node TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL,
                node TEXT NOT NULL, status TEXT NOT NULL, artifact_ids TEXT NOT NULL,
                details TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS backlog (
                candidate_id TEXT PRIMARY KEY, role TEXT NOT NULL, lens TEXT NOT NULL,
                source TEXT NOT NULL, fingerprint_digest TEXT NOT NULL,
                fingerprint_url TEXT NOT NULL, fingerprint_publisher TEXT NOT NULL,
                fingerprint_published_on TEXT, content_hash TEXT NOT NULL,
                state TEXT NOT NULL, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(role, fingerprint_digest)
            );
            CREATE TABLE IF NOT EXISTS reviews (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                decision TEXT NOT NULL, reviewer TEXT NOT NULL,
                note TEXT NOT NULL, reviewed_at TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    def start_run(self, role: Role) -> RunRecord:
        record = RunRecord(uuid4().hex, role, "running", datetime.now(UTC))
        self._db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            (record.run_id, record.role.value, record.status, record.created_at.isoformat()),
        )
        self._db.commit()
        return record

    def set_run_status(self, run_id: str, status: str) -> None:
        cursor = self._db.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))
        if cursor.rowcount != 1:
            raise KeyError(run_id)
        self._db.commit()

    def write_artifact(self, run_id: str, node: str, kind: str, payload: Any) -> str:
        artifact_id = uuid4().hex
        self._db.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
            (artifact_id, run_id, node, kind, json.dumps(_jsonable(payload), sort_keys=True), datetime.now(UTC).isoformat()),
        )
        self._db.commit()
        return artifact_id

    def append_event(
        self,
        run_id: str,
        node: str,
        status: str,
        *,
        artifact_ids: Iterable[str] = (),
        details: dict[str, Any] | None = None,
    ) -> NodeEvent:
        artifact_references = tuple(artifact_ids)
        for artifact_id in artifact_references:
            artifact = self._db.execute(
                "SELECT run_id FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if artifact is None:
                raise ValueError(f"event references missing artifact: {artifact_id}")
            if artifact["run_id"] != run_id:
                raise ValueError(f"event references artifact from another run: {artifact_id}")
        row = self._db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        event = NodeEvent(
            run_id, int(row["next_sequence"]), node, status, artifact_references, details or {}, datetime.now(UTC)
        )
        self._db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.run_id, event.sequence, event.node, event.status,
                json.dumps(event.artifact_ids), json.dumps(_jsonable(event.details), sort_keys=True), event.created_at.isoformat(),
            ),
        )
        self._db.commit()
        return event

    def events(self, run_id: str) -> tuple[NodeEvent, ...]:
        rows = self._db.execute("SELECT * FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
        return tuple(
            NodeEvent(
                row["run_id"], row["sequence"], row["node"], row["status"],
                tuple(json.loads(row["artifact_ids"])), json.loads(row["details"]), datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def artifacts(self, run_id: str) -> tuple[ArtifactRecord, ...]:
        rows = self._db.execute("SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)).fetchall()
        return tuple(
            ArtifactRecord(
                row["artifact_id"], row["run_id"], row["node"], row["kind"],
                json.loads(row["payload"]), datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def enqueue_candidate(
        self,
        role: Role,
        lens: EvidenceCategory,
        source: EvidenceSource,
        content_hash: str,
    ) -> tuple[BacklogItem, bool]:
        fingerprint = RetrievalFingerprint(source.url, source.publisher, source.published_on, content_hash)
        existing = self._db.execute(
            "SELECT candidate_id FROM backlog WHERE role = ? AND fingerprint_digest = ?",
            (role.value, fingerprint.digest),
        ).fetchone()
        if existing:
            return self.backlog_item(existing["candidate_id"]), False
        now = datetime.now(UTC)
        candidate_id = uuid4().hex
        self._db.execute(
            "INSERT INTO backlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id, role.value, lens.value, _source_to_json(source), fingerprint.digest,
                fingerprint.url, fingerprint.publisher, fingerprint.published_on.isoformat() if fingerprint.published_on else None,
                fingerprint.content_hash, BacklogState.QUEUED.value, now.isoformat(), now.isoformat(),
            ),
        )
        self._db.commit()
        return self.backlog_item(candidate_id), True

    def backlog_item(self, candidate_id: str) -> BacklogItem:
        row = self._db.execute("SELECT * FROM backlog WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        fingerprint = RetrievalFingerprint(
            row["fingerprint_url"], row["fingerprint_publisher"],
            date.fromisoformat(row["fingerprint_published_on"]) if row["fingerprint_published_on"] else None,
            row["content_hash"],
        )
        return BacklogItem(
            row["candidate_id"], Role(row["role"]), EvidenceCategory(row["lens"]), _source_from_json(row["source"]),
            fingerprint, BacklogState(row["state"]), datetime.fromisoformat(row["first_seen_at"]), datetime.fromisoformat(row["updated_at"]),
        )

    def list_backlog(self, role: Role, states: Iterable[BacklogState] | None = None) -> tuple[BacklogItem, ...]:
        state_values = tuple(state.value for state in states) if states is not None else ()
        sql = "SELECT candidate_id FROM backlog WHERE role = ?"
        parameters: list[Any] = [role.value]
        if state_values:
            sql += " AND state IN (" + ",".join("?" for _ in state_values) + ")"
            parameters.extend(state_values)
        sql += " ORDER BY first_seen_at, candidate_id"
        rows = self._db.execute(sql, parameters).fetchall()
        return tuple(self.backlog_item(row["candidate_id"]) for row in rows)

    def transition_backlog(self, candidate_id: str, state: BacklogState) -> BacklogItem:
        item = self.backlog_item(candidate_id)
        if state is item.state:
            return item
        if state not in _ALLOWED_TRANSITIONS[item.state]:
            raise ValueError(f"invalid backlog transition: {item.state.value} -> {state.value}")
        self._db.execute(
            "UPDATE backlog SET state = ?, updated_at = ? WHERE candidate_id = ?",
            (state.value, datetime.now(UTC).isoformat(), candidate_id),
        )
        self._db.commit()
        return self.backlog_item(candidate_id)

    def has_prior_draft(self, role: Role) -> bool:
        row = self._db.execute(
            """
            SELECT 1 FROM artifacts
            JOIN runs USING (run_id)
            WHERE runs.role = ? AND artifacts.kind = 'role_brief'
            LIMIT 1
            """,
            (role.value,),
        ).fetchone()
        return row is not None

    def run_record(self, run_id: str) -> RunRecord:
        row = self._db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord(row["run_id"], Role(row["role"]), row["status"], datetime.fromisoformat(row["created_at"]))

    def list_runs(self, *, role: Role | None = None, limit: int = 50) -> tuple[RunRecord, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("run list limit must be between 1 and 200")
        sql = "SELECT * FROM runs"
        parameters: list[Any] = []
        if role is not None:
            sql += " WHERE role = ?"
            parameters.append(role.value)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        rows = self._db.execute(sql, parameters).fetchall()
        return tuple(
            RunRecord(row["run_id"], Role(row["role"]), row["status"], datetime.fromisoformat(row["created_at"]))
            for row in rows
        )

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

    def role_brief(self, run_id: str) -> RoleBrief:
        row = self._db.execute(
            "SELECT payload FROM artifacts WHERE run_id = ? AND kind = 'role_brief' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"role brief for run {run_id}")
        return _brief_from_payload(json.loads(row["payload"]), review=self.review(run_id))

    def record_review(self, run_id: str, review: HumanReview) -> HumanReview:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            record = self.run_record(run_id)
            existing = self.review(run_id)
            if existing is not None:
                if (
                    existing.decision is review.decision
                    and existing.reviewer == review.reviewer
                    and existing.note == review.note
                ):
                    self._db.rollback()
                    return existing
                raise ValueError("run has already been reviewed")
            if record.status != "awaiting_human_review":
                raise ValueError(f"run is not awaiting human review: {record.status}")
            self.role_brief(run_id)
            status = {
                ReviewDecision.APPROVED: "approved",
                ReviewDecision.CHANGES_REQUESTED: "changes_requested",
                ReviewDecision.REJECTED: "rejected",
            }[review.decision]
            artifact_id = uuid4().hex
            now = datetime.now(UTC)
            self._db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    run_id,
                    "human_review",
                    "human_review",
                    json.dumps(_jsonable(review), sort_keys=True),
                    now.isoformat(),
                ),
            )
            self._db.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
                (run_id, review.decision.value, review.reviewer, review.note, review.reviewed_at.isoformat()),
            )
            self._db.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))
            row = self._db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            self._db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    int(row["next_sequence"]),
                    "human_review",
                    "completed",
                    json.dumps((artifact_id,)),
                    json.dumps({"decision": review.decision.value, "private": True}, sort_keys=True),
                    now.isoformat(),
                ),
            )
            self._db.commit()
            return review
        except Exception:
            self._db.rollback()
            raise


def _brief_from_payload(raw: dict[str, Any], *, review: HumanReview | None = None) -> RoleBrief:
    request_raw = raw["request"]
    request = RoleBriefRequest(
        Role(request_raw["role"]),
        date.fromisoformat(request_raw["as_of"]),
        request_raw.get("geography", "US"),
        request_raw.get("domain", "people_operations_and_talent"),
    )
    sources = tuple(_source_from_json(json.dumps(item)) for item in raw["sources"])
    claims = tuple(
        Claim(
            claim_id=item["claim_id"],
            kind=ClaimKind(item["kind"]),
            statement=item["statement"],
            evidence_ids=tuple(item["evidence_ids"]),
            impact=ClaimImpact(item.get("impact", ClaimImpact.ROUTINE.value)),
            uncertainty_note=item.get("uncertainty_note", ""),
            disagreement_note=item.get("disagreement_note", ""),
        )
        for item in raw["claims"]
    )
    scenarios = tuple(
        Scenario(
            ScenarioHorizon(item["horizon"]),
            item["description"],
            tuple(item["evidence_ids"]),
            item["uncertainty_note"],
        )
        for item in raw["scenarios"]
    )
    return RoleBrief(
        request=request,
        summary=raw["summary"],
        claims=claims,
        scenarios=scenarios,
        sources=sources,
        review=review,
        private=bool(raw.get("private", True)),
        generated_at=datetime.fromisoformat(raw["generated_at"]),
    )
