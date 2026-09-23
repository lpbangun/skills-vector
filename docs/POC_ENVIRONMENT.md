# Reproducible POC environment

This setup is for offline, lane-isolated preparation only. It does not fetch the shared corpus, load live credentials, call a provider, deploy a preview, launch a Pi, or create a lane configuration for a parent worker.

## Runtime

The seed uses `/usr/bin/python3.12` (3.12.3), the checked-in `.python-version`, the checked-in `uv.lock`, and a pinned `setuptools==80.9.0` build dependency.

From this worktree, the reproducible install is:

```sh
uv sync --frozen --group dev
```

Playwright is the only added development dependency. Its exact package version pins the browser revision selected by Playwright. Install Chromium into an explicitly chosen, non-global location:

```sh
PLAYWRIGHT_BROWSERS_PATH=/absolute/path/to/poc-browser \
  .venv/bin/python -m playwright install chromium
```

No apt or other global package installation is part of this setup.

## Lane configuration

The parent worker creates one nonsecret JSON file per lane after this setup is ready. Do not commit credentials in these files. Every path must be absolute. The required shape is:

```json
{
  "schema_version": 1,
  "lane": "A",
  "worktree": "/absolute/worktree-A",
  "corpus": {
    "directory": "/absolute/shared-corpus",
    "manifest": "/absolute/shared-corpus/manifest.json",
    "read_only": true
  },
  "lane_root": "/absolute/lane-roots/lane-a",
  "venv": "/absolute/lane-roots/lane-a/.venv",
  "paths": {
    "state": "/absolute/lane-roots/lane-a/state/skills_vector.db",
    "releases": "/absolute/lane-roots/lane-a/releases",
    "cache": "/absolute/lane-roots/lane-a/cache",
    "output": "/absolute/lane-roots/lane-a/output"
  },
  "browser": {
    "browsers_path": "/absolute/poc-browser"
  },
  "mode": {
    "offline_by_default": true,
    "live_inference": "not_configured"
  }
}
```

`lane_root` is the state-separation boundary. The venv and all four writable roots must be descendants of it; writable roots must not overlap one another, the venv, or the shared corpus. They may live under the worktree (the prepared lanes use ignored `.poc-env/`). A `.poc-lane.json` marker is created when `run` first claims a lane root; a marker for another lane is a hard collision error. These controls prevent accidental shared-state use; they are not a security sandbox or OS-level network prohibition.

The shared corpus manifest is also deliberately narrow:

```json
{
  "schema_version": 1,
  "sources": [
    {
      "id": "source-id",
      "url": "https://example.invalid/source",
      "path": "raw/source.bin",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "status": "present"
    }
  ]
}
```

Source paths are relative to the manifest directory. The collected snapshot uses schema `skills-vector.poc-corpus.v1` and status `retrieved`; both that format and the minimal schema-1/`present` example are accepted. Optional `extract_path`/`extract_sha256` pairs are also verified. `doctor` rejects path traversal, zero retrieved sources, missing files, and raw/extract hash mismatches. It never downloads a source. It checks both lockfile freshness and the configured venv against `uv sync --frozen --group dev --check --offline`.

## Commands

Validate a lane without running product work:

```sh
.venv/bin/python scripts/poc_env.py doctor --config /absolute/lane-a.json
```

The doctor returns JSON. `status: "READY_OFFLINE"` means the local Python/import/lock/corpus/isolation checks passed. `live_status: "LIVE_NOT_CONFIGURED"` and `live_inference_passed: false` remain explicit; this utility never claims live inference.

Run a command in the lane:

```sh
.venv/bin/python scripts/poc_env.py run \
  --config /absolute/lane-a.json \
  -- python -m unittest discover -s tests -v
```

The runner sets absolute `SKILLS_VECTOR_DATABASE` and `SKILLS_VECTOR_RELEASES`, sets `SKILLS_VECTOR_MONTHLY_BUDGET_USD=0`, forces `SKILLS_VECTOR_RUNTIME=offline`, isolates cache and temporary directories under the lane cache, sets `PYTHONNOUSERSITE=1` and `UV_PROJECT_ENVIRONMENT`, and prepends the configured lane venv to `PATH`. It strips known sensitive environment-name patterns and stale `SKILLS_VECTOR_*` controls; this is not a guarantee that arbitrary environment names or credential files cannot contain secrets. `HOME` is not changed; a caller that specifically needs `/home/logani` may pass `HOME=/home/logani` explicitly. Pipeline offline mode does not disable coding-agent subscription access stored in that HOME.

Run the actual installed Chromium headlessly against harmless local HTML and save a machine-readable receipt:

```sh
.venv/bin/python scripts/poc_env.py browser-smoke \
  --browsers-path /absolute/poc-browser \
  --receipt /absolute/evidence/browser-smoke.json
```

The smoke command uses `--no-sandbox`, records the Playwright and actual Chromium versions, checks headless launch, title and local-content marker, and performs no deployment or network fetch.
