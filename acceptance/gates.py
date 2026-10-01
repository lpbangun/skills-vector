#!/usr/bin/env python3
"""Executable acceptance gates for BENCHMARK.md (benchmark.round-zero.v1,
protocol completion.v2).

Evidence collector and verifier. It does NOT score categories and never
relaxes the benchmark: advisor judgment against §4 anchors prevails. Each gate
verdict is PASS | FAIL | BLOCKED | UNKNOWN with a machine-readable receipt.

This is the corrected, strict version of the round-zero protocol. Every gate
is executable; missing evidence is UNKNOWN, never pass; self-attested verdict
fields are only trusted when the independent evidence they reference verifies
(files exist, sha256 matches, live HTTP is re-executed).

Exit codes: 0 all gates PASS · 1 any gate FAIL/BLOCKED · 2 any gate UNKNOWN
(and no FAIL/BLOCKED) · 2 on config errors.

Usage:
    python3 acceptance/gates.py --help
    python3 acceptance/gates.py gate HG01 [--config acceptance/config.json]
    python3 acceptance/gates.py all
    python3 acceptance/gates.py summary

Gate list: HG01..HG18 — see BENCHMARK.md §5 and acceptance/README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import socket
import subprocess  # noqa: S404 - fixed argv, no shell
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = ACCEPTANCE_DIR / "evidence"
DEFAULT_CONFIG = ACCEPTANCE_DIR / "config.json"

GATE_IDS = [f"HG{i:02d}" for i in range(1, 19)]
REQUIRED_PROTOCOL_VERSION = "completion.v2"
REQUIRED_VARIANTS = ("product-growth", "growth-marketing", "sales-account-executive")
REQUIRED_CATEGORIES = (
    "research_evidence_quality",
    "market_intelligence_usefulness",
    "learning_priority_usefulness",
    "frontend_inspection_ux",
    "search_api_agent_interoperability",
    "deployed_e2e_functionality",
    "resilience_security_provenance",
)
JOURNEY_STAGES_REQUIRED = ("browse", "query", "finding", "inspect", "source")
DEFAULT_INSPECTOR_SECTIONS = ("extracts", "admissions", "exclusions", "mappings", "disagreements", "lineage")
MAX_HTTP_BYTES = 10 * 1024 * 1024


class ConfigError(Exception):
    pass


@dataclass
class GateResult:
    gate: str
    verdict: str  # PASS | FAIL | BLOCKED | UNKNOWN
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    ran_at: str = ""
    exit_code: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict,
            "detail": self.detail,
            "evidence": self.evidence,
            "ran_at": self.ran_at or datetime.now(UTC).isoformat(),
            "exit_code": self.exit_code,
        }


def _fail(gate: str, detail: str) -> GateResult:
    return GateResult(gate=gate, verdict="FAIL", detail=detail)


def _unknown(gate: str, detail: str) -> GateResult:
    return GateResult(gate=gate, verdict="UNKNOWN", detail=detail)


def _pass(gate: str, detail: str, evidence: dict[str, Any] | None = None) -> GateResult:
    return GateResult(gate=gate, verdict="PASS", detail=detail, evidence=evidence or {})


def _blocked(gate: str, detail: str) -> GateResult:
    return GateResult(gate=gate, verdict="BLOCKED", detail=detail)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("schema_version") != "benchmark.round-zero.v1":
        raise ConfigError("config.schema_version must be benchmark.round-zero.v1")
    if cfg.get("protocol_version") != REQUIRED_PROTOCOL_VERSION:
        raise ConfigError(
            f"config.protocol_version must be {REQUIRED_PROTOCOL_VERSION!r} "
            "(stale pre-completion config refused; refill from the corrected slate)"
        )
    for key in ("candidate", "deploy", "occupations", "gates", "paths", "review"):
        if key not in cfg:
            raise ConfigError(f"config missing required key: {key}")
    cand = cfg["candidate"]
    commit_sha = cand.get("commit_sha") or ""
    if commit_sha and not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise ConfigError("candidate.commit_sha must be a full 40-char hex SHA or empty (slate)")
    paths = cfg["paths"]
    for key in ("dump_dir", "protocol_completion_dir"):
        if not str(paths.get(key) or "").strip():
            raise ConfigError(f"paths.{key} must be set (frozen dump directories)")
    occs = cfg.get("occupations") or []
    if len(occs) != 3:
        raise ConfigError(f"occupations must list exactly the three benchmark occupations (got {len(occs)})")
    for occ in occs:
        if not str(occ.get("slug") or "").strip() or not str(occ.get("label") or "").strip():
            raise ConfigError("every occupation needs nonempty slug and label")
    growth = [o for o in occs if "growth" in str(o.get("slug"))]
    if len(growth) != 1 or not set(growth[0].get("growth_variants") or []) >= set(REQUIRED_VARIANTS):
        raise ConfigError(f"the growth occupation must carry variants {list(REQUIRED_VARIANTS)}")
    review = cfg["review"]
    if not str(review.get("advisor_pin") or "").strip():
        raise ConfigError("review.advisor_pin must be set")
    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_receipt(gate: str, result: GateResult) -> Path:
    out_dir = EVIDENCE_DIR / gate
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "receipt.json"
    result.exit_code = {"PASS": 0, "FAIL": 1, "BLOCKED": 1, "UNKNOWN": 2}.get(result.verdict, 1)
    payload = result.to_json()
    payload["ran_at"] = datetime.now(UTC).isoformat()
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def http_get(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "skills-vector-acceptance/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.status, resp.read(MAX_HTTP_BYTES)


def http_post_json(url: str, body: bytes, timeout: float = 20.0) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "skills-vector-acceptance/2.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_HTTP_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read(MAX_HTTP_BYTES)
        except Exception:  # noqa: BLE001 - body read of an error response is best effort
            return exc.code, b""
    except (urllib.error.URLError, socket.timeout):
        raise


def source_has_no_secrets(text: str) -> bool:
    """Heuristic: reject obvious private key material / token shapes."""
    patterns = (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"AKIA[0-9A-Z]{16}",
        r"sk-[A-Za-z0-9]{20,}",
        r"sk-proj-[A-Za-z0-9_-]{30,}",
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"gsk_[A-Za-z0-9]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"Bearer\s+[A-Za-z0-9\-_.]{25,}",
    )
    return not any(re.search(p, text) for p in patterns)


def _read_json(path_text: str | Path) -> Any | None:
    p = Path(path_text)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _check_file_ref(ref: Any, what: str, problems: list[str]) -> None:
    """Verify a {path, sha256} reference: file exists, hash matches."""
    if not isinstance(ref, dict) or not str(ref.get("path") or "").strip():
        problems.append(f"{what}: file reference must be an object {{path, sha256}}")
        return
    p = Path(str(ref["path"]))
    if not p.is_file():
        problems.append(f"{what}: referenced file missing: {ref['path']}")
        return
    expected = str(ref.get("sha256") or "").strip()
    if not expected:
        problems.append(f"{what}: sha256 not recorded for {ref['path']}")
        return
    actual = sha256_file(p)
    if actual != expected:
        problems.append(f"{what}: sha256 mismatch for {ref['path']} (recorded {expected[:12]}, actual {actual[:12]})")


def _verify_session_record(
    ref: Any,
    what: str,
    problems: list[str],
    expect_contains: tuple[str, ...] = (),
    expect_regex: tuple[str, ...] = (),
) -> None:
    """A runtime-record reference: file exists, hash matches, text contains expected strings."""
    _check_file_ref(ref, what, problems)
    p = Path(str(ref.get("path") or "")) if isinstance(ref, dict) else None
    if p and p.is_file() and (expect_contains or expect_regex):
        text = p.read_text(encoding="utf-8", errors="replace")
        for needle in expect_contains:
            if needle and needle not in text:
                problems.append(f"{what}: record text does not contain {needle!r}")
        for pattern in expect_regex:
            if pattern and not re.search(pattern, text):
                problems.append(f"{what}: record text does not match /{pattern}/")


def _manifest_verify(dump_dir: Path) -> tuple[bool, list[str], int]:
    """Verify a frozen dump manifest. Accepts the frozen shape
    files: [{path, sha256}, ...] and the compact shape files: {rel: sha256}.
    Empty/unsupported shapes and any missing/mismatching file fail."""
    manifest = dump_dir / "manifest.json"
    if not manifest.is_file():
        return False, [f"no manifest.json in {dump_dir}"], 0
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, ["manifest.json is not valid JSON"], 0
    if not isinstance(data, dict):
        return False, ["manifest.json is not a JSON object"], 0
    problems: list[str] = []
    entries: list[tuple[str, str]] = []
    files = data.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                entries.append((item["path"], item["sha256"]))
            else:
                problems.append("manifest files[] entry missing path/sha256")
    elif isinstance(files, dict):
        for rel, expected in files.items():
            sha = expected.get("sha256") if isinstance(expected, dict) else expected
            if isinstance(rel, str) and isinstance(sha, str):
                entries.append((rel, sha))
            else:
                problems.append(f"manifest files[{rel!r}] entry malformed")
    else:
        problems.append("manifest 'files' missing or unsupported shape (need list[{path,sha256}] or dict)")
    if not entries:
        problems.append("manifest has zero file entries (nothing would be verified)")
    verified = 0
    for rel, expected in entries:
        fpath = dump_dir / rel
        if not fpath.is_file():
            problems.append(f"missing: {rel}")
            continue
        if sha256_file(fpath) != expected:
            problems.append(f"hash mismatch: {rel}")
            continue
        verified += 1
    return (not problems), problems, verified


def _validate_run_receipt(
    rec: Any,
    label: str,
    problems: list[str],
    occupation: str | None = None,
) -> dict[str, Any]:
    """Strict live-research receipt validation. Verifies declared provenance
    fields, artifact hashes, and runtime-record cross-references."""
    if not isinstance(rec, dict):
        problems.append(f"{label}: receipt is not a JSON object")
        return {}
    if occupation is not None and str(rec.get("occupation") or "") != occupation:
        problems.append(f"{label}: receipt occupation {rec.get('occupation')!r} != expected {occupation!r}")
    if occupation is None and not str(rec.get("occupation") or "").strip():
        problems.append(f"{label}: receipt missing occupation")
    for field_name in ("provider", "model", "started_at", "finished_at"):
        if not str(rec.get(field_name) or "").strip():
            problems.append(f"{label}: missing {field_name}")
    if rec.get("model_fallback") is not False:
        problems.append(f"{label}: model_fallback must be explicit false (no substitutions)")
    ctx = str(rec.get("execution_context") or "")
    if ctx not in {"agent-runtime", "vps", "local-runtime"}:
        problems.append(
            f"{label}: execution_context must be agent-runtime/vps/local-runtime "
            f"(research must not run inside a long-held Vercel request); got {ctx!r}"
        )
    sampling = rec.get("sampling")
    if not isinstance(sampling, dict) or not sampling:
        problems.append(f"{label}: sampling scope missing")
    else:
        for field_name in ("geography", "seniority"):
            if not str(sampling.get(field_name) or "").strip():
                problems.append(f"{label}: sampling.{field_name} missing")
        if not isinstance(sampling.get("denominators"), (dict, list)):
            problems.append(f"{label}: sampling.denominators missing")
    artifacts = rec.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        problems.append(f"{label}: artifacts (named run outputs with hashes) missing")
    else:
        for name, ref in artifacts.items():
            _check_file_ref(ref, f"{label}: artifact {name}", problems)
    session_records = rec.get("session_records")
    if not isinstance(session_records, list) or not session_records:
        problems.append(f"{label}: session_records (runtime records proving provider/model) missing")
    else:
        provider = str(rec.get("provider") or "")
        model = str(rec.get("model") or "")
        expect = tuple(s for s in (provider, model) if s)
        for i, ref in enumerate(session_records):
            _verify_session_record(ref, f"{label}: session_record[{i}]", problems, expect)
    return rec


def _deploy_state(cfg: dict[str, Any]) -> tuple[str, bool]:
    base = str((cfg.get("deploy") or {}).get("preview_base_url") or "").strip()
    unauthorized = bool((cfg.get("deploy") or {}).get("unauthorized"))
    return base, unauthorized


def _resolve(base: str, endpoint: str) -> str:
    return endpoint if endpoint.startswith("http") else base.rstrip("/") + "/" + endpoint.lstrip("/")


def _get_or_fail(url: str) -> tuple[int | None, bytes | None, str | None]:
    try:
        status, body = http_get(url)
        return status, body, None
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return None, None, f"unreachable ({exc})"


def _load_release_checks(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Run the structural release checks once; None when not configured."""
    gates = cfg.get("gates") or {}
    release_dir = (
        (gates.get("hg05") or {}).get("release_dir")
        or (gates.get("hg04") or {}).get("release_dir")
        or (gates.get("hg03") or {}).get("release_dir")
        or ""
    )
    if not str(release_dir or "").strip():
        return None
    spec = importlib.util.spec_from_file_location("check_release", ACCEPTANCE_DIR / "check_release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_release", module)
    spec.loader.exec_module(module)
    min_postings = (gates.get("hg05") or {}).get("min_postings_per_occupation")
    return module.run_checks(Path(release_dir), min_postings if isinstance(min_postings, int) else None)


