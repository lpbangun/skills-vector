"""Replaceable repository boundary with a reliable local SQLite implementation."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from .domain import EvidenceCategory, EvidenceSource, Role, validate_ingested_evidence
from .seed import ROLE_METADATA, SEED_EVIDENCE


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository(Protocol):
    def initialize(self) -> None: ...
    def evidence_for_role(self, role: Role) -> list[dict[str, Any]]: ...
    def create_run(self, role: Role) -> str: ...
    def save_draft(self, run_id: str, artifacts: list[dict[str, Any]], brief: dict[str, Any]) -> None: ...
    def approve(self, run_id: str, reviewer: str, note: str = "") -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...


class SQLiteRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self._connection() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS roles (
                    slug TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
                    geography TEXT NOT NULL, context TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    source_id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL,
                    publisher TEXT NOT NULL, url TEXT NOT NULL, published_on TEXT NOT NULL,
                    relevant_excerpt TEXT NOT NULL, provenance TEXT NOT NULL, geography TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS role_evidence (
                    role_slug TEXT NOT NULL REFERENCES roles(slug),
                    source_id TEXT NOT NULL REFERENCES evidence(source_id),
                    PRIMARY KEY (role_slug, source_id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, role_slug TEXT NOT NULL REFERENCES roles(slug),
                    status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    stage TEXT NOT NULL, stage_order INTEGER NOT NULL, kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS briefs (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    role_slug TEXT NOT NULL REFERENCES roles(slug), version INTEGER NOT NULL,
                    state TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    reviewer TEXT NOT NULL, note TEXT NOT NULL, decision TEXT NOT NULL,
                    decided_at TEXT NOT NULL
                );
                """
            )
            for role, metadata in ROLE_METADATA.items():
                db.execute(
                    "INSERT OR REPLACE INTO roles VALUES (?, ?, ?, ?, ?)",
                    (role.value, metadata["name"], "People Operations & Talent", "US", metadata["context"]),
                )
            for item in SEED_EVIDENCE:
                roles = tuple(Role(value) for value in item["role_connections"])
                source = EvidenceSource(
                    source_id=str(item["source_id"]),
                    category=EvidenceCategory(item["category"]),
                    title=str(item["title"]), publisher=str(item["publisher"]), url=str(item["url"]),
                    published_on=datetime.fromisoformat(str(item["published_on"])).date(),
                    retrieved_at=datetime.now(UTC), relevant_excerpt=str(item["relevant_excerpt"]),
                    provenance=str(item["provenance"]), role_connections=roles,
                )
                for role in roles:
                    errors = validate_ingested_evidence(source, role=role)
                    if errors:
                        raise ValueError("; ".join(errors))
                db.execute(
                    "INSERT OR REPLACE INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'US')",
                    (source.source_id, source.category.value, source.title, source.publisher, source.url,
                     source.published_on.isoformat(), source.relevant_excerpt, source.provenance),
                )
                for role in roles:
                    db.execute("INSERT OR IGNORE INTO role_evidence VALUES (?, ?)", (role.value, source.source_id))

    def role_count(self) -> int:
        with self._connection() as db:
            return int(db.execute("SELECT COUNT(*) FROM roles").fetchone()[0])

    def evidence_for_role(self, role: Role) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT e.* FROM evidence e JOIN role_evidence re USING(source_id) WHERE re.role_slug=? ORDER BY e.category, e.source_id",
                (role.value,),
            ).fetchall()
        return [dict(row) | {"role_connections": [role.value]} for row in rows]

    def list_roles(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT r.*, (SELECT id FROM runs x WHERE x.role_slug=r.slug ORDER BY started_at DESC LIMIT 1) latest_run_id,
                (SELECT status FROM runs x WHERE x.role_slug=r.slug ORDER BY started_at DESC LIMIT 1) latest_status,
                (SELECT COUNT(*) FROM runs x WHERE x.role_slug=r.slug) run_count FROM roles r ORDER BY CASE slug
                WHEN 'hr_coordinator' THEN 1 WHEN 'recruiter' THEN 2 ELSE 3 END"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_run(self, role: Role) -> str:
        run_id = str(uuid.uuid4())
        with self._lock, self._connection() as db:
            db.execute("INSERT INTO runs VALUES (?, ?, 'running', ?, NULL)", (run_id, role.value, utc_now()))
        return run_id

    def fail_run(self, run_id: str, message: str) -> None:
        with self._lock, self._connection() as db:
            db.execute("UPDATE runs SET status='failed', completed_at=? WHERE id=?", (utc_now(), run_id))
            db.execute(
                "INSERT INTO artifacts(run_id,stage,stage_order,kind,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (run_id, "failure", 999, "error", json.dumps({"message": message}), utc_now()),
            )

    def save_draft(self, run_id: str, artifacts: list[dict[str, Any]], brief: dict[str, Any]) -> None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT role_slug FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("run not found")
            role_slug = row["role_slug"]
            version = int(db.execute("SELECT COUNT(*) FROM briefs WHERE role_slug=?", (role_slug,)).fetchone()[0]) + 1
            db.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
            for index, artifact in enumerate(artifacts):
                db.execute(
                    "INSERT INTO artifacts(run_id,stage,stage_order,kind,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (run_id, artifact["stage"], int(artifact.get("order", index)), artifact.get("kind", "stage"),
                     json.dumps(artifact.get("payload", {}), sort_keys=True), utc_now()),
                )
            brief_id = str(uuid.uuid4())
            brief = dict(brief) | {"id": brief_id, "version": version, "state": "awaiting_approval"}
            db.execute(
                "INSERT INTO briefs VALUES (?, ?, ?, ?, 'awaiting_approval', ?, ?, NULL)",
                (brief_id, run_id, role_slug, version, json.dumps(brief, sort_keys=True), utc_now()),
            )
            db.execute("UPDATE runs SET status='awaiting_approval', completed_at=? WHERE id=?", (utc_now(), run_id))

    def approve(self, run_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("reviewer is required")
        with self._lock, self._connection() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("run not found")
            if row["status"] != "awaiting_approval":
                raise ValueError("only an awaiting-approval run can be approved")
            decided_at = utc_now()
            db.execute("INSERT INTO approvals(run_id,reviewer,note,decision,decided_at) VALUES(?,?,?,?,?)",
                       (run_id, reviewer, note.strip(), "approved", decided_at))
            brief_row = db.execute("SELECT payload_json FROM briefs WHERE run_id=?", (run_id,)).fetchone()
            payload = json.loads(brief_row["payload_json"])
            payload["state"] = "approved"
            payload["approval"] = {"reviewer": reviewer, "note": note.strip(), "decision": "approved", "decided_at": decided_at}
            db.execute("UPDATE briefs SET state='approved', payload_json=?, approved_at=? WHERE run_id=?",
                       (json.dumps(payload, sort_keys=True), decided_at, run_id))
            db.execute("UPDATE runs SET status='approved' WHERE id=?", (run_id,))
            db.execute(
                "INSERT INTO artifacts(run_id,stage,stage_order,kind,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (run_id, "human_review", 80, "approval", json.dumps({
                    "status": "approved", "reviewer": reviewer, "note": note.strip(),
                    "decision": "approved", "decided_at": decided_at,
                }, sort_keys=True), decided_at),
            )
        result = self.get_run(run_id)
        assert result is not None
        return result

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT x.*, r.name role_name, r.context role_context FROM runs x JOIN roles r ON r.slug=x.role_slug WHERE x.id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            artifacts = db.execute("SELECT stage,stage_order,kind,payload_json,created_at FROM artifacts WHERE run_id=? ORDER BY stage_order,id", (run_id,)).fetchall()
            brief_row = db.execute("SELECT payload_json FROM briefs WHERE run_id=?", (run_id,)).fetchone()
            approval_row = db.execute("SELECT reviewer,note,decision,decided_at FROM approvals WHERE run_id=?", (run_id,)).fetchone()
        result = dict(row)
        result["artifacts"] = [dict(item) | {"payload": json.loads(item["payload_json"])} for item in artifacts]
        for item in result["artifacts"]:
            item.pop("payload_json", None)
        result["brief"] = json.loads(brief_row["payload_json"]) if brief_row else None
        result["approval"] = dict(approval_row) if approval_row else None
        return result

    def latest_run_for_role(self, role: Role) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT id FROM runs WHERE role_slug=? ORDER BY started_at DESC LIMIT 1", (role.value,)).fetchone()
        return self.get_run(row["id"]) if row else None

    def counts(self) -> dict[str, int]:
        with self._connection() as db:
            return {table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in ("roles", "evidence", "runs", "artifacts", "briefs", "approvals")}
