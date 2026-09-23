"""Optional forecast artifact. Never required to publish current requirements."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ForecastNote:
    occupation_id: str
    horizon: str
    statement: str
    uncertainty_note: str
    evidence_ids: tuple[str, ...]


def attach_forecast(occupation_id: str, evidence_ids: tuple[str, ...]) -> ForecastNote:
    return ForecastNote(
        occupation_id,
        "0_to_12_months",
        "Any near-term change remains conditional on new evidence and is not a current requirement.",
        "Forecasts are optional and must not gate publication of the skills reference.",
        evidence_ids,
    )