def _release_gate(gate: str, cfg: dict[str, Any], tag: str) -> GateResult:
    checks = _load_release_checks(cfg)
    if checks is None:
        return _unknown(gate, "release_dir not configured (gates.hg05.release_dir)")
    hard = [f["detail"] for f in checks["findings"] if tag in f["gates"]]
    soft = [f["detail"] for f in checks["advisories"] if tag in f["gates"]]
    evidence = {"release_dir": checks["release_dir"], "failures": hard, "advisories": soft}
    if hard:
        return _fail(gate, "; ".join(hard[:6]))
    if soft:
        return _unknown(gate, f"structural advisory for {tag}: " + "; ".join(soft[:6]))
    return _pass(gate, f"release structural checks for {tag} clean", evidence)


# ---------------------------------------------------------------------------
# Gate implementations
# ---------------------------------------------------------------------------


def gate_hg01(cfg: dict[str, Any]) -> GateResult:
    candidate = cfg["candidate"]
    commit_sha = str(candidate.get("commit_sha") or "")
    if not commit_sha:
        return _unknown("HG01", "candidate.commit_sha not set (slate)")
    problems: list[str] = []
    worktree = Path(candidate["worktree"])
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        return _fail("HG01", f"cannot read HEAD in worktree: {exc}")
    if head != commit_sha:
        problems.append(f"worktree HEAD {head} != configured commit {commit_sha}")

    dump_dir = Path(cfg["paths"]["dump_dir"])
    ok, probs, _n = _manifest_verify(dump_dir)
    if not ok:
        problems.append("round-zero frozen dump verification failed: " + "; ".join(probs[:4]))
    bench = dump_dir / "BENCHMARK.md"
    recorded_bench = str(candidate.get("benchmark_copy_sha256") or "")
    if not recorded_bench:
        problems.append("candidate.benchmark_copy_sha256 not set")
    elif bench.is_file() and sha256_file(bench) != recorded_bench:
        problems.append("candidate.benchmark_copy_sha256 mismatches frozen BENCHMARK.md")

    pc_dir = Path(str(cfg["paths"].get("protocol_completion_dir") or ""))
    if not pc_dir or not pc_dir.is_dir():
        problems.append("paths.protocol_completion_dir missing")
    else:
        ok2, probs2, _n2 = _manifest_verify(pc_dir)
        if not ok2:
            problems.append("protocol-completion frozen dump verification failed: " + "; ".join(probs2[:4]))
        else:
            frozen_gates = pc_dir / "acceptance" / "gates.py"
            acc_sha = str(candidate.get("acceptance_copy_sha256") or "")
            if not acc_sha:
                problems.append("candidate.acceptance_copy_sha256 not set")
            elif frozen_gates.is_file() and sha256_file(frozen_gates) != acc_sha:
                problems.append("candidate.acceptance_copy_sha256 mismatches frozen corrected gates.py")
            repo_gates = ACCEPTANCE_DIR / "gates.py"
            if frozen_gates.is_file() and repo_gates.is_file() and sha256_file(repo_gates) != sha256_file(frozen_gates):
                problems.append("working acceptance/gates.py drifted from frozen corrected copy")

    spec = cfg["gates"].get("hg01") or {}
    prov_path = str(spec.get("provenance_receipt") or "")
    if not prov_path:
        return _unknown("HG01", "gates.hg01.provenance_receipt not configured (candidate freeze file not yet authored)")
    rec = _read_json(prov_path)
    if rec is None:
        return _unknown("HG01", f"provenance receipt unreadable at {prov_path}")
    if not isinstance(rec, dict):
        problems.append("provenance receipt is not a JSON object")
        rec = {}
    if str(rec.get("candidate_commit_sha") or "") != commit_sha:
        problems.append("provenance receipt candidate_commit_sha mismatch")
    hashes = rec.get("hashes") if isinstance(rec.get("hashes"), dict) else {}
    if str(hashes.get("benchmark_copy_sha256") or "") != recorded_bench:
        problems.append("provenance receipt benchmark_copy_sha256 mismatch")
    if str(hashes.get("acceptance_copy_sha256") or "") != str(candidate.get("acceptance_copy_sha256") or ""):
        problems.append("provenance receipt acceptance_copy_sha256 mismatch")
    deployment = rec.get("deployment") if isinstance(rec.get("deployment"), dict) else {}
    if str(deployment.get("preview_base_url") or "") != str((cfg.get("deploy") or {}).get("preview_base_url") or ""):
        problems.append("provenance receipt deployment.preview_base_url mismatch with config")
    if not str(deployment.get("deployment_id") or "").strip():
        problems.append("provenance receipt missing deployment_id")
    _check_file_ref(deployment.get("deployment_receipt"), "provenance deployment_receipt", problems)
    output = rec.get("output") if isinstance(rec.get("output"), dict) else {}
    if not str(output.get("release_id") or "").strip():
        problems.append("provenance receipt missing output.release_id")
    _check_file_ref(output.get("output_receipt"), "provenance output_receipt", problems)

    if problems:
        return _fail("HG01", "; ".join(problems[:8]))
    return _pass(
        "HG01",
        "candidate commit matches frozen benchmark copy; provenance receipt verified",
        {"commit_sha": head, "dump_dir": str(dump_dir), "provenance_receipt": prov_path},
    )


