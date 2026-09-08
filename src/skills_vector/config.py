"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_RUNTIME_MODES = frozenset({"stub", "composer", "auto"})


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_path: Path = Path("data/skills_vector.db")
    runtime_mode: str = "stub"
    ui_directory: Path | None = Path("design-concepts")
    cors_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("SKILLS_VECTOR_PORT must be between 1 and 65535")
        if self.runtime_mode not in _RUNTIME_MODES:
            raise ValueError(
                "SKILLS_VECTOR_RUNTIME must be one of: " + ", ".join(sorted(_RUNTIME_MODES))
            )
        if self.environment == "production" and self.runtime_mode == "auto":
            raise ValueError("production must select an explicit runtime; auto is not allowed")

    @classmethod
    def from_env(cls) -> "Settings":
        ui_value = os.environ.get("SKILLS_VECTOR_UI_DIR", "design-concepts").strip()
        origins = tuple(
            origin.strip()
            for origin in os.environ.get("SKILLS_VECTOR_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        return cls(
            environment=os.environ.get("SKILLS_VECTOR_ENV", "development").strip().casefold(),
            host=os.environ.get("SKILLS_VECTOR_HOST", "127.0.0.1").strip(),
            port=int(os.environ.get("SKILLS_VECTOR_PORT", "8000")),
            database_path=Path(os.environ.get("SKILLS_VECTOR_DATABASE", "data/skills_vector.db")),
            runtime_mode=os.environ.get("SKILLS_VECTOR_RUNTIME", "stub").strip().casefold(),
            ui_directory=Path(ui_value) if ui_value else None,
            cors_origins=origins,
        )
