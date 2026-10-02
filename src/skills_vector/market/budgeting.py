"""Durable per-attempt accounting inside the pre-reserved market mission."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from ..budget import BudgetError, BudgetLedger, LedgerEntryKind
from .research_config import MAX_MISSION_ALLOCATION_USD, MAX_MONTHLY_CAP_USD, ResearchConfigError, ResearchRuntimeConfig

_ACTIVE_ATTEMPTS = ("reserved", "unknown", "overrun")
_KNOWN_ATTEMPTS = ("settled", "known_failure")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _utc_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class MissionBudget:
    """Use the operator's existing parent reservation without double-booking it.

    Model attempts are sub-reservations in dedicated tables. They do not create
    additional global ledger reserves. The original parent hold remains open
    until every attempt has verified usage and an operator explicitly settles
    the mission.
    """

    def __init__(self, config: ResearchRuntimeConfig) -> None:
        self.config = config
        self._db: sqlite3.Connection | None = None
        path = config.ledger_path
        if not path.is_file():
            raise BudgetError(f"authoritative budget ledger does not exist: {path}")
        self._validate_existing_parent(path)
        self._db = sqlite3.connect(path, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._ledger = BudgetLedger(self._db, monthly_cap_usd=config.monthly_cap_usd)
        self._create_market_schema()
        self._open_mission()

    def _readonly_connection(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _validate_existing_parent(self, path: Path) -> None:
        """Refuse unseeded ledgers and stale, settled, or mismatched parent holds."""

        db = self._readonly_connection(path)
        try:
            tables = {
                str(row["name"])
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "budget_ledger" not in tables:
                raise BudgetError("authoritative ledger has no seeded budget_ledger table")
            columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(budget_ledger)").fetchall()}
            required = {
                "entry_id", "month", "kind", "reservation_id", "run_id", "model_id", "estimated_usd",
                "actual_usd", "input_tokens", "output_tokens", "created_at", "note",
            }
            if not required.issubset(columns):
                raise BudgetError("authoritative budget_ledger schema is incompatible")
            parent_rows = db.execute(
                "SELECT month, kind, run_id, model_id, estimated_usd FROM budget_ledger "
                "WHERE reservation_id = ? AND kind = 'reserve'",
                (self.config.mission_reservation_id,),
            ).fetchall()
            if len(parent_rows) != 1:
                raise BudgetError("mission parent reservation is absent or ambiguous")
            parent = parent_rows[0]
            if (
                parent["month"] != self.config.month
                or parent["run_id"] != self.config.mission_id
                or parent["model_id"] != "catalog-wide-deepinfra"
                or not math.isclose(float(parent["estimated_usd"]), self.config.mission_allocation_usd, abs_tol=1e-9)
            ):
                raise BudgetError("mission parent reservation does not match the operator research config")
            closed = db.execute(
                "SELECT 1 FROM budget_ledger WHERE reservation_id = ? AND kind IN ('settle', 'release') LIMIT 1",
                (self.config.mission_reservation_id,),
            ).fetchone()
            if closed:
                raise BudgetError("mission parent reservation has already been settled or released")
            if self.config.month != _utc_month():
                raise BudgetError("mission reservation is not for the current UTC month")
            if self.config.monthly_cap_usd > MAX_MONTHLY_CAP_USD:
                raise BudgetError("monthly cap exceeds the hard US$10.00 ceiling")
            if self.config.mission_allocation_usd > MAX_MISSION_ALLOCATION_USD:
                raise BudgetError("mission allocation exceeds the hard US$2.00 ceiling")
            # A valid reserve must still be included in the global cap calculation.
            total_row = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN kind='settle' THEN actual_usd "
                "WHEN kind='reserve' THEN estimated_usd WHEN kind='release' THEN -estimated_usd "
                "ELSE 0 END), 0) AS total FROM budget_ledger WHERE month = ?",
                (self.config.month,),
            ).fetchone()
            total = float(total_row["total"] or 0)
            if total < 0 or total > self.config.monthly_cap_usd + 1e-9:
                raise BudgetError("authoritative monthly ledger is inconsistent with its cap")
        finally:
            db.close()

    def _create_market_schema(self) -> None:
        assert self._db is not None
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_missions (
                mission_id TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL UNIQUE,
                month TEXT NOT NULL,
                allocation_usd REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                settled_at TEXT,
                actual_usd REAL
            );
            CREATE TABLE IF NOT EXISTS market_attempt_reconciliations (
                event_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                previous_row_json TEXT NOT NULL,
                pricing_receipt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_attempts (
                attempt_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                attempt_key TEXT NOT NULL,
                stage TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                response_sha256 TEXT,
                reserved_usd REAL NOT NULL,
                actual_usd REAL,
                input_token_cap INTEGER NOT NULL,
                output_token_cap INTEGER NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                status TEXT NOT NULL,
                finish_reason TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                receipt_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(mission_id, attempt_key),
                FOREIGN KEY(mission_id) REFERENCES market_missions(mission_id)
            );
            CREATE INDEX IF NOT EXISTS market_attempts_run_idx ON market_attempts(mission_id, run_id);
            """
        )
        self._db.commit()

    def _begin(self) -> None:
        assert self._db is not None
        self._db.execute("BEGIN IMMEDIATE")

    def _open_mission(self) -> None:
        assert self._db is not None
        self._begin()
        try:
            existing = self._db.execute(
                "SELECT * FROM market_missions WHERE mission_id = ? OR reservation_id = ?",
                (self.config.mission_id, self.config.mission_reservation_id),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise BudgetError("mission id/reservation already map to multiple ledger rows")
                row = existing[0]
                if (
                    row["mission_id"] != self.config.mission_id
                    or row["reservation_id"] != self.config.mission_reservation_id
                    or row["month"] != self.config.month
                    or not math.isclose(float(row["allocation_usd"]), self.config.mission_allocation_usd, abs_tol=1e-9)
                    or row["status"] != "open"
                ):
                    raise BudgetError("existing durable mission record is closed or mismatched")
            else:
                self._db.execute(
                    "INSERT INTO market_missions "
                    "(mission_id, reservation_id, month, allocation_usd, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'open', ?)",
                    (
                        self.config.mission_id,
                        self.config.mission_reservation_id,
                        self.config.month,
                        self.config.mission_allocation_usd,
                        _now_iso(),
                    ),
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _mission_spend(self) -> float:
        assert self._db is not None
        active_marks = ",".join("?" for _ in _ACTIVE_ATTEMPTS)
        known_marks = ",".join("?" for _ in _KNOWN_ATTEMPTS)
        row = self._db.execute(
            f"SELECT COALESCE(SUM(CASE WHEN status IN ({known_marks}) THEN actual_usd "
            f"WHEN status = 'overrun' THEN MAX(COALESCE(actual_usd, 0), reserved_usd) "
            f"WHEN status IN ({active_marks}) THEN reserved_usd ELSE 0 END), 0) AS total "
            "FROM market_attempts WHERE mission_id = ?",
            (*_KNOWN_ATTEMPTS, *_ACTIVE_ATTEMPTS, self.config.mission_id),
        ).fetchone()
        return float(row["total"] or 0)

    def reserve_attempt(
        self,
        *,
        run_id: str,
        attempt_key: str,
        stage: str,
        attempt_number: int,
        model_id: str,
        request_bytes: bytes,
        input_token_cap: int,
        output_token_cap: int,
        max_model_calls: int,
    ) -> dict[str, Any]:
        """Persist a conservative token-priced bound before making HTTP request."""

        assert self._db is not None
        if self.config.month != _utc_month():
            raise BudgetError("cannot make an inference call outside the reserved UTC month")
        if not attempt_key or not run_id or not stage:
            raise BudgetError("attempt identity must be nonempty")
        if input_token_cap < 1 or output_token_cap < 1:
            raise BudgetError("attempt token caps must be positive")
        price = self.config.price_for(model_id)
        upper = price.upper_bound_usd(input_token_cap, output_token_cap)
        # Round upward at picodollar precision; never under-reserve due to float rounding.
        upper = math.ceil(upper * 1_000_000_000_000) / 1_000_000_000_000
        attempt_id = uuid4().hex
        request_sha = _sha256(request_bytes)
        self._begin()
        try:
            mission = self._db.execute(
                "SELECT status, month, allocation_usd FROM market_missions WHERE mission_id = ?",
                (self.config.mission_id,),
            ).fetchone()
            if not mission or mission["status"] != "open" or mission["month"] != _utc_month():
                raise BudgetError("market mission is not open for the current UTC month")
            duplicate = self._db.execute(
                "SELECT 1 FROM market_attempts WHERE mission_id = ? AND attempt_key = ?",
                (self.config.mission_id, attempt_key),
            ).fetchone()
            if duplicate:
                raise BudgetError("attempt key was already recorded; duplicate provider execution refused")
            count = int(
                self._db.execute(
                    "SELECT COUNT(*) AS n FROM market_attempts WHERE mission_id = ? AND run_id = ? "
                    "AND status != 'refused'",
                    (self.config.mission_id, run_id),
                ).fetchone()["n"]
            )
            if count >= max_model_calls:
                self._insert_refusal(attempt_id, run_id, attempt_key, stage, attempt_number, model_id, request_sha,
                                     input_token_cap, output_token_cap, "per_run_call_limit")
                raise BudgetError(f"per-run model-attempt ceiling {max_model_calls} reached")
            spend = self._mission_spend()
            allocation = float(mission["allocation_usd"])
            if spend + upper > allocation + 1e-12:
                self._insert_refusal(attempt_id, run_id, attempt_key, stage, attempt_number, model_id, request_sha,
                                     input_token_cap, output_token_cap, "mission_allocation")
                raise BudgetError(
                    f"mission allocation exhausted (used or held US${spend:.6f}; "
                    f"attempt bound US${upper:.6f}; allocation US${allocation:.2f})"
                )
            self._db.execute(
                "INSERT INTO market_attempts "
                "(attempt_id, mission_id, run_id, attempt_key, stage, attempt_number, model_id, request_sha256, "
                "reserved_usd, input_token_cap, output_token_cap, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)",
                (
                    attempt_id, self.config.mission_id, run_id, attempt_key, stage, attempt_number, model_id,
                    request_sha, upper, input_token_cap, output_token_cap, _now_iso(),
                ),
            )
            self._db.commit()
        except Exception:
            if self._db.in_transaction:
                self._db.commit() if self._refusal_written(attempt_id) else self._db.rollback()
            raise
        return {
            "attempt_id": attempt_id,
            "attempt_key": attempt_key,
            "model_id": model_id,
            "reserved_usd": upper,
            "request_sha256": request_sha,
            "input_token_cap": input_token_cap,
            "output_token_cap": output_token_cap,
        }

    def _refusal_written(self, attempt_id: str) -> bool:
        assert self._db is not None
        row = self._db.execute("SELECT 1 FROM market_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return row is not None

    def _insert_refusal(
        self,
        attempt_id: str,
        run_id: str,
        attempt_key: str,
        stage: str,
        attempt_number: int,
        model_id: str,
        request_sha: str,
        input_cap: int,
        output_cap: int,
        reason: str,
    ) -> None:
        assert self._db is not None
        self._db.execute(
            "INSERT INTO market_attempts "
            "(attempt_id, mission_id, run_id, attempt_key, stage, attempt_number, model_id, request_sha256, "
            "reserved_usd, input_token_cap, output_token_cap, status, error_code, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'refused', ?, ?, ?)",
            (
                attempt_id, self.config.mission_id, run_id, attempt_key, stage, attempt_number, model_id,
                request_sha, input_cap, output_cap, reason, _now_iso(), _now_iso(),
            ),
        )
        self._db.commit()

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        actual_usd: float | None,
        input_tokens: int | None,
        output_tokens: int | None,
        response_bytes: bytes | None = None,
        finish_reason: str | None = None,
        error_code: str | None = None,
        receipt: dict[str, Any] | None = None,
        reconcile_unknown: bool = False,
    ) -> dict[str, Any]:
        """Settle verified usage; unknown usage remains a held upper bound."""

        assert self._db is not None
        self._begin()
        try:
            row = self._db.execute(
                "SELECT * FROM market_attempts WHERE attempt_id = ? AND mission_id = ?",
                (attempt_id, self.config.mission_id),
            ).fetchone()
            if not row:
                raise BudgetError("unknown market attempt")
            if reconcile_unknown:
                if row["status"] != "unknown" or response_bytes is None or _sha256(response_bytes) != row["response_sha256"]:
                    raise BudgetError("recovery requires an unknown attempt and the identical recorded provider response")
                try:
                    provider_response = json.loads(response_bytes)
                    provider_usage = provider_response["usage"]
                    provider_detail = provider_usage.get("prompt_tokens_details") or {}
                    provider_cached = provider_detail.get("cached_tokens") or 0
                    if (
                        provider_response["model"] != row["model_id"]
                        or provider_usage["prompt_tokens"] != input_tokens
                        or provider_usage["completion_tokens"] != output_tokens
                        or provider_cached != (receipt or {}).get("cached_input_tokens", 0)
                    ):
                        raise ValueError("usage mismatch")
                except (KeyError, TypeError, ValueError):
                    raise BudgetError("recovery usage does not match the original provider response") from None
            elif row["status"] != "reserved":
                raise BudgetError("market attempt is already final; duplicate settlement refused")
            usage_known = (
                actual_usd is not None
                and not isinstance(actual_usd, bool)
                and isinstance(input_tokens, int)
                and not isinstance(input_tokens, bool)
                and isinstance(output_tokens, int)
                and not isinstance(output_tokens, bool)
                and input_tokens >= 0
                and output_tokens >= 0
                and math.isfinite(float(actual_usd))
                and float(actual_usd) >= 0
            )
            if usage_known:
                actual = float(actual_usd)
                price = self.config.price_for(str(row["model_id"]))
                cached_tokens = (receipt or {}).get("cached_input_tokens", 0)
                try:
                    calculated = price.cost_usd(input_tokens, output_tokens, cached_tokens=cached_tokens)
                except ResearchConfigError:
                    calculated = math.inf
                if not math.isclose(actual, calculated, rel_tol=0.001, abs_tol=0.000001):
                    usage_known = False
                    error_code = error_code or "provider_usage_cost_mismatch"
            if usage_known:
                input_over_cap = input_tokens > int(row["input_token_cap"])
                output_over_cap = output_tokens > int(row["output_token_cap"])
                status = "settled" if not error_code else "known_failure"
                if input_over_cap or output_over_cap or actual > float(row["reserved_usd"]) + 1e-12:
                    status = "overrun"
                    error_code = error_code or "provider_usage_exceeded_token_or_cost_cap"
                reserved_actual: float | None = actual
                input_value: int | None = input_tokens
                output_value: int | None = output_tokens
                if reconcile_unknown:
                    self._db.execute(
                        "INSERT INTO market_attempt_reconciliations VALUES (?, ?, ?, ?, ?)",
                        (uuid4().hex, attempt_id, json.dumps(dict(row), sort_keys=True),
                         self.config.pricing_receipt_sha256, _now_iso()),
                    )
            else:
                if reconcile_unknown:
                    raise BudgetError("recovery requires fully verifiable token usage and non-promotional cost")
                status = "unknown"
                reserved_actual = None
                input_value = None
                output_value = None
            receipt_text = json.dumps(receipt or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(receipt_text.encode("utf-8")) > 8192:
                raise BudgetError("attempt receipt exceeds 8192 bytes")
            response_sha = _sha256(response_bytes) if response_bytes is not None else None
            self._db.execute(
                "UPDATE market_attempts SET response_sha256 = ?, actual_usd = ?, input_tokens = ?, output_tokens = ?, "
                "status = ?, finish_reason = ?, error_code = ?, completed_at = ?, receipt_json = ? "
                "WHERE attempt_id = ?",
                (
                    response_sha, reserved_actual, input_value, output_value, status, finish_reason, error_code,
                    _now_iso(), receipt_text, attempt_id,
                ),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return {
            "attempt_id": attempt_id,
            "status": status,
            "actual_usd": reserved_actual,
            "input_tokens": input_value,
            "output_tokens": output_value,
            "reserved_usd": float(row["reserved_usd"]),
            "error_code": error_code,
            "response_sha256": response_sha,
        }

    def attempts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = self._db.execute(
            "SELECT attempt_id, attempt_key, stage, attempt_number, model_id, request_sha256, response_sha256, "
            "reserved_usd, actual_usd, input_token_cap, output_token_cap, input_tokens, output_tokens, status, "
            "finish_reason, error_code, created_at, completed_at, receipt_json "
            "FROM market_attempts WHERE mission_id = ? AND run_id = ? ORDER BY created_at, attempt_id",
            (self.config.mission_id, run_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        assert self._db is not None
        mission = self._db.execute(
            "SELECT * FROM market_missions WHERE mission_id = ?", (self.config.mission_id,)
        ).fetchone()
        rows = self._db.execute(
            "SELECT status, COUNT(*) AS n, COALESCE(SUM(actual_usd), 0) AS actual_usd, "
            "COALESCE(SUM(CASE WHEN status IN ('reserved', 'unknown') THEN reserved_usd "
            "WHEN status = 'overrun' THEN MAX(COALESCE(actual_usd, 0), reserved_usd) ELSE 0 END), 0) AS held_usd "
            "FROM market_attempts WHERE mission_id = ? GROUP BY status ORDER BY status",
            (self.config.mission_id,),
        ).fetchall()
        return {
            "mission_id": self.config.mission_id,
            "reservation_id": self.config.mission_reservation_id,
            "month": self.config.month,
            "allocation_usd": self.config.mission_allocation_usd,
            "status": str(mission["status"]) if mission else "missing",
            "spent_or_held_usd": round(self._mission_spend(), 12),
            "attempts": [dict(row) for row in rows],
        }

    def settle_mission(self) -> dict[str, Any]:
        """Atomically settle/release the parent hold after all usage is known."""

        assert self._db is not None
        if self.config.month != _utc_month():
            raise BudgetError("cannot settle a mission outside its reserved UTC month")
        self._begin()
        try:
            mission = self._db.execute(
                "SELECT * FROM market_missions WHERE mission_id = ?", (self.config.mission_id,)
            ).fetchone()
            if not mission or mission["status"] != "open":
                raise BudgetError("market mission is not open")
            unresolved = self._db.execute(
                "SELECT COUNT(*) AS n FROM market_attempts WHERE mission_id = ? "
                "AND status IN ('reserved', 'unknown', 'overrun')",
                (self.config.mission_id,),
            ).fetchone()
            if int(unresolved["n"]):
                raise BudgetError("mission has reserved, unknown, or overrun model attempts; parent hold stays open")
            total_row = self._db.execute(
                "SELECT COALESCE(SUM(actual_usd), 0) AS total, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens FROM market_attempts "
                "WHERE mission_id = ? AND status IN ('settled', 'known_failure')",
                (self.config.mission_id,),
            ).fetchone()
            actual = float(total_row["total"] or 0)
            allocation = float(mission["allocation_usd"])
            if actual > allocation + 1e-12:
                raise BudgetError("verified mission usage exceeds its reserved allocation; operator reconciliation required")
            parent = self._db.execute(
                "SELECT * FROM budget_ledger WHERE reservation_id = ? AND kind = 'reserve'",
                (self.config.mission_reservation_id,),
            ).fetchone()
            if not parent:
                raise BudgetError("mission parent reservation is missing")
            already_closed = self._db.execute(
                "SELECT 1 FROM budget_ledger WHERE reservation_id = ? AND kind IN ('settle', 'release') LIMIT 1",
                (self.config.mission_reservation_id,),
            ).fetchone()
            if already_closed:
                raise BudgetError("mission parent reservation has already been reconciled")
            current_total = self._ledger.spent_and_reserved(self.config.month)
            after_settlement = current_total - float(parent["estimated_usd"]) + actual
            if after_settlement > self.config.monthly_cap_usd + 1e-9:
                raise BudgetError("settled mission would exceed the global monthly budget cap")
            stamp = _now_iso()
            self._db.execute(
                "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex, self.config.month, LedgerEntryKind.SETTLE.value, self.config.mission_reservation_id,
                    parent["run_id"], parent["model_id"], 0, actual, int(total_row["input_tokens"] or 0),
                    int(total_row["output_tokens"] or 0), stamp, "market mission reconciled against verified provider usage",
                ),
            )
            self._db.execute(
                "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex, self.config.month, LedgerEntryKind.RELEASE.value, self.config.mission_reservation_id,
                    parent["run_id"], parent["model_id"], parent["estimated_usd"], None, None, None,
                    stamp, "release market mission parent hold after complete reconciliation",
                ),
            )
            self._db.execute(
                "UPDATE market_missions SET status='settled', settled_at=?, actual_usd=? WHERE mission_id=?",
                (stamp, actual, self.config.mission_id),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return self.summary()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


__all__ = ["MissionBudget"]