def gates_hg02(cfg: dict[str, Any]) -> dict[str, GateResult]:
    runs = (cfg["gates"].get("hg02") or {}).get("runs") or []
    occ_map = {str(r.get("occupation") or ""): str(r.get("receipt_path") or "") for r in runs}
    results: dict[str, GateResult] = {}
    for occ in cfg.get("occupations") or []:
        slug = str(occ["slug"])
        gate = f"HG02-{slug}"
        receipt_path = occ_map.get(slug, "")
        if not receipt_path:
            results[gate] = _unknown(gate, f"no run receipt configured for {slug}")
            continue
        rec = _read_json(receipt_path)
        if rec is None:
            results[gate] = _fail(gate, f"run receipt unreadable at {receipt_path}")
            continue
        problems: list[str] = []
        _validate_run_receipt(rec, f"{slug} run receipt", problems, occupation=slug)
        blob = json.dumps(rec, default=str)
        if '"fixtures_used": true' in blob or '"fixture_substitution": true' in blob:
            problems.append(f"{slug}: receipt declares fixture use")
        if problems:
            results[gate] = _fail(gate, "; ".join(problems[:8]))
        else:
            results[gate] = _pass(gate, f"receipt {receipt_path} carries verified live provenance", {"receipt_path": receipt_path})
    return results


def gate_hg03(cfg: dict[str, Any]) -> GateResult:
    return _release_gate("HG03", cfg, "HG03")


def gate_hg04(cfg: dict[str, Any]) -> GateResult:
    return _release_gate("HG04", cfg, "HG04")


def gate_hg05(cfg: dict[str, Any]) -> GateResult:
    return _release_gate("HG05", cfg, "HG05")


def gate_hg06(cfg: dict[str, Any]) -> GateResult:
    return _release_gate("HG06", cfg, "HG06")


def gate_hg07(cfg: dict[str, Any]) -> GateResult:
    base, unauthorized = _deploy_state(cfg)
    spec = cfg["gates"].get("hg07") or {}
    endpoint = str(spec.get("endpoint") or "")
    if unauthorized:
        return _blocked("HG07", "deploy.unauthorized=true; deployed inspector unverifiable (authority/linkage boundary)")
    if not base or not endpoint:
        return _unknown("HG07", "deploy.preview_base_url / gates.hg07.endpoint not filled")
    url = _resolve(base, endpoint)
    status, body, err = _get_or_fail(url)
    if err or status != 200 or body is None:
        return _fail("HG07", f"{url}: HTTP {status} {err or ''}")
    text = body.decode("utf-8", "replace").lower()
    required = list(spec.get("required_sections") or DEFAULT_INSPECTOR_SECTIONS)
    allowed_missing = int(spec.get("allowed_missing") or 0)
    missing = [k for k in required if k not in text]
    evidence = {"url": url, "status": status, "bytes": len(body), "missing_sections": missing}
    if len(missing) > allowed_missing:
        return _fail("HG07", f"inspector endpoint missing required sections {missing} at {url}")
    manual_path = EVIDENCE_DIR / "HG07" / "advisor-inspection.json"
    manual = _read_json(manual_path)
    if manual is None:
        return _unknown("HG07", f"endpoint structurally OK (sections {required}); advisor inspection receipt absent: {manual_path}")
    problems: list[str] = []
    if not isinstance(manual, dict):
        problems.append("advisor inspection receipt is not a JSON object")
        manual = {}
    if str(manual.get("inspected_url") or "") != url:
        problems.append("advisor inspection inspected_url != probed endpoint URL")
    if str(manual.get("verdict") or "").upper() != "PASS":
        problems.append(f"advisor inspection verdict not PASS: {manual.get('verdict')!r}")
    checked = manual.get("sections_checked")
    if not isinstance(checked, list) or not set(required) <= {str(c) for c in checked}:
        problems.append("advisor inspection did not confirm all required inspector sections")
    for i, art in enumerate(manual.get("artifacts") or []):
        _check_file_ref(art, f"HG07 advisor artifact[{i}]", problems)
    if problems:
        return _fail("HG07", "; ".join(problems[:6]))
    return _pass("HG07", "deployed inspector exposes publishable supporting data; advisor inspected", evidence)


