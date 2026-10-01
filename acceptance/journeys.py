#!/usr/bin/env python3
"""Deployed browser journey driver (HG10 evidence collector).

Executes the configured browser journeys (browse → query → finding → inspect
evidence → original source) against the deployed preview URL with a real
browser and records per-step artifacts (screenshots, HAR) plus a
machine-readable receipt with sha256 per artifact. Fail-closed: without a
browser driver it writes verdict UNKNOWN and names the exact prerequisite;
it never fabricates journey evidence.

Usage:
    python3 acceptance/journeys.py --config acceptance/config.json \
        [--base https://<preview-url>] \
        [--out acceptance/evidence/HG10] \
        [--journey NAME ...] [--timeout-ms 30000]

Exit codes: 0 all journeys PASS · 1 journey/step failure · 2 config error ·
3 no usable browser driver (prerequisite named in receipt/stderr).

Journey config (gates.hg10.journeys[]):
    {"name": "...", "base_url_marker"?: "...", "steps": [...]}
Each step: {"type": "goto"|"click"|"fill"|"press"|"wait"|"assert_text"|"source",
            "url"?, "selector"?, "value"?, "stage"?: "browse"|"query"|"finding"|
            "inspect"|"source", "timeout_ms"?, "artifact"? (default true for
            goto/click/assert_text/source)}
"source" behaves like goto but must land outside the deployed host (the
original public source) unless the base URL itself is local (smoke).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ACCEPTANCE_DIR = Path(__file__).resolve().parent
STAGES_REQUIRED = ("browse", "query", "finding", "inspect", "source")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _resolve_url(base: str, step: dict[str, Any]) -> str:
    url = str(step.get("url") or "")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


def _driver_python():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return None
    return "python-playwright"


def _driver_venv(venv_python: str | None):
    if not venv_python:
        return None
    code = "import playwright, sys; print(playwright.__name__)"
    import subprocess
    try:
        proc = subprocess.run([venv_python, "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return "python-playwright-in-venv" if proc.returncode == 0 else None


def _driver_node():
    import subprocess
    try:
        proc = subprocess.run(["node", "-e", "require('playwright');"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return "node-playwright" if proc.returncode == 0 else None


def detect_driver(cfg: dict[str, Any]) -> str | None:
    venv_python = (cfg.get("paths") or {}).get("venv_python")
    return _driver_python() or _driver_venv(venv_python) or _driver_node()


def _run_python_playwright(
    journeys: list[dict[str, Any]],
    base: str,
    out_dir: Path,
    timeout_ms: int,
    har_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    results: list[dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for journey in journeys:
            name = str(journey.get("name") or f"journey-{len(results) + 1}")
            j_dir = out_dir / "artifacts" / name
            j_dir.mkdir(parents=True, exist_ok=True)
            har_path = har_dir / f"{name}.har"
            har_path.parent.mkdir(parents=True, exist_ok=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_har_path=str(har_path),
            )
            page = context.new_page()
            steps_out: list[dict[str, Any]] = []
            failures: list[str] = []
            started = now_iso()
            for idx, step in enumerate(journey.get("steps") or [], start=1):
                stype = str(step.get("type") or "")
                entry: dict[str, Any] = {"index": idx, "type": stype}
                if "stage" in step:
                    entry["stage"] = step.get("stage")
                want_artifact = step.get("artifact", stype in {"goto", "click", "assert_text", "source"})
                t0 = time.monotonic()
                try:
                    if stype in {"goto", "source"}:
                        target = _resolve_url(base, step)
                        entry["requested_url"] = target
                        resp = page.goto(target, timeout=timeout_ms, wait_until="domcontentloaded")
                        entry["http_status"] = resp.status if resp else None
                    elif stype == "click":
                        page.click(str(step["selector"]), timeout=timeout_ms)
                    elif stype == "fill":
                        page.fill(str(step["selector"]), str(step.get("value") or ""), timeout=timeout_ms)
                    elif stype == "press":
                        page.press(str(step["selector"]), str(step.get("value") or "Enter"), timeout=timeout_ms)
                    elif stype == "wait":
                        page.wait_for_timeout(int(step.get("value") or 500))
                    elif stype == "assert_text":
                        page.wait_for_selector(f"text={step.get('value')}", timeout=timeout_ms)
                    else:
                        raise ValueError(f"unsupported step type {stype!r}")
                    entry["url_after"] = page.url
                    if stype == "source" and not _is_local(base):
                        src_host = urlparse(page.url).hostname or ""
                        base_host = urlparse(base).hostname or ""
                        if src_host == base_host:
                            failures.append(f"step {idx}: source stage stayed on deployed host {base_host}")
                    if want_artifact:
                        art = j_dir / f"step-{idx:02d}-{stype}.png"
                        page.screenshot(path=str(art), full_page=True)
                        entry["artifact"] = {
                            "path": str(art),
                            "sha256": sha256_file(art),
                            "bytes": art.stat().st_size,
                        }
                    entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                except Exception as exc:  # noqa: BLE001 - record any step failure verbatim
                    failures.append(f"step {idx} ({stype or '?'}): {type(exc).__name__}: {exc}")
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                steps_out.append(entry)
            context.close()
            har_entry: dict[str, Any] | None = None
            if har_path.exists() and har_path.stat().st_size > 0:
                har_entry = {"path": str(har_path), "sha256": sha256_file(har_path), "bytes": har_path.stat().st_size}
            verdict = "PASS" if not failures else "FAIL"
            results.append({
                "journey": name,
                "base_url": base,
                "started_at": started,
                "finished_at": now_iso(),
                "driver": "python-playwright",
                "steps": steps_out,
                "har": har_entry,
                "failures": failures,
                "verdict": verdict,
                "smoke": _is_local(base),
            })
        browser.close()
    return results, [r["journey"] for r in results if r["verdict"] != "PASS"]


def _run_node_playwright(
    journeys: list[dict[str, Any]],
    base: str,
    out_dir: Path,
    timeout_ms: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Node fallback: drive journeys with a generated JS runner."""
    import subprocess
    import tempfile

    spec = {
        "base": base,
        "timeoutMs": timeout_ms,
        "outDir": str(out_dir),
        "journeys": [
            {"name": j.get("name"), "steps": j.get("steps") or []}
            for j in journeys
        ],
    }
    js = r"""
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const spec = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const { chromium } = require('playwright');
function sha256(p){ const b = fs.readFileSync(p); return crypto.createHash('sha256').update(b).digest('hex'); }
(async () => {
  const out = [];
  const browser = await chromium.launch({ headless: true });
  for (const journey of spec.journeys) {
    const jDir = path.join(spec.outDir, 'artifacts', journey.name);
    fs.mkdirSync(jDir, { recursive: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    const steps = []; const failures = [];
    for (let i = 0; i < journey.steps.length; i++) {
      const step = journey.steps[i]; const idx = i + 1;
      const entry = { index: idx, type: step.type, stage: step.stage };
      try {
        if (step.type === 'goto' || step.type === 'source') {
          const target = /^https?:/.test(step.url || '') ? step.url : spec.base.replace(/\/$/, '') + '/' + String(step.url || '').replace(/^\//, '');
          entry.requested_url = target;
          const resp = await page.goto(target, { timeout: spec.timeoutMs, waitUntil: 'domcontentloaded' });
          entry.http_status = resp ? resp.status() : null;
        } else if (step.type === 'click') { await page.click(step.selector, { timeout: spec.timeoutMs }); }
        else if (step.type === 'fill') { await page.fill(step.selector, step.value || '', { timeout: spec.timeoutMs }); }
        else if (step.type === 'press') { await page.press(step.selector, step.value || 'Enter', { timeout: spec.timeoutMs }); }
        else if (step.type === 'wait') { await page.waitForTimeout(step.value || 500); }
        else if (step.type === 'assert_text') { await page.waitForSelector('text=' + step.value, { timeout: spec.timeoutMs }); }
        else { throw new Error('unsupported step type ' + step.type); }
        entry.url_after = page.url;
        const want = step.artifact !== undefined ? step.artifact : ['goto','click','assert_text','source'].includes(step.type);
        if (want) {
          const art = path.join(jDir, 'step-' + String(idx).padStart(2, '0') + '-' + step.type + '.png');
          await page.screenshot({ path: art, fullPage: true });
          entry.artifact = { path: art, sha256: sha256(art), bytes: fs.statSync(art).size };
        }
      } catch (e) { failures.push('step ' + idx + ' (' + (step.type || '?') + '): ' + e.message); entry.error = e.message; }
      steps.push(entry);
    }
    await context.close();
    out.push({ journey: journey.name, base_url: spec.base, driver: 'node-playwright',
               steps, failures, verdict: failures.length ? 'FAIL' : 'PASS' });
  }
  await browser.close();
  fs.writeFileSync(path.join(spec.outDir, 'journey-receipts.json'), JSON.stringify(out, null, 2));
  process.exit(out.some(r => r.verdict !== 'PASS') ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        js_path = fh.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        spec_path = fh.name
    receipts_path = out_dir / "journey-receipts.json"
    try:
        proc = subprocess.run(
            ["node", js_path, spec_path],
            capture_output=True, text=True, timeout=max(600, timeout_ms * len(journeys) // 1000),
        )
        receipts = json.loads(receipts_path.read_text(encoding="utf-8")) if receipts_path.exists() else []
        for r in receipts:
            r["started_at"] = r.get("started_at") or now_iso()
            r["finished_at"] = now_iso()
            r["smoke"] = _is_local(base)
            for s in r.get("steps") or []:
                art = s.get("artifact")
                if art and Path(art["path"]).exists():
                    art["sha256"] = sha256_file(Path(art["path"]))
        failed = [r["journey"] for r in receipts if r.get("verdict") != "PASS"]
        if proc.returncode not in (0, 1) or not receipts:
            return receipts or [], [f"node runner exit {proc.returncode}: {proc.stderr[-400:]}"]
        return receipts, failed
    finally:
        for p in (js_path, spec_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = ACCEPTANCE_DIR
    ap.add_argument("--config", default=str(here / "config.json"))
    ap.add_argument("--base", default=None, help="deployed base URL (default deploy.preview_base_url)")
    ap.add_argument("--out", default=str(here / "evidence" / "HG10"))
    ap.add_argument("--journey", action="append", default=None, help="restrict to named journey(s)")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"FAIL: config not found: {cfg_path}", file=sys.stderr)
        return 2
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    base = (args.base or (cfg.get("deploy") or {}).get("preview_base_url") or "").strip()
    if not base:
        print("FAIL: no --base and deploy.preview_base_url empty — journeys cannot run", file=sys.stderr)
        return 2
    journeys = (cfg.get("gates") or {}).get("hg10", {}).get("journeys") or []
    if args.journey:
        journeys = [j for j in journeys if j.get("name") in set(args.journey)]
    if not journeys:
        print("FAIL: no journeys configured (gates.hg10.journeys)", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    driver = detect_driver(cfg)
    if driver is None:
        receipt = {
            "verdict": "UNKNOWN",
            "detail": "no usable browser driver: playwright not importable by "
                      f"{os.path.basename(sys.executable) or 'python3'} nor configured venv_python; install e.g. "
                      "`uv pip install --python .venv/bin/python playwright && .venv/bin/playwright install chromium`",
            "ran_at": now_iso(),
        }
        (out_dir / "journey-receipts.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print("UNKNOWN: " + receipt["detail"], file=sys.stderr)
        return 3

    if driver == "node-playwright":
        receipts, failed = _run_node_playwright(journeys, base, out_dir, args.timeout_ms)
        (out_dir / "journey-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    else:
        receipts, failed = _run_python_playwright(journeys, base, out_dir, args.timeout_ms, out_dir / "har")
        (out_dir / "journey-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")

    for r in receipts:
        print(f"{r['journey']}: {r['verdict']}" + (f" — {'; '.join(r['failures'][:3])}" if r.get("failures") else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
