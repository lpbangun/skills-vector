"""Bounded research resource ceilings (fixed; never raised automatically).

The pipeline refuses to start when a requested action would exceed these caps.
They are code constants, not runtime knobs: a run may request fewer retrievals
or model calls, never more.
"""

from __future__ import annotations

RESEARCH_LIMITS: dict[str, int | float | bool] = {
    "max_retrievals": 60,
    "max_model_calls": 12,
    "max_response_bytes": 2 * 1024 * 1024,
    "max_quote_chars": 280,
    "max_extract_chars": 24000,
    "max_postings_per_role": 60,
    "max_boards_per_role": 16,
    "max_foundations_per_role": 4,
    "retrieval_timeout_seconds": 20,
    "model_timeout_seconds": 300,
    "infrastructure_retries": 2,
    "serialized_calls": True,
}

MODEL_PIN = "opencode-go/deepseek-v4.1-flash:max"
MODEL_PROVIDER = "opencode-go"
MODEL_NAME = "deepseek-v4.1-flash"
MODEL_THINKING = "max"
