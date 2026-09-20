"""Monthly inference budget with reservations, retries, and reconciliation.

Live model calls are refused until a ledger exists and a reservation succeeds.
There is no proprietary-model fallback.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class BudgetError(RuntimeError):
    """Raised when work must be queued because the monthly cap is exhausted."""


class LedgerEntryKind(StrEnum):
    RESERVE = "reserve"
    SETTLE = "settle"
    RELEASE = "release"
    QUEUE = "queue"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    model_id: str
    license: str
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float
    json_schema: bool
    notes: str


# Prices verified 2026-09-20 against DeepInfra public listings.
PINNED_MODELS = {
    "extract": ModelPrice(
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "MIT",
        0.20,
        0.60,
        0.006,
        True,
        "Default extraction, mapping, and drafting. JSON schema and function calling advertised.",
    ),
    "reconcile": ModelPrice(
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "MIT",
        0.20,
        0.60,
        0.006,
        True,
        "Routine terminology reconciliation uses the extraction model.",
    ),
    "challenge": ModelPrice(
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "MIT",
        0.20,
        0.60,
        0.006,
        True,
        "Default challenge uses the extraction model to conserve the $10 cap.",
    ),
    "challenge_hard": ModelPrice(
        "zai-org/GLM-5.3",
        "Z.AI license (MIT-style; MaaS review if licensee revenue > $10B)",
        1.20,
        4.00,
        0.20,
        True,
        "Escalation only. JSON schema and function calling advertised. No automatic proprietary fallback.",
    ),
}

DEFAULT_MONTHLY_CAP_USD = 10.0
MAX_RETRIES = 2
DEFAULT_MAX_OUTPUT_TOKENS = 2048


def estimate_cost_usd(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    billed_input = max(0, input_tokens - cached_input_tokens)
    return (
        billed_input * price.input_usd_per_million
        + cached_input_tokens * price.cached_input_usd_per_million
        + output_tokens * price.output_usd_per_million
    ) / 1_000_000


class BudgetLedger:
    def __init__(self, db: sqlite3.Connection, *, monthly_cap_usd: float = DEFAULT_MONTHLY_CAP_USD) -> None:
        self._db = db
        self.monthly_cap_usd = monthly_cap_usd
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_ledger (
                entry_id TEXT PRIMARY KEY,
                month TEXT NOT NULL,
                kind TEXT NOT NULL,
                reservation_id TEXT,
                run_id TEXT,
                model_id TEXT,
                estimated_usd REAL NOT NULL DEFAULT 0,
                actual_usd REAL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                created_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS inference_queue (
                queue_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def month_key(self, when: datetime | None = None) -> str:
        stamp = when or datetime.now(UTC)
        return f"{stamp.year:04d}-{stamp.month:02d}"

    def spent_and_reserved(self, month: str | None = None) -> float:
        key = month or self.month_key()
        row = self._db.execute(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN kind = 'settle' THEN actual_usd
                    WHEN kind = 'reserve' THEN estimated_usd
                    WHEN kind = 'release' THEN -estimated_usd
                    ELSE 0
                END
            ), 0) AS total
            FROM budget_ledger
            WHERE month = ?
            """,
            (key,),
        ).fetchone()
        return float(row["total"] if row["total"] is not None else 0)

    def remaining(self, month: str | None = None) -> float:
        return round(self.monthly_cap_usd - self.spent_and_reserved(month), 6)

    def reserve(
        self,
        *,
        run_id: str,
        model_id: str,
        estimated_usd: float,
        note: str = "",
    ) -> str:
        if estimated_usd <= 0:
            raise ValueError("reservation must be positive")
        remaining = self.remaining()
        if estimated_usd > remaining:
            queue_id = uuid4().hex
            self._db.execute(
                "INSERT INTO inference_queue VALUES (?, ?, ?, ?, ?)",
                (queue_id, run_id, note or "inference", datetime.now(UTC).isoformat(), "budget_exhausted"),
            )
            self._db.execute(
                "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    self.month_key(),
                    LedgerEntryKind.QUEUE.value,
                    None,
                    run_id,
                    model_id,
                    0,
                    None,
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                    "budget exhausted; queued",
                ),
            )
            self._db.commit()
            raise BudgetError(
                f"monthly inference cap of US${self.monthly_cap_usd:.2f} exhausted "
                f"(remaining US${remaining:.4f}); work queued"
            )
        reservation_id = uuid4().hex
        self._db.execute(
            "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                self.month_key(),
                LedgerEntryKind.RESERVE.value,
                reservation_id,
                run_id,
                model_id,
                estimated_usd,
                None,
                None,
                None,
                datetime.now(UTC).isoformat(),
                note,
            ),
        )
        self._db.commit()
        return reservation_id

    def settle(
        self,
        reservation_id: str,
        *,
        actual_usd: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        reserved = self._db.execute(
            "SELECT estimated_usd, run_id, model_id FROM budget_ledger "
            "WHERE reservation_id = ? AND kind = 'reserve'",
            (reservation_id,),
        ).fetchone()
        if reserved is None:
            raise KeyError(reservation_id)
        self._db.execute(
            "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                self.month_key(),
                LedgerEntryKind.SETTLE.value,
                reservation_id,
                reserved["run_id"],
                reserved["model_id"],
                0,
                actual_usd,
                input_tokens,
                output_tokens,
                datetime.now(UTC).isoformat(),
                "reconciled against provider usage",
            ),
        )
        self._db.execute(
            "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                self.month_key(),
                LedgerEntryKind.RELEASE.value,
                reservation_id,
                reserved["run_id"],
                reserved["model_id"],
                reserved["estimated_usd"],
                None,
                None,
                None,
                datetime.now(UTC).isoformat(),
                "release reservation after settle",
            ),
        )
        self._db.commit()

    def release(self, reservation_id: str, *, note: str = "retry abandoned") -> None:
        reserved = self._db.execute(
            "SELECT estimated_usd, run_id, model_id FROM budget_ledger "
            "WHERE reservation_id = ? AND kind = 'reserve'",
            (reservation_id,),
        ).fetchone()
        if reserved is None:
            raise KeyError(reservation_id)
        self._db.execute(
            "INSERT INTO budget_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                self.month_key(),
                LedgerEntryKind.RELEASE.value,
                reservation_id,
                reserved["run_id"],
                reserved["model_id"],
                reserved["estimated_usd"],
                None,
                None,
                None,
                datetime.now(UTC).isoformat(),
                note,
            ),
        )
        self._db.commit()

    def queued(self, run_id: str | None = None) -> tuple[tuple[str, str, str], ...]:
        sql = "SELECT queue_id, run_id, operation FROM inference_queue"
        params: tuple[str, ...] = ()
        if run_id:
            sql += " WHERE run_id = ?"
            params = (run_id,)
        rows = self._db.execute(sql, params).fetchall()
        return tuple((row["queue_id"], row["run_id"], row["operation"]) for row in rows)
