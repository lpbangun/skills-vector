"""OMP subscription agent runner for research decision passes.

Every research stage that needs a model decision runs through the actual OMP
CLI with the pinned subscription task model and an externally supplied runtime
overlay (fallback disabled). Each call is bounded by a process timeout, counted
against the run's fixed call budget, and verified from the session record:
resolved provider/model must match the pin and ``resolvedModelIsFallback`` must
be false — otherwise the run fails instead of substituting a model.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 - fixed argv, no shell
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .limits import MODEL_NAME, MODEL_PIN, MODEL_PROVIDER, MODEL_THINKING, RESEARCH_LIMITS
from .release import sha256_file, sha256_text


class ResearchError(RuntimeError):
    """Fail-closed research error (no silent retry or substitution)."""


class BudgetExceeded(ResearchError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class AgentCallResult:
    ok: bool
    stage: str
    call_index: int
    text: str = ""
    exit_code: int | None = None
    attempts: int = 0
    error: str | None = None
    elapsed_ms: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    thinking: str = ""
    fallback: bool | None = None
    session_record: str | None = None
    session_sha256: str | None = None
    prompt_sha256: str = ""
    stdout_path: str | None = None
    problems: list[str] = field(default_factory=list)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "call_index": self.call_index,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "attempts": self.attempts,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "cost_usd": round(self.cost_usd, 6),
            "provider": self.provider,
            "model": self.model,
            "thinking": self.thinking,
            "model_fallback": self.fallback,
            "session_record": self.session_record,
            "session_sha256": self.session_sha256,
            "prompt_sha256": self.prompt_sha256,
            "stdout_path": self.stdout_path,
            "problems": list(self.problems),
        }


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model output (fences tolerated)."""

    if not text:
        return None
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("```")
        for part in body:
            chunk = part.strip()
            if chunk.startswith("{") and chunk.endswith("}"):
                candidates.append(chunk)
            elif chunk.startswith("json"):
                inner = chunk[4:].strip()
                if inner.startswith("{"):
                    candidates.append(inner)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_session_record(path: Path) -> dict[str, Any]:
    """Read an OMP session record: resolved model, thinking level, cost, fallback."""

    info: dict[str, Any] = {
        "provider": "",
        "model": "",
        "thinking": "",
        "fallback": None,
        "cost_usd": 0.0,
        "records": 0,
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return info
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        info["records"] += 1
        kind = record.get("type")
        if kind == "model_change":
            model = str(record.get("model") or "")
            if "/" in model:
                provider, _, name = model.partition("/")
                info["provider"] = provider
                info["model"] = name
            else:
                info["model"] = model
            info["fallback"] = record.get("resolvedModelIsFallback")
        elif kind == "thinking_level_change":
            info["thinking"] = str(record.get("thinkingLevel") or "")
        elif kind == "message":
            message = record.get("message") if isinstance(record.get("message"), dict) else {}
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
            if isinstance(cost.get("total"), (int, float)):
                info["cost_usd"] += float(cost["total"])
            if message.get("provider"):
                info["provider"] = str(message.get("provider"))
            if message.get("model"):
                info["model"] = str(message.get("model"))
    return info


class CallBudget:
    """Fixed per-run model-call counter (never reset inside a run)."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = int(limit if limit is not None else RESEARCH_LIMITS["max_model_calls"])
        self.calls = 0
        self.cost_usd = 0.0

    def reserve(self) -> int:
        if self.calls >= self.limit:
            raise BudgetExceeded(f"model call budget exhausted ({self.limit} calls per run)")
        self.calls += 1
        return self.calls


class OmpAgentRunner:
    """Run model decision passes through the authenticated OMP CLI."""

    def __init__(
        self,
        *,
        run_dir: Path,
        overlay: Path,
        session_root: Path | None = None,
        model_pin: str = MODEL_PIN,
        thinking: str = MODEL_THINKING,
        timeout_seconds: int | None = None,
        retries: int | None = None,
        budget: CallBudget | None = None,
        runner: Callable[..., tuple[int, str, str]] | None = None,
        binary: str = "omp",
    ) -> None:
        self.run_dir = Path(run_dir)
        self.overlay = Path(overlay)
        self.session_root = Path(session_root) if session_root else self.run_dir / "sessions"
        self.model_pin = model_pin
        self.thinking = thinking
        self.timeout_seconds = int(timeout_seconds if timeout_seconds is not None else RESEARCH_LIMITS["model_timeout_seconds"])
        self.retries = int(retries if retries is not None else RESEARCH_LIMITS["infrastructure_retries"])
        self.budget = budget or CallBudget()
        self._runner = runner
        self.binary = binary
        self.calls: list[AgentCallResult] = []
        self.session_root.mkdir(parents=True, exist_ok=True)

    # -- command ---------------------------------------------------------

    def command(self, prompt: str) -> list[str]:
        base_model = self.model_pin.split(":")[0]
        return [
            self.binary,
            "--model",
            base_model,
            "--thinking",
            self.thinking,
            "--config",
            str(self.overlay),
            "--session-dir",
            str(self.session_root),
            "--no-title",
            "--no-skills",
            "--no-rules",
            "--no-extensions",
            "--no-lsp",
            "--no-tools",
            "--max-time",
            str(self.timeout_seconds),
            "--mode",
            "text",
            "-p",
            prompt,
        ]

    def _default_runner(self, cmd: list[str], *, timeout: int) -> tuple[int, str, str]:
        completed = subprocess.run(  # noqa: S603 - fixed argv from command()
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.run_dir),
            env={**os.environ, "PI_NO_PTY": "1"},
        )
        return completed.returncode, completed.stdout, completed.stderr

    def _latest_session(self, since: float) -> Path | None:
        best: Path | None = None
        best_mtime = since
        for path in self.session_root.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= best_mtime - 1.0 and (best is None or mtime > best_mtime):
                best = path
                best_mtime = mtime
        return best

    # -- call ------------------------------------------------------------

    def call(self, stage: str, prompt: str) -> AgentCallResult:
        if not self.overlay.is_file():
            raise ResearchError(
                f"runtime overlay {self.overlay} is required for research runs (fallback stays externally disabled)"
            )
        call_index = self.budget.reserve()
        prompt_dir = self.run_dir / "prompts"
        stdout_dir = self.run_dir / "responses"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        stdout_dir.mkdir(parents=True, exist_ok=True)
        call_slug = f"{call_index:02d}-{stage}"
        (prompt_dir / f"{call_slug}.txt").write_text(prompt, encoding="utf-8")
        started = time.monotonic()
        since = time.time()
        attempts = 0
        last: AgentCallResult | None = None
        while attempts <= self.retries:
            attempts += 1
            try:
                if self._runner is not None:
                    exit_code, stdout, stderr = self._runner(self.command(prompt), timeout=self.timeout_seconds)
                else:
                    exit_code, stdout, stderr = self._default_runner(self.command(prompt), timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                last = AgentCallResult(
                    ok=False, stage=stage, call_index=call_index, attempts=attempts,
                    error=f"model process timed out after {self.timeout_seconds}s",
                )
                continue
            except OSError as exc:
                raise ResearchError(f"cannot launch OMP agent runtime: {exc}") from exc
            stdout_path = stdout_dir / f"{call_slug}.txt"
            stdout_path.write_text(stdout or "", encoding="utf-8")
            if stderr:
                (stdout_dir / f"{call_slug}.stderr.txt").write_text(stderr, encoding="utf-8")
            if exit_code != 0:
                last = AgentCallResult(
                    ok=False, stage=stage, call_index=call_index, attempts=attempts, exit_code=exit_code,
                    error=f"agent process exited {exit_code}", stdout_path=str(stdout_path),
                )
                continue
            session = self._latest_session(since)
            info = parse_session_record(session) if session else {"provider": "", "model": "", "thinking": "", "fallback": None, "cost_usd": 0.0, "records": 0}
            problems: list[str] = []
            if not session:
                problems.append("no OMP session record produced for this call")
            else:
                if info["fallback"] is True:
                    problems.append("session record reports resolvedModelIsFallback=true")
                if info["model"] and info["model"] != MODEL_NAME:
                    problems.append(f"session record model {info['model']!r} != pinned {MODEL_NAME!r}")
                if info["provider"] and info["provider"] != MODEL_PROVIDER:
                    problems.append(f"session record provider {info['provider']!r} != pinned {MODEL_PROVIDER!r}")
                if not info["model"]:
                    problems.append("session record has no resolved model")
                if info["thinking"] and info["thinking"] != self.thinking:
                    problems.append(f"session record thinking {info['thinking']!r} != pinned {self.thinking!r}")
            self.budget.cost_usd += float(info.get("cost_usd") or 0.0)
            result = AgentCallResult(
                ok=not problems,
                stage=stage,
                call_index=call_index,
                text=stdout or "",
                exit_code=exit_code,
                attempts=attempts,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                cost_usd=float(info.get("cost_usd") or 0.0),
                provider=str(info.get("provider") or ""),
                model=str(info.get("model") or ""),
                thinking=str(info.get("thinking") or ""),
                fallback=info.get("fallback") if isinstance(info.get("fallback"), bool) else None,
                session_record=str(session) if session else None,
                session_sha256=sha256_file(session) if session else None,
                prompt_sha256=sha256_text(prompt),
                stdout_path=str(stdout_path),
                problems=problems,
            )
            self.calls.append(result)
            if result.ok:
                return result
            if any("session record" in problem or "no OMP session" in problem for problem in problems):
                raise ResearchError(
                    f"model identity verification failed for {stage}: " + "; ".join(problems)
                )
            last = result
        if last is None:
            last = AgentCallResult(ok=False, stage=stage, call_index=call_index, attempts=attempts, error="unknown failure")
        self.calls.append(last)
        return last

    def require_json(self, stage: str, prompt: str) -> tuple[dict[str, Any], AgentCallResult]:
        """Run a decision pass and require a parseable JSON object back."""

        result = self.call(stage, prompt)
        if not result.ok:
            raise ResearchError(f"{stage} model call failed: {result.error or '; '.join(result.problems)}")
        payload = extract_json_object(result.text)
        if payload is None:
            raise ResearchError(f"{stage} model output was not a JSON object (semantic failure; not retried)")
        return payload, result

    def receipt_calls(self) -> list[dict[str, Any]]:
        return [call.as_metadata() for call in self.calls]


__all__ = [
    "AgentCallResult",
    "BudgetExceeded",
    "CallBudget",
    "OmpAgentRunner",
    "ResearchError",
    "extract_json_object",
    "parse_session_record",
]
