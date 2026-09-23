"""Environment-backed local configuration. Credentials never belong in the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .budget import DEFAULT_MONTHLY_CAP_USD


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = Path("data/skills_vector.db")
    releases_path: Path = Path("data/releases")
    monthly_budget_usd: float = DEFAULT_MONTHLY_CAP_USD
    deepinfra_api_key: str | None = None
    runtime: str = "offline"

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.environ.get("DEEPINFRA_API_KEY", "").strip() or None
        return cls(
            database_path=Path(os.environ.get("SKILLS_VECTOR_DATABASE", "data/skills_vector.db")),
            releases_path=Path(os.environ.get("SKILLS_VECTOR_RELEASES", "data/releases")),
            monthly_budget_usd=float(os.environ.get("SKILLS_VECTOR_MONTHLY_BUDGET_USD", str(DEFAULT_MONTHLY_CAP_USD))),
            deepinfra_api_key=key,
            runtime=os.environ.get("SKILLS_VECTOR_RUNTIME", "offline").strip().casefold(),
        )
