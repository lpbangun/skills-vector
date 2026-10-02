"""Hard local ceilings for public retrieval and operator-funded market research."""

from __future__ import annotations

RESEARCH_LIMITS: dict[str, int | bool] = {
    "max_retrievals": 60,
    "max_model_calls": 12,
    "max_input_tokens": 8192,
    "max_output_tokens": 2048,
    "max_response_bytes": 2 * 1024 * 1024,
    "max_quote_chars": 280,
    "max_extract_chars": 24000,
    "max_postings_per_role": 60,
    "max_boards_per_role": 16,
    "max_foundations_per_role": 4,
    "retrieval_timeout_seconds": 20,
    "model_timeout_seconds": 120,
    "max_retries": 2,
    "max_run_seconds": 1800,
    "serialized_calls": True,
}
