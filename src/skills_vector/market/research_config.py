"""Fail-closed configuration for the authenticated DeepInfra research lane.

The file is operator-owned and lives outside the repository. It contains no
credential; the only credential source is the short-lived process environment.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONFIG_SCHEMA = "skills-vector-market-research/1"
PROVIDER = "deepinfra"
ENDPOINT = "https://api.deepinfra.com/v1/openai/chat/completions"
MAX_MONTHLY_CAP_USD = 10.0
MAX_MISSION_ALLOCATION_USD = 2.0
HARD_LIMITS = {
    "max_retrievals": 60,
    "max_model_calls": 12,
    "max_input_tokens": 8192,
    "max_output_tokens": 2048,
    "max_retries": 2,
    "model_timeout_seconds": 120,
    "max_run_seconds": 1800,
}


class ResearchConfigError(RuntimeError):
    """Operator configuration is incomplete, stale, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    model_id: str
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None

    def upper_bound_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_usd_per_million
            + output_tokens * self.output_usd_per_million
        ) / 1_000_000

    def cost_usd(self, input_tokens: int, output_tokens: int, *, cached_tokens: int = 0) -> float:
        if isinstance(cached_tokens, bool) or not isinstance(cached_tokens, int) or not 0 <= cached_tokens <= input_tokens:
            raise ResearchConfigError("cached input token usage is invalid")
        if cached_tokens and self.cached_input_usd_per_million is None:
            raise ResearchConfigError("cached input usage lacks a verified non-promotional price")
        return (
            (input_tokens - cached_tokens) * self.input_usd_per_million
            + cached_tokens * (self.cached_input_usd_per_million or 0)
            + output_tokens * self.output_usd_per_million
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class ResearchRuntimeConfig:
    config_path: Path
    provider: str
    endpoint: str
    ledger_path: Path
    month: str
    monthly_cap_usd: float
    mission_id: str
    mission_reservation_id: str
    mission_allocation_usd: float
    pricing_receipt_path: Path
    pricing_receipt_sha256: str
    models: dict[str, str]
    prices: dict[str, ModelPrice]
    limits: dict[str, int]

    def model_for(self, role: str) -> str:
        try:
            return self.models[role]
        except KeyError as exc:
            raise ResearchConfigError(f"unknown configured model role {role!r}") from exc

    def price_for(self, model_id: str) -> ModelPrice:
        try:
            return self.prices[model_id]
        except KeyError as exc:
            raise ResearchConfigError(f"no verified non-promotional price for model {model_id!r}") from exc


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchConfigError(f"{field} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ResearchConfigError(f"{field} must be a finite positive number")
    return result


def _outside_product(path: Path, product_root: Path, field: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(product_root)
    except ValueError:
        return resolved
    raise ResearchConfigError(f"{field} must be outside the product worktree")


def _reject_credentials(value: Any, prefix: str = "config") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(marker in normalized for marker in ("api_key", "credential", "secret", "access_token")):
                raise ResearchConfigError(f"{prefix}.{key} is forbidden; inject credentials through the process environment")
            _reject_credentials(child, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_credentials(child, f"{prefix}[{index}]")


def load_research_config(
    path: Path | str,
    *,
    product_root: Path | str | None = None,
    now: datetime | None = None,
) -> ResearchRuntimeConfig:
    """Load and validate the operator config, pinned price receipt, and limits.

    Config limits may be reduced from the frozen ceilings, never increased.
    A current-month check prevents an October reservation being reused later.
    """

    config_path = Path(path).expanduser().resolve()
    root = Path(product_root).resolve() if product_root else Path(__file__).resolve().parents[3]
    _outside_product(config_path, root, "research config")
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise ResearchConfigError(f"research config unavailable: {config_path}") from exc
    if len(raw) > 64 * 1024:
        raise ResearchConfigError("research config exceeds 65536 bytes")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchConfigError("research config is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ResearchConfigError("research config must be a JSON object")
    _reject_credentials(data)
    expected = {
        "schema_version", "provider", "endpoint", "ledger_path", "month", "monthly_cap_usd",
        "mission_id", "mission_reservation_id", "mission_allocation_usd", "pricing_receipt_path",
        "pricing_receipt_sha256", "models", "limits",
    }
    if set(data) != expected:
        missing, extra = sorted(expected - set(data)), sorted(set(data) - expected)
        raise ResearchConfigError(f"research config fields mismatch (missing={missing}, extra={extra})")
    if data["schema_version"] != CONFIG_SCHEMA:
        raise ResearchConfigError(f"unsupported research config schema {data['schema_version']!r}")
    if data["provider"] != PROVIDER or data["endpoint"] != ENDPOINT:
        raise ResearchConfigError("active market research is restricted to the authenticated DeepInfra endpoint")

    ledger_path = _outside_product(Path(str(data["ledger_path"])), root, "ledger_path")
    pricing_path = _outside_product(Path(str(data["pricing_receipt_path"])), root, "pricing_receipt_path")
    month = str(data["month"])
    current_month = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m")
    if month != current_month:
        raise ResearchConfigError(f"mission month {month!r} does not match current UTC month {current_month!r}")
    monthly_cap = _finite_positive(data["monthly_cap_usd"], "monthly_cap_usd")
    allocation = _finite_positive(data["mission_allocation_usd"], "mission_allocation_usd")
    if monthly_cap > MAX_MONTHLY_CAP_USD:
        raise ResearchConfigError(f"monthly cap cannot exceed US${MAX_MONTHLY_CAP_USD:.2f}")
    if allocation > MAX_MISSION_ALLOCATION_USD or allocation > monthly_cap:
        raise ResearchConfigError("mission allocation exceeds the frozen US$2.00 / monthly ceiling")
    mission_id = str(data["mission_id"]).strip()
    reservation_id = str(data["mission_reservation_id"]).strip()
    if not mission_id or len(mission_id) > 100 or not reservation_id or len(reservation_id) > 100:
        raise ResearchConfigError("mission_id and mission_reservation_id must be nonempty bounded identifiers")

    receipt_sha = str(data["pricing_receipt_sha256"])
    if len(receipt_sha) != 64 or any(ch not in "0123456789abcdef" for ch in receipt_sha.lower()):
        raise ResearchConfigError("pricing_receipt_sha256 must be a SHA-256 hex digest")
    try:
        price_bytes = pricing_path.read_bytes()
    except OSError as exc:
        raise ResearchConfigError(f"verified pricing receipt unavailable: {pricing_path}") from exc
    if hashlib.sha256(price_bytes).hexdigest() != receipt_sha.lower():
        raise ResearchConfigError("verified pricing receipt SHA-256 mismatch")
    try:
        pricing_doc = json.loads(price_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchConfigError("verified pricing receipt is not valid UTF-8 JSON") from exc
    if not isinstance(pricing_doc, dict) or pricing_doc.get("provider") != PROVIDER or pricing_doc.get("endpoint") != ENDPOINT:
        raise ResearchConfigError("pricing receipt provider or endpoint does not match DeepInfra")
    if not str(pricing_doc.get("verified_at") or "").strip():
        raise ResearchConfigError("pricing receipt is missing verified_at")
    receipt_models = pricing_doc.get("models")
    if not isinstance(receipt_models, dict):
        raise ResearchConfigError("pricing receipt is missing its model table")

    raw_models = data["models"]
    roles = {"primary", "challenger", "escalation"}
    if not isinstance(raw_models, dict) or set(raw_models) != roles:
        raise ResearchConfigError("models must define exactly primary, challenger, and escalation")
    models: dict[str, str] = {}
    prices: dict[str, ModelPrice] = {}
    for role in sorted(roles):
        model_id = str(raw_models[role]).strip()
        if not model_id or len(model_id) > 200:
            raise ResearchConfigError(f"models.{role} must be an exact bounded DeepInfra model id")
        row = receipt_models.get(model_id)
        if not isinstance(row, dict) or row.get("model_id") != model_id:
            raise ResearchConfigError(f"model {model_id!r} is absent from the hash-pinned pricing receipt")
        if row.get("standard_tier") is not True or row.get("promotional_discount_not_applied") is not True:
            raise ResearchConfigError(f"model {model_id!r} lacks a verified standard non-promotional price")
        catalog = row.get("catalog_record") or {}
        catalog_pricing = catalog.get("pricing") or {}
        cache_ratio = catalog_pricing.get("rate_per_input_token_cached")
        cached_price = None
        if cache_ratio is not None:
            ratio = _finite_positive(cache_ratio, f"price.{model_id}.cached_ratio")
            if ratio > 1 or catalog.get("model_name") != model_id:
                raise ResearchConfigError("cached price ratio or catalog model identity is invalid")
            cached_price = _finite_positive(row.get("input_usd_per_million"), f"price.{model_id}.input") * ratio
        price = ModelPrice(
            model_id=model_id,
            input_usd_per_million=_finite_positive(row.get("input_usd_per_million"), f"price.{model_id}.input"),
            output_usd_per_million=_finite_positive(row.get("output_usd_per_million"), f"price.{model_id}.output"),
            cached_input_usd_per_million=cached_price,
        )
        models[role] = model_id
        prices[model_id] = price
    if models["primary"] == models["challenger"]:
        raise ResearchConfigError("challenger must be a different exact model from primary")

    raw_limits = data["limits"]
    if not isinstance(raw_limits, dict) or set(raw_limits) != set(HARD_LIMITS):
        raise ResearchConfigError("limits must define every frozen resource ceiling")
    limits: dict[str, int] = {}
    for name, hard_limit in HARD_LIMITS.items():
        value = raw_limits[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > hard_limit:
            raise ResearchConfigError(f"limits.{name} must be an integer between 1 and {hard_limit}")
        limits[name] = value

    return ResearchRuntimeConfig(
        config_path=config_path,
        provider=PROVIDER,
        endpoint=ENDPOINT,
        ledger_path=ledger_path,
        month=month,
        monthly_cap_usd=monthly_cap,
        mission_id=mission_id,
        mission_reservation_id=reservation_id,
        mission_allocation_usd=allocation,
        pricing_receipt_path=pricing_path,
        pricing_receipt_sha256=receipt_sha.lower(),
        models=models,
        prices=prices,
        limits=limits,
    )


__all__ = [
    "CONFIG_SCHEMA", "ENDPOINT", "HARD_LIMITS", "MAX_MISSION_ALLOCATION_USD", "MAX_MONTHLY_CAP_USD",
    "ModelPrice", "PROVIDER", "ResearchConfigError", "ResearchRuntimeConfig", "load_research_config",
]
