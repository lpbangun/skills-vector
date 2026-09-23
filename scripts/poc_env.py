#!/usr/bin/env python3
"""Validate and run a lane-isolated, offline-by-default POC environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Sequence


EXPECTED_PYTHON = (3, 12, 3)
REQUIRED_IMPORTS = ("langgraph", "playwright")
LANE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SENSITIVE_ENV_EXACT = {
    "DEEPINFRA_API_KEY",
    "CURSOR_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY",
}
RUNTIME_ENV_KEYS = {
    "SKILLS_VECTOR_DATABASE",
    "SKILLS_VECTOR_RELEASES",
    "SKILLS_VECTOR_MONTHLY_BUDGET_USD",
    "SKILLS_VECTOR_RUNTIME",
    "SKILLS_VECTOR_OFFLINE",
    "SKILLS_VECTOR_LIVE_CONFIGURED",
    "SKILLS_VECTOR_OUTPUT",
    "VIRTUAL_ENV",
    "PYTHONHOME",
    "PYTHONPATH",
}


class ConfigError(ValueError):
    """Raised when a lane configuration cannot be used safely."""


@dataclass(frozen=True)
class LaneConfig:
    config_path: Path
    lane: str
    worktree: Path
    corpus_directory: Path
    corpus_manifest: Path
    lane_root: Path
    venv: Path
    state: Path
    releases: Path
    cache: Path
    output: Path
    browsers_path: Path | None
    offline_by_default: bool
    live_inference: str

    @property
    def venv_python(self) -> Path:
        return self.venv / "bin" / "python"


def _is_within(path: Path, root: Path) -> bool:
    """Return whether path is root or a descendant of root."""

    return path == root or root in path.parents


def _paths_related(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _absolute_path(raw: Any, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{field} must be a non-empty absolute path")
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigError(f"{field} must be an absolute path")
    return path.resolve(strict=False)


def _required_string(mapping: dict[str, Any], key: str, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{field} does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must contain a JSON object")
    return value


def load_config(config_path: str | Path) -> LaneConfig:
    """Load and validate one nonsecret lane configuration."""

    path = Path(config_path)
    if not path.is_absolute():
        raise ConfigError("--config must be an absolute path")
    path = path.resolve(strict=False)
    if not path.is_file():
        raise ConfigError("config file does not exist")
    raw = _load_json(path, "config")

    if raw.get("schema_version") != 1:
        raise ConfigError("config schema_version must be 1")
    lane = _required_string(raw, "lane", "lane")
    if not LANE_NAME_RE.fullmatch(lane):
        raise ConfigError("lane must contain only letters, numbers, '.', '_' or '-'")

    worktree = _absolute_path(raw.get("worktree"), "worktree")
    corpus = raw.get("corpus")
    if not isinstance(corpus, dict):
        raise ConfigError("corpus must be an object")
    corpus_directory = _absolute_path(corpus.get("directory"), "corpus.directory")
    corpus_manifest = _absolute_path(corpus.get("manifest"), "corpus.manifest")
    if corpus.get("read_only") is not True:
        raise ConfigError("corpus.read_only must be true")
    if not _is_within(corpus_manifest, corpus_directory):
        raise ConfigError("corpus.manifest must be inside corpus.directory")

    lane_root = _absolute_path(raw.get("lane_root"), "lane_root")
    venv = _absolute_path(raw.get("venv"), "venv")
    if not _is_within(venv, lane_root) or venv == lane_root:
        raise ConfigError("venv must be under lane_root")
    if _paths_related(lane_root, corpus_directory):
        raise ConfigError("lane_root must not overlap the shared corpus")
    if _paths_related(lane_root, worktree) and not _is_within(lane_root, worktree):
        raise ConfigError("lane_root must not contain worktree")
    if lane_root == worktree:
        raise ConfigError("lane_root must be distinct from worktree")

    paths = raw.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError("paths must be an object")
    path_values = {
        key: _absolute_path(paths.get(key), f"paths.{key}")
        for key in ("state", "releases", "cache", "output")
    }
    writable_roots = {
        "state": path_values["state"].parent,
        "releases": path_values["releases"],
        "cache": path_values["cache"],
        "output": path_values["output"],
    }
    for name, candidate in writable_roots.items():
        if not _is_within(candidate, lane_root) or candidate == lane_root:
            raise ConfigError(f"paths.{name} must be under lane_root")
        if _paths_related(candidate, corpus_directory):
            raise ConfigError(f"paths.{name} must not overlap the shared corpus")
        if _paths_related(candidate, worktree) and not _is_within(candidate, worktree):
            raise ConfigError(f"paths.{name} must not contain worktree")
        if _paths_related(candidate, venv):
            raise ConfigError(f"paths.{name} must not overlap venv")
    roots = list(writable_roots.items())
    for index, (left_name, left) in enumerate(roots):
        for right_name, right in roots[index + 1 :]:
            if _paths_related(left, right):
                raise ConfigError(f"writable paths {left_name} and {right_name} overlap")

    browser = raw.get("browser")
    if not isinstance(browser, dict) or "browsers_path" not in browser:
        raise ConfigError("browser.browsers_path must be provided, or explicitly null")
    browsers_path = browser["browsers_path"]
    if browsers_path is not None:
        browsers_path = _absolute_path(browsers_path, "browser.browsers_path")

    mode = raw.get("mode")
    if not isinstance(mode, dict) or mode.get("offline_by_default") is not True:
        raise ConfigError("mode.offline_by_default must be true")
    live_inference = mode.get("live_inference")
    if live_inference != "not_configured":
        raise ConfigError("live inference is not configured; only offline mode is supported")

    return LaneConfig(
        config_path=path,
        lane=lane,
        worktree=worktree,
        corpus_directory=corpus_directory,
        corpus_manifest=corpus_manifest,
        lane_root=lane_root,
        venv=venv,
        state=path_values["state"],
        releases=path_values["releases"],
        cache=path_values["cache"],
        output=path_values["output"],
        browsers_path=browsers_path,
        offline_by_default=True,
        live_inference=live_inference,
    )


def _source_path(config: LaneConfig, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigError("corpus source path must be a non-empty relative path")
    normalized = raw_path.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise ConfigError("corpus source path traversal is not allowed")
    candidate = (config.corpus_manifest.parent / relative).resolve(strict=False)
    if not _is_within(candidate, config.corpus_manifest.parent) or not _is_within(
        candidate, config.corpus_directory
    ):
        raise ConfigError("corpus source path traversal is not allowed")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_corpus(config: LaneConfig) -> dict[str, int]:
    """Validate the shared manifest and hashes without fetching any data."""

    if not config.corpus_directory.is_dir():
        raise ConfigError("shared corpus directory is missing")
    if not config.corpus_manifest.is_file():
        raise ConfigError("corpus manifest is missing")
    manifest = _load_json(config.corpus_manifest, "corpus manifest")
    if manifest.get("schema_version") not in (1, "skills-vector.poc-corpus.v1"):
        raise ConfigError("unsupported corpus manifest schema_version")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ConfigError("corpus manifest sources must be non-empty")

    seen_ids: set[str] = set()
    present = 0
    verified = 0
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ConfigError(f"corpus source {index} must be an object")
        source_id = _required_string(source, "id", f"corpus source {index}.id")
        if source_id in seen_ids:
            raise ConfigError(f"duplicate corpus source id: {source_id}")
        seen_ids.add(source_id)
        if "url" not in source or not isinstance(source["url"], str):
            raise ConfigError(f"corpus source {source_id}.url must be a string")
        source_path = _source_path(config, source.get("path"))
        status = _required_string(source, "status", f"corpus source {source_id}.status").casefold()
        expected = source.get("sha256")
        if expected not in (None, "") and (
            not isinstance(expected, str) or not SHA256_RE.fullmatch(expected)
        ):
            raise ConfigError(f"corpus source {source_id}.sha256 must be a SHA-256 hex digest")

        if status not in ("present", "retrieved"):
            continue
        present += 1
        if not source_path.is_file():
            raise ConfigError(f"present corpus source {source_id} is missing")
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise ConfigError(f"present corpus source {source_id} needs a SHA-256 hash")
        if _sha256(source_path).casefold() != expected.casefold():
            raise ConfigError(f"corpus source {source_id} hash mismatch")
        verified += 1
        if "extract_path" in source or "extract_sha256" in source:
            extract = _source_path(config, source.get("extract_path"))
            extract_hash = source.get("extract_sha256")
            if not isinstance(extract_hash, str) or not SHA256_RE.fullmatch(extract_hash):
                raise ConfigError(f"corpus extract {source_id} needs a SHA-256 hash")
            if not extract.is_file():
                raise ConfigError(f"corpus extract {source_id} is missing")
            if _sha256(extract).casefold() != extract_hash.casefold():
                raise ConfigError(f"corpus extract {source_id} hash mismatch")
            verified += 1
    if present == 0:
        raise ConfigError("corpus has no retrieved sources")
    return {"sources": len(sources), "present": present, "verified": verified}


def _sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return name in SENSITIVE_ENV_EXACT or any(
        token in upper
        for token in ("API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY")
    )


def _sanitized_base_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if _sensitive_env_name(key) or key in RUNTIME_ENV_KEYS or key.startswith("SKILLS_VECTOR_"):
            env.pop(key, None)
    return env


def _ensure_lane_layout(config: LaneConfig) -> None:
    config.lane_root.mkdir(parents=True, exist_ok=True)
    marker = config.lane_root / ".poc-lane.json"
    expected_marker = {"schema_version": 1, "lane": config.lane, "worktree": str(config.worktree)}
    if marker.exists():
        existing = _load_json(marker, "lane marker")
        if existing != expected_marker:
            raise ConfigError("lane_root collision: existing marker belongs to another lane")
    else:
        marker.write_text(json.dumps(expected_marker, sort_keys=True) + "\n", encoding="utf-8")

    config.state.parent.mkdir(parents=True, exist_ok=True)
    for directory in (config.releases, config.cache, config.output, config.cache / "tmp", config.cache / "pip", config.cache / "uv"):
        directory.mkdir(parents=True, exist_ok=True)


def build_run_env(config: LaneConfig) -> dict[str, str]:
    """Create the sanitized environment used for every lane command."""

    _ensure_lane_layout(config)
    if not config.venv.is_dir() or not config.venv_python.is_file():
        raise ConfigError("matching lane venv is missing")
    if not os.access(config.venv_python, os.X_OK):
        raise ConfigError("matching lane venv python is not executable")

    env = _sanitized_base_env()
    env["SKILLS_VECTOR_DATABASE"] = str(config.state)
    env["SKILLS_VECTOR_RELEASES"] = str(config.releases)
    env["SKILLS_VECTOR_MONTHLY_BUDGET_USD"] = "0"
    env["SKILLS_VECTOR_RUNTIME"] = "offline"
    env["SKILLS_VECTOR_OFFLINE"] = "1"
    env["SKILLS_VECTOR_LIVE_CONFIGURED"] = "0"
    env["SKILLS_VECTOR_OUTPUT"] = str(config.output)
    env["PYTHONNOUSERSITE"] = "1"
    env["VIRTUAL_ENV"] = str(config.venv)
    env["UV_PROJECT_ENVIRONMENT"] = str(config.venv)
    env["XDG_CACHE_HOME"] = str(config.cache)
    env["PIP_CACHE_DIR"] = str(config.cache / "pip")
    env["UV_CACHE_DIR"] = str(config.cache / "uv")
    env["TMPDIR"] = str(config.cache / "tmp")
    env["TEMP"] = str(config.cache / "tmp")
    env["TMP"] = str(config.cache / "tmp")
    if config.browsers_path is None:
        env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    else:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(config.browsers_path)

    existing_path = env.get("PATH", os.defpath)
    env["PATH"] = str(config.venv / "bin") + os.pathsep + existing_path
    return env


def run_command(config: LaneConfig, command: Sequence[str]) -> int:
    if not command:
        raise ConfigError("run needs a command after --")
    env = build_run_env(config)
    completed = subprocess.run(list(command), cwd=config.worktree, env=env, check=False)
    return completed.returncode


def _filesystem_check(config: LaneConfig) -> dict[str, Any]:
    if not config.worktree.is_dir():
        return {"ok": False, "reason": "worktree is missing"}
    if not config.corpus_directory.is_dir() or not config.corpus_manifest.is_file():
        return {"ok": False, "reason": "shared corpus directory or manifest is missing"}
    targets = {
        "state": config.state.parent,
        "releases": config.releases,
        "cache": config.cache,
        "output": config.output,
    }
    for name, target in targets.items():
        if target.exists() and not target.is_dir():
            return {"ok": False, "reason": f"{name} target is not a directory"}
        probe = target
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not os.access(probe, os.W_OK | os.X_OK):
            return {"ok": False, "reason": f"{name} target is not writable"}

    marker = config.lane_root / ".poc-lane.json"
    if marker.exists():
        try:
            existing = _load_json(marker, "lane marker")
        except ConfigError as exc:
            return {"ok": False, "reason": str(exc)}
        expected = {"schema_version": 1, "lane": config.lane, "worktree": str(config.worktree)}
        if existing != expected:
            return {"ok": False, "reason": "lane_root collision"}
        marker_status = "claimed"
    else:
        marker_status = "unclaimed"
    return {"ok": True, "lane_marker": marker_status}


def _runtime_probe(config: LaneConfig) -> dict[str, Any]:
    if not config.venv.is_dir() or not config.venv_python.is_file():
        return {"ok": False, "reason": "matching lane venv is missing"}
    env = _sanitized_base_env()
    env["PYTHONNOUSERSITE"] = "1"
    env["VIRTUAL_ENV"] = str(config.venv)
    env["PATH"] = str(config.venv / "bin") + os.pathsep + env.get("PATH", os.defpath)
    code = (
        "import importlib, json, sys\n"
        "names = %r\n"
        "errors = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception as exc:\n"
        "        errors[name] = type(exc).__name__\n"
        "print(json.dumps({'python': list(sys.version_info[:3]), 'errors': errors}))\n"
    ) % (REQUIRED_IMPORTS,)
    try:
        completed = subprocess.run(
            [str(config.venv_python), "-c", code],
            cwd=config.worktree,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "reason": "lane runtime probe failed"}
    if completed.returncode != 0:
        return {"ok": False, "reason": "lane runtime probe failed"}
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "reason": "lane runtime probe returned invalid data"}
    version_ok = tuple(result.get("python", ())) == EXPECTED_PYTHON
    imports_ok = not result.get("errors")
    return {
        "ok": version_ok and imports_ok,
        "python": ".".join(str(part) for part in result.get("python", ())),
        "python_exact": version_ok,
        "required_imports": {name: name not in result.get("errors", {}) for name in REQUIRED_IMPORTS},
    }


def _lock_check(config: LaneConfig) -> dict[str, Any]:
    lockfile = config.worktree / "uv.lock"
    if not lockfile.is_file():
        return {"ok": False, "reason": "uv.lock is missing"}
    uv = shutil.which("uv")
    if uv is None:
        return {"ok": False, "reason": "uv is unavailable"}
    env = _sanitized_base_env()
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(config.venv)
    try:
        completed = subprocess.run(
            [uv, "lock", "--check", "--offline"],
            cwd=config.worktree,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if completed.returncode == 0:
            completed = subprocess.run(
                [uv, "sync", "--frozen", "--group", "dev", "--check", "--offline"],
                cwd=config.worktree,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "reason": "uv lock check failed"}
    return {"ok": completed.returncode == 0, "reason": "uv lock check failed" if completed.returncode else "ok"}


def _browser_check(config: LaneConfig) -> dict[str, Any]:
    if config.browsers_path is None:
        return {"ok": True, "status": "not_configured"}
    if not config.browsers_path.is_dir():
        return {"ok": False, "reason": "configured browser location is missing"}
    return {"ok": True, "status": "configured"}


def doctor_report(config_path: str | Path) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "NOT_READY",
        "live_status": "LIVE_NOT_CONFIGURED",
        "live_inference_passed": False,
    }
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        base["error"] = str(exc)
        return base

    checks: dict[str, Any] = {}
    checks["filesystem"] = _filesystem_check(config)
    try:
        checks["corpus"] = {"ok": True, **verify_corpus(config)}
    except ConfigError as exc:
        checks["corpus"] = {"ok": False, "reason": str(exc)}
    checks["runtime"] = _runtime_probe(config)
    checks["lock"] = _lock_check(config)
    checks["browser"] = _browser_check(config)
    base["lane"] = config.lane
    base["checks"] = checks
    required = ("filesystem", "corpus", "runtime", "lock", "browser")
    if all(checks[name].get("ok") is True for name in required):
        base["status"] = "READY_OFFLINE"
    return base


def _browser_smoke(receipt_path: Path, browsers_path: Path | None) -> int:
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "browser": {"name": "chromium"},
        "checks": [],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if browsers_path is not None:
            if not browsers_path.is_absolute():
                raise ConfigError("--browsers-path must be an absolute path")
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
        else:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

        from playwright.sync_api import sync_playwright

        try:
            playwright_version = package_version("playwright")
        except PackageNotFoundError:
            playwright_version = "unknown"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                receipt["browser"].update(
                    {
                        "version": browser.version,
                        "executable": str(playwright.chromium.executable_path),
                        "playwright_version": playwright_version,
                    }
                )
                receipt["checks"].append({"name": "headless_chromium_launch", "passed": True})
                page = browser.new_page()
                page.set_content(
                    "<!doctype html><html><head><title>POC environment smoke</title></head>"
                    "<body><main id='smoke-marker'>offline-browser-ok</main></body></html>",
                    wait_until="load",
                )
                receipt["checks"].extend(
                    [
                        {
                            "name": "no_sandbox_launch_arg",
                            "passed": True,
                        },
                        {
                            "name": "local_content_title",
                            "passed": page.title() == "POC environment smoke",
                        },
                        {
                            "name": "local_content_marker",
                            "passed": page.locator("#smoke-marker").text_content() == "offline-browser-ok",
                        },
                    ]
                )
                page.close()
            finally:
                browser.close()
        receipt["status"] = "PASS" if all(check["passed"] for check in receipt["checks"]) else "FAIL"
    except Exception as exc:  # pragma: no cover - exercised by the real smoke command
        receipt["error_type"] = type(exc).__name__

    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if receipt["status"] == "PASS" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    doctor = subparsers.add_parser("doctor", help="validate one lane configuration")
    doctor.add_argument("--config", required=True, help="absolute lane JSON config path")

    run = subparsers.add_parser("run", help="run a command with the lane environment")
    run.add_argument("--config", required=True, help="absolute lane JSON config path")
    run.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    smoke = subparsers.add_parser("browser-smoke", help="launch installed Chromium headlessly")
    smoke.add_argument("--browsers-path", help="absolute PLAYWRIGHT_BROWSERS_PATH, if configured")
    smoke.add_argument("--receipt", required=True, help="absolute machine-readable receipt path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.subcommand == "doctor":
        report = doctor_report(args.config)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "READY_OFFLINE" else 1

    if args.subcommand == "run":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        try:
            config = load_config(args.config)
            return run_command(config, command)
        except ConfigError as exc:
            print(f"poc environment error: {exc}", file=sys.stderr)
            return 2

    browsers_path = None if args.browsers_path is None else Path(args.browsers_path)
    receipt_path = Path(args.receipt)
    if not receipt_path.is_absolute():
        print("poc environment error: --receipt must be an absolute path", file=sys.stderr)
        return 2
    return _browser_smoke(receipt_path, browsers_path)


if __name__ == "__main__":
    raise SystemExit(main())