def gate_hg08(cfg: dict[str, Any]) -> GateResult:
    base, unauthorized = _deploy_state(cfg)
    spec = cfg["gates"].get("hg08") or {}
    endpoints = spec.get("endpoints") or []
    if unauthorized:
        return _blocked("HG08", "deploy.unauthorized=true; deployed API unverifiable (authority/linkage boundary)")
    if not base or not endpoints:
        return _unknown("HG08", "deploy.preview_base_url / gates.hg08.endpoints not filled")
    min_chars = int(spec.get("min_content_chars") or 0)
    evidence: dict[str, Any] = {}
    failures: list[str] = []
    for i, entry in enumerate(endpoints):
        if isinstance(entry, str):
            entry = {"path": entry}
        path = str(entry.get("path") or "")
        url = _resolve(base, path)
        kind = str(entry.get("kind") or "json")
        expect_status = int(entry.get("expect_status") or 200)
        must_contain = [str(m).lower() for m in (entry.get("must_contain") or [])]
        must_not_contain = [str(m).lower() for m in (entry.get("must_not_contain") or [])]
        status, body, err = _get_or_fail(url)
        if err or body is None:
            failures.append(f"{url}: {err}")
            continue
        text = body.decode("utf-8", "replace")
        text_l = text.lower()
        rec = {"url": url, "status": status, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        evidence[f"endpoint[{i}]"] = rec
        if status != expect_status:
            failures.append(f"{url}: HTTP {status}, expected {expect_status}")
            continue
        if min_chars and len(text) < min_chars:
            failures.append(f"{url}: {len(text)} chars < min_content_chars {min_chars}")
            continue
        if kind == "json":
            try:
                json.loads(text)
            except json.JSONDecodeError:
                failures.append(f"{url}: body is not valid JSON (kind=json)")
                continue
        missed = [m for m in must_contain if m not in text_l]
        if missed:
            failures.append(f"{url}: required markers absent: {missed}")
            continue
        banned = [m for m in must_not_contain if m in text_l]
        if banned:
            failures.append(f"{url}: forbidden markers present: {banned}")
    if failures:
        return _fail("HG08", "; ".join(failures[:8]))
    return _pass("HG08", f"{len(endpoints)} endpoint(s) answered with verified live content", evidence)


def gate_hg09(cfg: dict[str, Any]) -> GateResult:
    spec = cfg["gates"].get("hg09") or {}
    receipt_path = str(spec.get("receipt_path") or EVIDENCE_DIR / "HG09" / "independent-consumption-receipt.json")
    rec = _read_json(receipt_path)
    if rec is None:
        return _unknown("HG09", f"no independent-agent receipt at {receipt_path}")
    problems: list[str] = []
    if not isinstance(rec, dict):
        problems.append("receipt is not a JSON object")
        rec = {}
    consumer = rec.get("consumer") if isinstance(rec.get("consumer"), dict) else {}
    identity = str(consumer.get("identity") or "")
    if not identity:
        problems.append("consumer.identity missing (independent agent context not named)")
    if str(consumer.get("kind") or "") != "agent":
        problems.append("consumer.kind must be 'agent' (independent agent context)")
    excluded = [str(e) for e in (spec.get("excluded_identities") or [])]
    for e in excluded:
        if e and e in identity:
            problems.append(f"consumer identity {identity!r} matches excluded context {e!r} (not independent)")
    _verify_session_record(consumer.get("session_record"), "consumer session record", problems, (identity,) if identity else ())
    cli = rec.get("cli") if isinstance(rec.get("cli"), dict) else {}
    if str(cli.get("command") or "").strip() == "":
        problems.append("cli.command missing")
    if cli.get("exit_code") != 0:
        problems.append(f"cli exit_code not 0: {cli.get('exit_code')!r}")
    cli_out = cli.get("output")
    _check_file_ref(cli_out, "cli output", problems)
    if isinstance(cli_out, dict) and Path(str(cli_out.get("path") or "")).is_file():
        try:
            json.loads(Path(str(cli_out["path"])).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append("cli output is not valid JSON (not machine-consumable)")
    mcp = rec.get("mcp") if isinstance(rec.get("mcp"), dict) else {}
    tools = mcp.get("tools") if isinstance(mcp.get("tools"), list) else []
    if not tools:
        problems.append("mcp.tools missing (no MCP tool consumption recorded)")
    for i, call in enumerate(tools):
        if not isinstance(call, dict) or not str(call.get("tool") or "").strip():
            problems.append(f"mcp.tools[{i}]: tool name missing")
            continue
        if call.get("exit_code") != 0:
            problems.append(f"mcp.tools[{i}] ({call.get('tool')}): exit_code not 0")
        _check_file_ref(call.get("result"), f"mcp.tools[{i}] ({call.get('tool')}) result", problems)
        res = call.get("result")
        if isinstance(res, dict) and Path(str(res.get("path") or "")).is_file():
            try:
                json.loads(Path(str(res["path"])).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                problems.append(f"mcp.tools[{i}] result is not valid JSON")
    if str(rec.get("verdict") or "").upper() != "PASS":
        problems.append(f"receipt verdict not PASS: {rec.get('verdict')!r}")
    if problems:
        return _fail("HG09", "; ".join(problems[:8]))
    return _pass("HG09", "independent agent receipt records verified CLI+MCP consumption", {"receipt_path": receipt_path})


def gate_hg10(cfg: dict[str, Any]) -> GateResult:
    base, unauthorized = _deploy_state(cfg)
    spec = cfg["gates"].get("hg10") or {}
    journeys = spec.get("journeys") or []
    if unauthorized:
        return _blocked("HG10", "deploy.unauthorized=true; browser journeys cannot run against a nonexistent preview")
    if not base or not journeys:
        return _unknown("HG10", "deploy.preview_base_url / gates.hg10.journeys not filled")
    if len(journeys) < 4:
        return _fail("HG10", f"benchmark requires four journeys; configured {len(journeys)}")
    receipts_path = Path(str(spec.get("receipts_path") or EVIDENCE_DIR / "HG10" / "journey-receipts.json"))
    raw = _read_json(receipts_path)
    if raw is None:
        return _unknown(
            "HG10",
            f"no journey receipts at {receipts_path}; run "
            "`python3 acceptance/journeys.py --config acceptance/config.json` against the deployed URL",
        )
    if isinstance(raw, dict) and raw.get("verdict") == "UNKNOWN" and not raw.get("journeys"):
        return _unknown("HG10", f"journey driver could not run: {raw.get('detail', 'no driver')}")
    receipts = raw if isinstance(raw, list) else []
    by_name = {str(r.get("journey") or ""): r for r in receipts if isinstance(r, dict)}
    base_norm = base.rstrip("/")
    problems: list[str] = []
    for j in journeys:
        name = str(j.get("name") or "")
        rec = by_name.get(name)
        if rec is None:
            problems.append(f"journey {name}: no recorded receipt")
            continue
        if str(rec.get("base_url") or "").rstrip("/") != base_norm:
            problems.append(f"journey {name}: recorded base_url {rec.get('base_url')!r} != deployed {base_norm!r} (stale/other-surface evidence)")
            continue
        if rec.get("verdict") != "PASS":
            problems.append(f"journey {name}: driver verdict {rec.get('verdict')!r}: {'; '.join((rec.get('failures') or [])[:3])}")
            continue
        steps = rec.get("steps") if isinstance(rec.get("steps"), list) else []
        stages = [str(s.get("stage") or "") for s in steps if isinstance(s, dict)]
        pos = 0
        for required_stage in JOURNEY_STAGES_REQUIRED:
            if required_stage in stages[pos:]:
                pos = stages.index(required_stage, pos) + 1
            else:
                problems.append(f"journey {name}: journey chain missing/unordered stage {required_stage!r} (have {stages})")
                break
        for i, s in enumerate(steps):
            art = s.get("artifact")
            if art:
                _check_file_ref(art, f"journey {name} step[{i}] artifact", problems)
                p = Path(str(art.get("path") or ""))
                if p.is_file():
                    if p.stat().st_size < 1024:
                        problems.append(f"journey {name} step[{i}]: screenshot suspiciously small ({p.stat().st_size} bytes)")
                    if p.suffix == ".png" and p.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                        problems.append(f"journey {name} step[{i}]: artifact is not a valid PNG")
        har = rec.get("har")
        if har:
            _check_file_ref(har, f"journey {name} har", problems)
            hp = Path(str(har.get("path") or ""))
            if hp.is_file():
                try:
                    json.loads(hp.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    problems.append(f"journey {name}: HAR file is not valid JSON")
        for s in steps:
            if str(s.get("type") or "") == "source":
                src_host = (s.get("url_after") or "")
                if src_host and urlparse_host(src_host) == urlparse_host(base_norm):
                    problems.append(f"journey {name}: source stage URL stayed on deployed host")
    manual_path = EVIDENCE_DIR / "HG10" / "advisor-inspection.json"
    manual = _read_json(manual_path)
    if problems:
        return _fail("HG10", "; ".join(problems[:8]))
    if manual is None:
        return _unknown(
            "HG10",
            f"journey receipts/artifacts structurally verified so far; advisor browser inspection receipt absent: {manual_path}",
        )
    if not isinstance(manual, dict):
        problems.append("HG10 advisor inspection receipt is not a JSON object")
        manual = {}
    if str(manual.get("inspected_url") or "").rstrip("/") != base_norm:
        problems.append("HG10 advisor inspection inspected_url != deployed base URL")
    if str(manual.get("verdict") or "").upper() != "PASS":
        problems.append(f"HG10 advisor inspection verdict not PASS: {manual.get('verdict')!r}")
    checked_journeys = {str(c) for c in (manual.get("journeys_checked") or [])}
    if not {str(j.get("name")) for j in journeys} <= checked_journeys:
        problems.append("HG10 advisor inspection did not cover every configured journey")
    for i, art in enumerate(manual.get("artifacts") or []):
        _check_file_ref(art, f"HG10 advisor artifact[{i}]", problems)
    if problems:
        return _fail("HG10", "; ".join(problems[:8]))
    return _pass("HG10", "four deployed browser journeys recorded, verified, advisor-inspected", {"receipts_path": str(receipts_path)})


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


def gate_hg11(cfg: dict[str, Any]) -> GateResult:
    spec = cfg["gates"].get("hg11") or {}
    receipt_path = str(spec.get("receipt_path") or "")
    cited_claim_url = str(spec.get("cited_claim_url") or "")
    pre_sha = str(spec.get("pre_update_content_sha256") or "")
    if not receipt_path or not cited_claim_url or not pre_sha:
        missing = [k for k, v in (("receipt_path", receipt_path), ("cited_claim_url", cited_claim_url), ("pre_update_content_sha256", pre_sha)) if not v]
        return _unknown("HG11", f"second-update evidence not configured: missing {missing}")
    rec = _read_json(receipt_path)
    if rec is None:
        return _fail("HG11", f"second-update receipt unreadable at {receipt_path}")
    problems: list[str] = []
    rec = _validate_run_receipt(rec, "second-update run receipt", problems)
    first_id = str(rec.get("first_release_id") or "") if isinstance(rec, dict) else ""
    second_id = str(rec.get("second_release_id") or "") if isinstance(rec, dict) else ""
    if not first_id or not second_id:
        problems.append("second-update receipt missing first_release_id/second_release_id")
    elif first_id == second_id:
        problems.append("first_release_id == second_release_id (no second update happened)")
    try:
        status, body = http_get(cited_claim_url)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return _fail("HG11", f"cited claim URL not dereferenceable after update: {exc}")
    if status != 200:
        return _fail("HG11", f"cited claim URL returned HTTP {status}")
    actual = hashlib.sha256(body).hexdigest()
    if actual != pre_sha:
        return _fail(
            "HG11",
            f"cited claim content changed across the second update (pre-recorded {pre_sha[:12]}, live {actual[:12]}) — citation does not survive",
        )
    expected_id = spec.get("expected_claim_id")
    if expected_id and str(expected_id) not in body.decode("utf-8", "replace"):
        return _fail("HG11", f"cited claim id {expected_id!r} absent from live body")
    if problems:
        return _fail("HG11", "; ".join(problems[:8]))
    return _pass(
        "HG11",
        "citation survives second update: stable URL dereferences with byte-identical claim content",
        {"url": cited_claim_url, "sha256": actual},
    )


def gate_hg12(cfg: dict[str, Any]) -> GateResult:
    base, unauthorized = _deploy_state(cfg)
    spec = cfg["gates"].get("hg12") or {}
    mutations = spec.get("mutation_endpoints") or []
    negatives = spec.get("validation_negatives") or []
    max_payload = spec.get("max_payload_bytes")
    if unauthorized:
        return _blocked("HG12", "deploy.unauthorized=true; deployed mutation surface untestable")
    if not base or not mutations:
        return _unknown("HG12", "deploy.preview_base_url / gates.hg12.mutation_endpoints not filled")
    evidence: dict[str, Any] = {}
    failures: list[str] = []
    for i, m in enumerate(mutations):
        endpoint = m if isinstance(m, str) else str((m or {}).get("path") or "")
        if not endpoint:
            failures.append(f"mutation[{i}]: empty endpoint")
            continue
        url = _resolve(base, endpoint)
        try:
            status, _body = http_post_json(url, b'{"op":"declare-pass"}')
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            failures.append(f"{url}: unreachable ({exc})")
            continue
        evidence[f"mutation[{i}]"] = {"url": url, "status": status}
        if status < 400:
            failures.append(f"{url}: unauthenticated mutation ACCEPTED (HTTP {status}) — endpoint not protected")
    for i, neg in enumerate(negatives):
        if not isinstance(neg, dict) or not str(neg.get("endpoint") or ""):
            failures.append(f"validation_negative[{i}]: missing endpoint")
            continue
        url = _resolve(base, str(neg["endpoint"]))
        payload: bytes
        if neg.get("payload_over_bytes") is not None and max_payload is not None:
            n = int(neg["payload_over_bytes"])
            payload = json.dumps({"pad": "x" * (max_payload + n)}).encode("utf-8")
        else:
            payload = str(neg.get("payload") or "{}").encode("utf-8")
        expect_status = int(neg.get("expect_status") or 400)
        try:
            status, _body = http_post_json(url, payload)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            failures.append(f"{url}: unreachable ({exc})")
            continue
        evidence[f"validation_negative[{i}]"] = {"url": url, "status": status, "payload_bytes": len(payload)}
        if status < 400:
            failures.append(f"{url}: invalid/oversized payload accepted (HTTP {status}, expected >=400)")
    runs = (cfg["gates"].get("hg02") or {}).get("runs") or []
    for r in runs:
        rec = _read_json(str(r.get("receipt_path") or ""))
        if isinstance(rec, dict) and str(rec.get("execution_context") or "") == "vercel-request":
            failures.append(f"{r.get('occupation')}: research receipt declares execution_context 'vercel-request' (long-held request)")
    if failures:
        return _fail("HG12", "; ".join(failures[:8]))
    return _pass("HG12", "mutations rejected, payload validation/bounds enforced live", evidence)


def gate_hg13(cfg: dict[str, Any]) -> GateResult:
    base, unauthorized = _deploy_state(cfg)
    spec = cfg["gates"].get("hg13") or {}
    if unauthorized:
        return _blocked("HG13", "deploy.unauthorized=true; deployed honesty suite untestable")
    problems: list[str] = []
    checked: list[str] = []
    for scenario in ("unsupported", "insufficient"):
        s = spec.get(scenario) if isinstance(spec.get(scenario), dict) else {}
        path = str(s.get("path") or "")
        if not path:
            return _unknown("HG13", f"{scenario} scenario not configured (need gates.hg13.{scenario}.path)")
        url = _resolve(base, path)
        try:
            status, body = http_get(url)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            problems.append(f"{scenario}: {url} unreachable ({exc})")
            continue
        text = body.decode("utf-8", "replace").lower()
        markers = [str(m).lower() for m in (s.get("honest_markers") or [])]
        if status != 200:
            problems.append(f"{scenario}: {url} returned HTTP {status}")
        elif markers and not any(m in text for m in markers):
            problems.append(f"{scenario}: {url} lacks honest markers {markers}")
        if "fixture" in text:
            problems.append(f"{scenario}: {url} body contains fixture content (silent fallback)")
        else:
            checked.append(f"{scenario}:HTTP{status}")
    for scenario in ("retrieval_failure", "outage"):
        s = spec.get(scenario) if isinstance(spec.get(scenario), dict) else {}
        receipt_path = str(s.get("receipt_path") or "")
        if not receipt_path:
            return _unknown("HG13", f"{scenario} scenario not configured (need gates.hg13.{scenario}.receipt_path)")
        rec = _read_json(receipt_path)
        if rec is None:
            problems.append(f"{scenario}: scenario receipt unreadable at {receipt_path}")
            continue
        if not isinstance(rec, dict):
            problems.append(f"{scenario}: receipt is not a JSON object")
            continue
        observed = rec.get("observed") if isinstance(rec.get("observed"), dict) else {}
        if not observed:
            problems.append(f"{scenario}: receipt missing observed evidence")
        _check_file_ref(observed.get("body"), f"{scenario} observed.body", problems)
        if observed.get("fixture_used") is not False:
            problems.append(f"{scenario}: observed.fixture_used must be explicit false (no fixture substitution)")
        if rec.get("honest") is not True:
            problems.append(f"{scenario}: receipt.honest must be explicit true")
        must_contain = [str(m).lower() for m in (s.get("must_contain") or [])]
        body_ref = observed.get("body")
        if isinstance(body_ref, dict) and Path(str(body_ref.get("path") or "")).is_file() and must_contain:
            text = Path(str(body_ref["path"])).read_text(encoding="utf-8", errors="replace").lower()
            missed = [m for m in must_contain if m not in text]
            if missed:
                problems.append(f"{scenario}: failure body lacks explicit-failure markers {missed}")
        checked.append(f"{scenario}: receipt verified")
    last_good = spec.get("last_good") if isinstance(spec.get("last_good"), dict) else {}
    lg_url = str(last_good.get("url") or "")
    lg_sha = str(last_good.get("content_sha256") or "")
    if lg_url and lg_sha:
        try:
            status, body = http_get(lg_url)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            problems.append(f"last_good: {lg_url} unreachable ({exc})")
        else:
            actual = hashlib.sha256(body).hexdigest()
            if status != 200:
                problems.append(f"last_good: {lg_url} returned HTTP {status} after failure (last good release not served)")
            elif actual != lg_sha:
                problems.append(f"last_good: {lg_url} content changed after failure (recorded {lg_sha[:12]}, live {actual[:12]})")
            else:
                checked.append("last_good: verified")
    else:
        return _unknown("HG13", "gates.hg13.last_good {url, content_sha256} not configured")
    if problems:
        return _fail("HG13", "; ".join(problems[:8]))
    return _pass("HG13", "unsupported/insufficient probed live; retrieval-failure and outage receipts verified; last-good preserved", {"checks": checked})


def gate_hg14(cfg: dict[str, Any]) -> GateResult:
    worktree = Path(cfg["candidate"]["worktree"])
    ignore_names = {"node_modules", ".venv", ".vercel", ".git", "__pycache__", ".poc-env", ".vercel-out", ".ruff_cache", ".mypy_cache"}
    findings: list[str] = []
    scanned = 0
    for path in worktree.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignore_names for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        scanned += 1
        if not source_has_no_secrets(text):
            findings.append(str(path))
    if findings:
        return _fail("HG14", f"potential secret material in: {findings[:5]}")
    problems: list[str] = []
    prod_patterns = [str(p) for p in ((cfg["gates"].get("hg14") or {}).get("production_host_patterns") or [])]
    base, _unauth = _deploy_state(cfg)
    if base and prod_patterns and any(re.search(p, urlparse_host(base)) for p in prod_patterns if p):
        problems.append(f"deploy.preview_base_url host matches a production pattern: {base}")
    mutation_receipts = [
        EVIDENCE_DIR / "HG12" / "receipt.json",
    ]
    for mr in mutation_receipts:
        rec = _read_json(mr)
        if not isinstance(rec, dict):
            continue
        for entry in (rec.get("evidence") or {}).values() if isinstance(rec.get("evidence"), dict) else []:
            url = str(entry.get("url") or "") if isinstance(entry, dict) else ""
            if url and prod_patterns and any(re.search(p, urlparse_host(url)) for p in prod_patterns if p):
                problems.append(f"mutation traffic against production-pattern host: {url}")
    attestation_path = EVIDENCE_DIR / "HG14" / "no-production-mutation.json"
    att = _read_json(attestation_path)
    if att is None:
        if problems:
            return _fail("HG14", "; ".join(problems[:6]))
        return _unknown("HG14", f"heuristic scan clean ({scanned} files); no-production-mutation attestation absent: {attestation_path}")
    if not isinstance(att, dict) or att.get("production_mutations_performed") != 0:
        problems.append("no-production-mutation attestation missing or declares mutations")
    if problems:
        return _fail("HG14", "; ".join(problems[:6]))
    return _pass("HG14", f"no secret-shaped material in {scanned} scanned files; production mutation attestation clean")


def gate_hg15(cfg: dict[str, Any]) -> GateResult:
    base, unauthorized = _deploy_state(cfg)
    if unauthorized:
        return _blocked("HG15", "deploy.unauthorized=true; deployed a11y/fidelity unverifiable")
    if not base:
        return _unknown("HG15", "deploy.preview_base_url not set; deployed surface unverifiable")
    spec = cfg["gates"].get("hg15") or {}
    static_url = str(spec.get("static_url") or base)
    url = _resolve(base, static_url) if not static_url.startswith("http") else static_url
    status, body, err = _get_or_fail(url)
    if err or status != 200 or body is None:
        return _fail("HG15", f"{url}: HTTP {status} {err or ''}")
    html = body.decode("utf-8", "replace")
    markers = [str(m) for m in (spec.get("required_html_markers") or ["<html lang", "skip"])]
    missed = [m for m in markers if m.lower() not in html.lower()]
    if missed:
        return _fail("HG15", f"deployed HTML missing static a11y markers: {missed}")
    manual_path = EVIDENCE_DIR / "HG15" / "advisor-manual-verdict.json"
    manual = _read_json(manual_path)
    if manual is None:
        return _unknown("HG15", f"static markers OK on deployed HTML; advisor manual a11y/fidelity verdict absent: {manual_path}")
    problems: list[str] = []
    if not isinstance(manual, dict):
        problems.append("advisor manual verdict is not a JSON object")
        manual = {}
    if str(manual.get("inspected_url") or "").rstrip("/") != base.rstrip("/"):
        problems.append("advisor manual verdict inspected_url != deployed base URL")
    if str(manual.get("verdict") or "").upper() != "PASS":
        problems.append(f"advisor manual verdict not PASS: {manual.get('verdict')!r}")
    dimensions = manual.get("dimensions") if isinstance(manual.get("dimensions"), dict) else {}
    for dim in ("keyboard_navigation", "visible_focus", "reduced_motion", "responsive_behavior", "skip_link", "design_fidelity"):
        d = dimensions.get(dim)
        if not isinstance(d, dict):
            problems.append(f"advisor manual verdict missing dimension {dim}")
            continue
        if d.get("checked") is not True:
            problems.append(f"advisor manual verdict dimension {dim} not checked")
        elif d.get("passed") is not True:
            problems.append(f"advisor manual verdict dimension {dim} failed: {d.get('notes', '')}")
    artifacts = manual.get("recording_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        problems.append("advisor manual verdict has no recording artifacts")
    else:
        for i, art in enumerate(artifacts):
            _check_file_ref(art, f"HG15 recording artifact[{i}]", problems)
    if problems:
        return _fail("HG15", "; ".join(problems[:8]))
    return _pass("HG15", "static markers + advisor manual a11y/fidelity verdict with recordings verified", {"url": url})


def gate_hg16(cfg: dict[str, Any]) -> GateResult:
    spec = cfg["gates"].get("hg16") or {}
    venv_python = str((cfg.get("paths") or {}).get("venv_python") or "")
    if not venv_python:
        return _unknown("HG16", "paths.venv_python not set; baseline suite needs an isolated env (uv venv .venv && uv pip install -e .)")
    python = venv_python if Path(venv_python).is_file() else None
    if python is None:
        return _fail("HG16", f"paths.venv_python does not exist: {venv_python}")
    worktree = Path(cfg["candidate"]["worktree"])
    cmd = [python, "-m", "unittest", "discover", "-s", "tests", "-v"]
    timeout = int(spec.get("timeout_seconds") or 900)
    import time as _time

    t0 = _time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail("HG16", f"baseline suite timed out after {timeout}s")
    except OSError as exc:
        return _fail("HG16", f"cannot execute baseline suite: {exc}")
    duration = round(_time.monotonic() - t0, 2)
    output = (proc.stdout or "") + (proc.stderr or "")
    m_run = re.search(r"Ran (\d+) tests? in", output)
    m_failed = re.search(r"FAILED \(failures=(\d+)(?:, errors=(\d+))?\)", output)
    m_errors_only = re.search(r"FAILED \((?:\S+ )?errors=(\d+)\)", output)
    receipt = {
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "tests_run": int(m_run.group(1)) if m_run else None,
        "failures": int(m_failed.group(1)) if m_failed else (int(m_errors_only.group(1)) if m_errors_only else 0),
        "duration_s": duration,
        "output_tail": output[-4000:],
        "ran_at": datetime.now(UTC).isoformat(),
    }
    out_dir = EVIDENCE_DIR / "HG16"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if proc.returncode != 0:
        return _fail("HG16", f"baseline tests did not pass: exit {proc.returncode}")
    minimum = spec.get("minimum_tests")
    allow_removed = int(spec.get("allow_removed_tests") or 0)
    removed = spec.get("removed_incidental_tests") or []
    if isinstance(minimum, int) and receipt["tests_run"] is not None:
        floor = minimum - allow_removed
        if receipt["tests_run"] < floor:
            return _fail(
                "HG16",
                f"behavioral test coverage reduced: {receipt['tests_run']} tests < floor {floor} "
                f"(previous {minimum}, allow_removed {allow_removed})",
            )
    if allow_removed > 0:
        if not isinstance(removed, list) or len(removed) != allow_removed:
            return _fail("HG16", f"allow_removed_tests={allow_removed} but removed_incidental_tests lists {len(removed) if isinstance(removed, list) else '?'} entries")
        for i, item in enumerate(removed):
            if not isinstance(item, dict) or not str(item.get("path") or "") or not str(item.get("reason") or ""):
                return _fail("HG16", f"removed_incidental_tests[{i}] needs path + reason (developer-rule category)")
    return _pass("HG16", f"baseline unittest suite passed on candidate ({receipt['tests_run']} tests)", {"receipt": receipt})


def gate_hg17(cfg: dict[str, Any]) -> GateResult:
    spec = cfg["gates"].get("hg17") or {}
    verdict_paths = [str(p) for p in (spec.get("verdict_artifact_paths") or [])]
    if not verdict_paths:
        return _unknown("HG17", "advisor verdict artifact not registered in config")
    problems: list[str] = []
    advisor_pin = str((cfg.get("review") or {}).get("advisor_pin") or "")
    fresh_ids = [str(s) for s in ((cfg.get("review") or {}).get("fresh_context_session_ids") or [])]
    if not fresh_ids:
        problems.append("review.fresh_context_session_ids empty (fresh pinned advisor context not recorded)")
    for p in verdict_paths:
        rec = _read_json(p)
        if rec is None:
            problems.append(f"verdict artifact unreadable at {p}")
            continue
        if not isinstance(rec, dict):
            problems.append(f"verdict artifact at {p} is not a JSON object")
            continue
        if str(rec.get("role") or "") != "advisor":
            problems.append(f"verdict artifact at {p}: role must be 'advisor'")
        if advisor_pin and str(rec.get("advisor_pin") or "") != advisor_pin:
            problems.append(f"verdict artifact at {p}: advisor_pin mismatch with config")
        cand_sha = str((cfg.get("candidate") or {}).get("commit_sha") or "")
        if cand_sha and str(rec.get("candidate_commit_sha") or "") != cand_sha:
            problems.append(f"verdict artifact at {p}: candidate_commit_sha mismatch")
        categories = rec.get("categories") if isinstance(rec.get("categories"), dict) else {}
        missing_cats = [c for c in REQUIRED_CATEGORIES if c not in categories]
        if missing_cats:
            problems.append(f"verdict artifact at {p}: missing per-category scores for {missing_cats}")
        else:
            for c, score in categories.items():
                if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 10:
                    problems.append(f"verdict artifact at {p}: category {c} score invalid: {score!r}")
        commands = rec.get("executed_commands")
        if not isinstance(commands, list) or not commands:
            problems.append(f"verdict artifact at {p}: no executed commands recorded")
        else:
            for i, c in enumerate(commands):
                if not isinstance(c, dict) or not str(c.get("command") or "") or not isinstance(c.get("exit_code"), int):
                    problems.append(f"verdict artifact at {p}: executed_commands[{i}] needs command + exit_code")
        browser_arts = rec.get("browser_output_artifacts")
        if not isinstance(browser_arts, list) or not browser_arts:
            problems.append(f"verdict artifact at {p}: no inspected browser output artifacts recorded")
        else:
            for i, art in enumerate(browser_arts):
                _check_file_ref(art, f"verdict {p} browser artifact[{i}]", problems)
        session_records = rec.get("session_records")
        if not isinstance(session_records, list) or not session_records:
            problems.append(f"verdict artifact at {p}: no advisor runtime records (model pinning proof)")
        else:
            expect: tuple[str, ...] = ()
            expect_re: tuple[str, ...] = ()
            if advisor_pin:
                model_part, sep, thinking = advisor_pin.partition(":")
                expect = (model_part,)
                if sep and thinking:
                    expect_re = (rf'\"thinkingLevel\"\s*:\s*\"{re.escape(thinking)}\"',)
            for i, ref in enumerate(session_records):
                _verify_session_record(ref, f"verdict {p} session_record[{i}]", problems, expect, expect_re)
    if problems:
        return _fail("HG17", "; ".join(problems[:8]))
    return _pass("HG17", "advisor verdict artifact present and structurally complete", {"paths": verdict_paths})


def gate_hg18(cfg: dict[str, Any]) -> GateResult:
    dump_dir = Path(cfg["paths"]["dump_dir"])
    if not dump_dir.is_dir():
        return _unknown("HG18", f"external dump dir missing: {dump_dir}")
    ok, problems, n = _manifest_verify(dump_dir)
    if not ok:
        return _fail("HG18", "; ".join(problems[:6]) or "dump manifest verification failed")
    if n == 0:
        return _fail("HG18", "dump manifest has zero entries (nothing hash-pinned)")
    manifest = json.loads((dump_dir / "manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    entries = {str(e.get("path")): str(e.get("sha256")) for e in files if isinstance(e, dict)} if isinstance(files, list) else {}
    recorded_bench = str((cfg.get("candidate") or {}).get("benchmark_copy_sha256") or "")
    bench_entry = entries.get("BENCHMARK.md")
    if recorded_bench and bench_entry and bench_entry != recorded_bench:
        return _fail("HG18", "dump BENCHMARK.md hash does not match the frozen benchmark copy")
    required_substrings = [str(s) for s in ((cfg.get("gates") or {}).get("hg18") or {}).get("required_dump_entries") or ["provenance"]]
    entry_text = " ".join(entries.keys())
    missing = [s for s in required_substrings if s not in entry_text]
    if missing:
        return _unknown("HG18", f"dump verified ({n} files) but verdict artifacts not yet frozen: entries matching {missing} absent")
    return _pass("HG18", f"external dump hash manifest verifies ({n} files) and matches frozen benchmark", {"dump_dir": str(dump_dir), "files": n})


# ---------------------------------------------------------------------------
# Receipt persistence + CLI
# ---------------------------------------------------------------------------


def run_gate(gate: str, cfg: dict[str, Any]) -> GateResult | dict[str, GateResult]:
    implementations: dict[str, Callable[[dict[str, Any]], GateResult]] = {
        "HG01": gate_hg01,
        "HG03": gate_hg03,
        "HG04": gate_hg04,
        "HG05": gate_hg05,
        "HG06": gate_hg06,
        "HG07": gate_hg07,
        "HG08": gate_hg08,
        "HG09": gate_hg09,
        "HG10": gate_hg10,
        "HG11": gate_hg11,
        "HG12": gate_hg12,
        "HG13": gate_hg13,
        "HG14": gate_hg14,
        "HG15": gate_hg15,
        "HG16": gate_hg16,
        "HG17": gate_hg17,
        "HG18": gate_hg18,
    }
    if gate == "HG02":
        results = gates_hg02(cfg)
        details = []
        combined = "PASS"
        for g, r in sorted(results.items()):
            write_receipt(g, r)
            details.append(f"{g}={r.verdict}")
            if r.verdict == "UNKNOWN" and combined == "PASS":
                combined = "UNKNOWN"
            if r.verdict in {"FAIL", "BLOCKED"}:
                combined = "FAIL"
        final = GateResult(gate="HG02", verdict=combined, detail="; ".join(details))
        write_receipt("HG02", final)
        return final
    fn = implementations.get(gate)
    if fn is None:
        raise ConfigError(f"unknown gate {gate}")
    result = fn(cfg)
    write_receipt(gate, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["gate", "all", "summary"])
    parser.add_argument("gate", nargs="?", default=None, help="Gate id like HG01 (command=gate)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"CONFIG-ERROR: {exc}", file=sys.stderr)
        return 2
    if args.command == "summary":
        missing: list[str] = []
        for g in GATE_IDS:
            receipt = EVIDENCE_DIR / g / "receipt.json"
            rec = _read_json(receipt) if receipt.exists() else None
            if isinstance(rec, dict) and rec.get("verdict"):
                print(f"{g}: {rec['verdict']}")
            else:
                missing.append(g)
        if missing:
            print("UNKNOWN (no receipt): " + ", ".join(missing))
        return 0
    if args.command == "gate":
        if not args.gate:
            parser.error("command 'gate' requires a gate id")
        try:
            results = [run_gate(args.gate.upper(), cfg)]
        except ConfigError as exc:
            print(f"CONFIG-ERROR: {exc}", file=sys.stderr)
            return 2
    else:
        results = []
        for g in GATE_IDS:
            try:
                results.append(run_gate(g, cfg))
            except ConfigError as exc:
                results.append(_unknown(g, f"not executable here: {exc}"))
                write_receipt(g, results[-1])
    exit_code = 0
    saw_unknown = False
    for r in results:
        rs = [r] if isinstance(r, GateResult) else list(r.values())
        for res in rs:
            print(f"{res.gate}: {res.verdict}{' — ' + res.detail if res.detail and not args.quiet else ''}")
            if res.verdict in {"FAIL", "BLOCKED"}:
                exit_code = 1
            elif res.verdict == "UNKNOWN":
                saw_unknown = True
    if exit_code == 0 and saw_unknown:
        exit_code = 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
