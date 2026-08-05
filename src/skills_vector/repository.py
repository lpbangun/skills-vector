"""Replaceable repository boundary with a reliable local SQLite implementation."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from .domain import EvidenceCategory, EvidenceSource, Role, validate_ingested_evidence
from .seed import ROLE_METADATA, SEED_EVIDENCE
from .trace import (
    CLUSTER_LABELS,
    STAGE_DEFINITIONS,
    TRACE_EDGES,
    artifact_source_ids,
    artifact_summary,
    iter_claims,
    rank_source,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository(Protocol):
    def initialize(self) -> None: ...
    def evidence_for_role(self, role: Role) -> list[dict[str, Any]]: ...
    def create_run(self, role: Role) -> str: ...
    def save_draft(self, run_id: str, artifacts: list[dict[str, Any]], brief: dict[str, Any]) -> None: ...
    def approve(self, run_id: str, reviewer: str, note: str = "") -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def trace_for_run(self, run_id: str) -> dict[str, Any] | None: ...
    def evidence_for_run(self, run_id: str, **filters: Any) -> dict[str, Any] | None: ...
    def invocation_detail(self, run_id: str, invocation_id: str) -> dict[str, Any] | None: ...


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
                CREATE TABLE IF NOT EXISTS run_stages (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    stage_key TEXT NOT NULL, label TEXT NOT NULL, phase TEXT NOT NULL,
                    stage_order INTEGER NOT NULL, status TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT, duration_ms REAL,
                    invocation_count INTEGER NOT NULL, finding_count INTEGER NOT NULL,
                    source_count INTEGER NOT NULL, failure_count INTEGER NOT NULL,
                    summary TEXT NOT NULL, UNIQUE(run_id, stage_key)
                );
                CREATE TABLE IF NOT EXISTS invocations (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    stage_id TEXT NOT NULL REFERENCES run_stages(id), worker_key TEXT NOT NULL,
                    worker_kind TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT, duration_ms REAL,
                    summary TEXT NOT NULL, raw_payload_json TEXT NOT NULL, error TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    stage_id TEXT NOT NULL REFERENCES run_stages(id),
                    invocation_id TEXT REFERENCES invocations(id), finding_key TEXT NOT NULL,
                    finding_type TEXT NOT NULL, summary TEXT NOT NULL,
                    uncertainty TEXT NOT NULL, status TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL, UNIQUE(run_id, finding_key)
                );
                CREATE TABLE IF NOT EXISTS run_sources (
                    run_id TEXT NOT NULL REFERENCES runs(id), source_id TEXT NOT NULL,
                    category TEXT NOT NULL, title TEXT NOT NULL, publisher TEXT NOT NULL,
                    url TEXT NOT NULL, published_on TEXT NOT NULL,
                    relevant_excerpt TEXT NOT NULL, provenance TEXT NOT NULL,
                    role_connection TEXT NOT NULL, validation_status TEXT NOT NULL,
                    PRIMARY KEY(run_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS finding_sources (
                    finding_id TEXT NOT NULL REFERENCES findings(id), run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, relationship TEXT NOT NULL,
                    PRIMARY KEY(finding_id, source_id),
                    FOREIGN KEY(run_id, source_id) REFERENCES run_sources(run_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    claim_key TEXT NOT NULL, section_key TEXT NOT NULL,
                    cluster_key TEXT NOT NULL, claim_order INTEGER NOT NULL,
                    statement TEXT NOT NULL, impact TEXT NOT NULL,
                    uncertainty TEXT NOT NULL, disagreement TEXT NOT NULL,
                    validation_status TEXT NOT NULL, topic_tags_json TEXT NOT NULL,
                    UNIQUE(run_id, claim_key)
                );
                CREATE TABLE IF NOT EXISTS finding_claims (
                    finding_id TEXT NOT NULL REFERENCES findings(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id), relationship TEXT NOT NULL,
                    PRIMARY KEY(finding_id, claim_id)
                );
                CREATE TABLE IF NOT EXISTS claim_sources (
                    claim_id TEXT NOT NULL REFERENCES claims(id), run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, relationship TEXT NOT NULL,
                    citation_order INTEGER NOT NULL,
                    PRIMARY KEY(claim_id, source_id),
                    FOREIGN KEY(run_id, source_id) REFERENCES run_sources(run_id, source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_run_stages_run ON run_stages(run_id, stage_order);
                CREATE INDEX IF NOT EXISTS idx_invocations_stage ON invocations(stage_id);
                CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id, finding_key);
                CREATE INDEX IF NOT EXISTS idx_claims_run ON claims(run_id, claim_order);
                CREATE INDEX IF NOT EXISTS idx_claim_sources_run ON claim_sources(run_id, source_id);
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
            completeness = str(brief.get("completeness", "complete"))
            approval_eligible = bool(brief.get("approval_eligible", True))
            if completeness == "partial" and approval_eligible:
                run_status = "partial_awaiting_approval"
            elif completeness == "partial":
                run_status = "partial_blocked"
            else:
                run_status = "awaiting_approval"
            brief_state = run_status
            brief = dict(brief) | {
                "id": brief_id, "version": version, "state": brief_state,
                "approval_eligible": approval_eligible,
            }
            db.execute(
                "INSERT INTO briefs VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (brief_id, run_id, role_slug, version, brief_state, json.dumps(brief, sort_keys=True), utc_now()),
            )
            self._save_trace(db, run_id, artifacts, brief)
            db.execute("UPDATE runs SET status=?, completed_at=? WHERE id=?", (run_status, utc_now(), run_id))

    def _save_trace(
        self, db: sqlite3.Connection, run_id: str,
        artifacts: list[dict[str, Any]], brief: dict[str, Any],
    ) -> None:
        for table in (
            "finding_claims", "claim_sources", "finding_sources", "claims",
            "findings", "invocations", "run_stages", "run_sources",
        ):
            db.execute(f"DELETE FROM {table} WHERE run_id=?" if table not in {"finding_claims"} else
                       "DELETE FROM finding_claims WHERE claim_id IN (SELECT id FROM claims WHERE run_id=?)", (run_id,))

        for source in brief.get("sources", []):
            db.execute(
                """INSERT INTO run_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, source["source_id"], source["category"], source["title"],
                    source["publisher"], source["url"], source["published_on"],
                    source.get("relevant_excerpt", ""), source.get("provenance", ""),
                    source.get("role_connection", ""), "validated",
                ),
            )

        finding_ids_by_key: dict[str, str] = {}
        available_sources = {source["source_id"] for source in brief.get("sources", [])}
        for artifact in artifacts:
            stage_key = str(artifact["stage"])
            definition = STAGE_DEFINITIONS.get(stage_key, {
                "label": stage_key.replace("_", " ").title(),
                "phase": "other", "order": artifact.get("order", 999),
            })
            payload = artifact.get("payload", {})
            trace = artifact.get("trace", {})
            stage_id = f"{run_id}:{stage_key}"
            invocation_id = trace.get("invocation_id")
            summary = artifact_summary(stage_key, payload)
            source_ids = [source_id for source_id in artifact_source_ids(payload) if source_id in available_sources]
            has_finding = bool(invocation_id)
            status = str(trace.get("status") or payload.get("status") or "succeeded")
            db.execute(
                """INSERT INTO run_stages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stage_id, run_id, stage_key, definition["label"], definition["phase"],
                    int(definition["order"]), status, trace.get("started_at"),
                    trace.get("completed_at"), trace.get("duration_ms"),
                    1 if invocation_id else 0, 1 if has_finding else 0, len(set(source_ids)),
                    1 if status == "failed" else 0, summary,
                ),
            )
            if not invocation_id:
                continue
            db.execute(
                """INSERT INTO invocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    invocation_id, run_id, stage_id, trace.get("worker_key") or stage_key,
                    trace.get("worker_kind") or "deterministic", int(trace.get("attempt") or 1),
                    status, trace.get("started_at"), trace.get("completed_at"),
                    trace.get("duration_ms"), summary, json.dumps(payload, sort_keys=True),
                    str(trace.get("error") or payload.get("error") or ""),
                ),
            )
            finding_id = f"{invocation_id}:finding"
            finding_ids_by_key[stage_key] = finding_id
            db.execute(
                """INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    finding_id, run_id, stage_id, invocation_id, stage_key,
                    artifact.get("kind", "stage"), summary,
                    str(payload.get("uncertainty", "")), status,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            for source_id in source_ids:
                db.execute(
                    "INSERT OR IGNORE INTO finding_sources VALUES (?,?,?,?)",
                    (finding_id, run_id, source_id, "used"),
                )

        for claim in iter_claims(brief):
            claim_id = f"{run_id}:{claim['claim_key']}"
            db.execute(
                """INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id, run_id, claim["claim_key"], claim["section"],
                    claim["cluster_key"], int(claim["claim_order"]), claim.get("statement", ""),
                    claim.get("impact", "routine"), claim.get("uncertainty", ""),
                    claim.get("disagreement", ""), claim.get("validation_status", "validated"),
                    json.dumps(claim.get("topic_tags", []), sort_keys=True),
                ),
            )
            relationship = "qualifies" if claim["cluster_key"] == "counter_evidence" else "supports"
            for finding_key in claim.get("finding_keys", []):
                finding_id = finding_ids_by_key.get(finding_key)
                if finding_id:
                    db.execute(
                        "INSERT OR IGNORE INTO finding_claims VALUES (?,?,?)",
                        (finding_id, claim_id, relationship),
                    )
            for citation_order, source_id in enumerate(claim.get("evidence_ids", [])):
                if source_id in available_sources:
                    db.execute(
                        "INSERT OR IGNORE INTO claim_sources VALUES (?,?,?,?,?)",
                        (claim_id, run_id, source_id, relationship, citation_order),
                    )

    def approve(self, run_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("reviewer is required")
        with self._lock, self._connection() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("run not found")
            if row["status"] not in {"awaiting_approval", "partial_awaiting_approval"}:
                raise ValueError("only an approval-eligible draft can be approved")
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
            db.execute(
                """UPDATE run_stages SET status='succeeded', completed_at=?,
                summary='Approved by ' || ?, failure_count=0
                WHERE run_id=? AND stage_key='human_approval_gate'""",
                (decided_at, reviewer, run_id),
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
        result["trace"] = self.trace_for_run(run_id)
        return result

    def trace_for_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            run = db.execute("SELECT status,started_at,completed_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                return None
            stages = [dict(row) for row in db.execute(
                "SELECT * FROM run_stages WHERE run_id=? ORDER BY stage_order", (run_id,)
            ).fetchall()]
            invocations = [dict(row) for row in db.execute(
                """SELECT id,stage_id,worker_key,worker_kind,attempt,status,started_at,
                completed_at,duration_ms,summary,error FROM invocations WHERE run_id=?
                ORDER BY started_at,id""", (run_id,)
            ).fetchall()]
            findings = [dict(row) for row in db.execute(
                """SELECT id,stage_id,invocation_id,finding_key,finding_type,summary,
                uncertainty,status FROM findings WHERE run_id=? ORDER BY finding_key""", (run_id,)
            ).fetchall()]
            claims = [dict(row) for row in db.execute(
                """SELECT c.*,
                (SELECT COUNT(*) FROM claim_sources cs WHERE cs.claim_id=c.id) source_count,
                (SELECT COUNT(*) FROM finding_claims fc WHERE fc.claim_id=c.id) finding_count
                FROM claims c WHERE c.run_id=? ORDER BY c.claim_order""", (run_id,)
            ).fetchall()]
            source_count = int(db.execute(
                "SELECT COUNT(*) FROM run_sources WHERE run_id=?", (run_id,)
            ).fetchone()[0])

        invocations_by_stage: dict[str, list[dict[str, Any]]] = {}
        for invocation in invocations:
            invocations_by_stage.setdefault(invocation.pop("stage_id"), []).append(invocation)
        findings_by_stage: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            findings_by_stage.setdefault(finding.pop("stage_id"), []).append(finding)
        for stage in stages:
            stage["invocations"] = invocations_by_stage.get(stage["id"], [])
            stage["findings"] = findings_by_stage.get(stage["id"], [])
        for claim in claims:
            claim["topic_tags"] = json.loads(claim.pop("topic_tags_json"))
        duration_ms: float | None = None
        if run["completed_at"]:
            try:
                duration_ms = round(
                    (datetime.fromisoformat(run["completed_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds() * 1000,
                    3,
                )
            except ValueError:
                pass
        return {
            "summary": {
                "stage_count": len(stages),
                "invocation_count": len(invocations),
                "finding_count": len(findings),
                "claim_count": len(claims),
                "source_count": source_count,
                "failure_count": sum(stage["failure_count"] for stage in stages),
                "duration_ms": duration_ms,
            },
            "edges": [{"source": source, "target": target} for source, target in TRACE_EDGES],
            "stages": stages,
            "claims": claims,
        }

    def invocation_detail(self, run_id: str, invocation_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT i.*,s.stage_key,s.label stage_label FROM invocations i
                JOIN run_stages s ON s.id=i.stage_id WHERE i.run_id=? AND i.id=?""",
                (run_id, invocation_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["raw_payload"] = json.loads(result.pop("raw_payload_json"))
        return result

    def evidence_for_run(
        self, run_id: str, *, cluster_key: str | None = None,
        stage_key: str | None = None, lens: str | None = None,
        status: str | None = None, limit: int = 25, offset: int = 0,
    ) -> dict[str, Any] | None:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        with self._connection() as db:
            run = db.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                return None
            brief_row = db.execute("SELECT payload_json FROM briefs WHERE run_id=?", (run_id,)).fetchone()
            brief = json.loads(brief_row["payload_json"]) if brief_row else {}
            cluster_rows = [dict(row) for row in db.execute(
                """SELECT c.cluster_key,COUNT(DISTINCT c.id) claim_count,
                COUNT(DISTINCT cs.source_id) source_count,
                COUNT(DISTINCT fc.finding_id) finding_count,
                SUM(CASE WHEN c.validation_status!='validated' THEN 1 ELSE 0 END) blocked_claim_count
                FROM claims c
                LEFT JOIN claim_sources cs ON cs.claim_id=c.id
                LEFT JOIN finding_claims fc ON fc.claim_id=c.id
                WHERE c.run_id=? GROUP BY c.cluster_key ORDER BY MIN(c.claim_order)""",
                (run_id,),
            ).fetchall()]
            if not cluster_rows:
                return {"clusters": [], "active_cluster": None, "sources": [], "total": 0, "limit": limit, "offset": offset}
            available_clusters = {row["cluster_key"] for row in cluster_rows}
            active_cluster = cluster_key if cluster_key in available_clusters else cluster_rows[0]["cluster_key"]

            conditions = ["c.run_id=?", "c.cluster_key=?"]
            parameters: list[Any] = [run_id, active_cluster]
            if lens:
                conditions.append("rs.category=?")
                parameters.append(lens)
            if status in {"validated", "blocked"}:
                conditions.append("c.validation_status=?")
                parameters.append(status)
            if stage_key:
                conditions.append(
                    "EXISTS (SELECT 1 FROM finding_claims fcx JOIN findings fx ON fx.id=fcx.finding_id "
                    "WHERE fcx.claim_id=c.id AND fx.finding_key=?)"
                )
                parameters.append(stage_key)
            rows = [dict(row) for row in db.execute(
                f"""SELECT rs.*,c.id claim_id,c.claim_key,c.statement,c.validation_status,
                cs.relationship FROM run_sources rs
                JOIN claim_sources cs ON cs.run_id=rs.run_id AND cs.source_id=rs.source_id
                JOIN claims c ON c.id=cs.claim_id WHERE {' AND '.join(conditions)}
                ORDER BY cs.citation_order,rs.publisher,rs.title""",
                parameters,
            ).fetchall()]

            sources_by_id: dict[str, dict[str, Any]] = {}
            for row in rows:
                source_id = row["source_id"]
                source = sources_by_id.setdefault(source_id, {
                    key: row[key] for key in (
                        "source_id", "category", "title", "publisher", "url", "published_on",
                        "relevant_excerpt", "provenance", "role_connection", "validation_status",
                    )
                } | {"claims": []})
                if not any(item["claim_id"] == row["claim_id"] for item in source["claims"]):
                    source["claims"].append({
                        "claim_id": row["claim_id"], "claim_key": row["claim_key"],
                        "statement": row["statement"], "validation_status": row["validation_status"],
                        "relationship": row["relationship"],
                    })
            publisher_counts: dict[str, int] = {}
            for source in sources_by_id.values():
                publisher_counts[source["publisher"]] = publisher_counts.get(source["publisher"], 0) + 1
            as_of: date | None = None
            try:
                as_of = date.fromisoformat(str(brief.get("as_of", "")))
            except ValueError:
                pass
            ranked: list[dict[str, Any]] = []
            for source in sources_by_id.values():
                score, reasons = rank_source(
                    source, claim_count=len(source["claims"]),
                    publisher_count=publisher_counts[source["publisher"]], as_of=as_of,
                    counter_evidence=active_cluster == "counter_evidence",
                )
                source["rank_score"] = score
                source["rank_reasons"] = reasons
                ranked.append(source)
            ranked.sort(key=lambda item: (-item["rank_score"], item["publisher"], item["title"]))
            total = len(ranked)
            page = ranked[offset:offset + limit]
            for source in page:
                source["findings"] = [dict(row) for row in db.execute(
                    """SELECT DISTINCT f.id,f.finding_key,f.summary,f.status,f.invocation_id,
                    s.label stage_label FROM findings f
                    JOIN finding_sources fs ON fs.finding_id=f.id
                    JOIN run_stages s ON s.id=f.stage_id
                    WHERE fs.run_id=? AND fs.source_id=? ORDER BY s.stage_order""",
                    (run_id, source["source_id"]),
                ).fetchall()]
            lens_options = [row[0] for row in db.execute(
                "SELECT DISTINCT category FROM run_sources WHERE run_id=? ORDER BY category", (run_id,)
            ).fetchall()]
            stage_options = [dict(row) for row in db.execute(
                "SELECT stage_key,label FROM run_stages WHERE run_id=? AND invocation_count>0 ORDER BY stage_order",
                (run_id,),
            ).fetchall()]

        for cluster in cluster_rows:
            cluster["label"] = CLUSTER_LABELS.get(cluster["cluster_key"], cluster["cluster_key"].replace("_", " ").title())
            cluster["status"] = "blocked" if cluster["blocked_claim_count"] else "validated"
        return {
            "clusters": cluster_rows,
            "active_cluster": active_cluster,
            "sources": page,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {
                "lenses": lens_options,
                "stages": stage_options,
                "statuses": ["validated", "blocked"],
            },
        }

    def latest_run_for_role(self, role: Role) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT id FROM runs WHERE role_slug=? ORDER BY started_at DESC LIMIT 1", (role.value,)).fetchone()
        return self.get_run(row["id"]) if row else None

    def counts(self) -> dict[str, int]:
        with self._connection() as db:
            return {table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in ("roles", "evidence", "runs", "artifacts", "briefs", "approvals")}
