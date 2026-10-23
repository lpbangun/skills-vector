"""Small deterministic local artifact helpers; never write published inputs."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .role_atlas import FIXTURE_ROOT, RoleCatalogStore, RoleRecord, _period

ROOT = Path(__file__).resolve().parents[3]


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path: Path) -> Any:
    require(not any(part.startswith(".env") for part in (*path.parts, *path.resolve().parts)), "not a data file")
    def pairs(items: list[tuple]) -> dict:
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=lambda token: require(False, f"nonfinite JSON: {token}"))


def guard_output(path: Path, *inputs: Path) -> Path:
    target = path.resolve()
    require(not any(parent.is_symlink() for parent in (path, *path.absolute().parents)), "output symlinks are not allowed")
    for protected in (ROOT / "preview/release", FIXTURE_ROOT, *inputs):
        protected = protected.resolve()
        require(not (target == protected or target.is_relative_to(protected)
                     or protected.is_relative_to(target)), f"output overlaps protected input: {protected}")
    require(not any(part.startswith(".env") for part in (*path.parts, *target.parts)), "not an output data path")
    return target


def write_json(path: Path, value: Any, *, replace: bool = False, inputs: tuple[Path, ...] = ()) -> None:
    target = guard_output(path, *inputs)
    payload = canonical(value)
    if target.exists() and not replace:
        require(target.read_text(encoding="utf-8") == payload, "refusing to replace a different artifact")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            name = handle.name
            handle.write(payload)
        os.replace(name, target)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def load_roles(root: Path, release_id: str | None = None) -> list[dict]:
    records = RoleCatalogStore(root).list_roles()
    require(bool(records), "no role/2 records")
    if release_id is None:
        release_id = max(records, key=lambda row: (_period(row["demand_window"]["id"]), row["release_id"]))["release_id"]
    result = [record for record in records if record["release_id"] == release_id]
    require(bool(result), "unknown release; no fallback")
    require(len({record["data_kind"] for record in result}) == 1, "mixed data kinds")
    return result


def period_end(window: str):
    from datetime import date
    require(bool(re.fullmatch(r"[0-9]{4}-(?:Q[1-4]|0[1-9]|1[0-2])", window)), "invalid window")
    year = int(window[:4])
    month = int(window[-1]) * 3 if "Q" in window else int(window[-2:])
    return date(year, month, calendar.monthrange(year, month)[1])


def publish_roles(records: list[dict], output: Path, receipt: dict, *inputs: Path) -> dict:
    target = guard_output(output, *inputs)
    release_id = "role_signals_" + digest({"recipe": "official-signals/1", "records": records, "receipt": receipt})[:16]
    filenames = {record["id"].removeprefix("role.") + ".json" for record in records}
    if target.exists():
        require({p.name for p in target.iterdir()} <= filenames | {"_provenance"}, "unrelated output files")
    for record in records:
        record["release_id"] = release_id
        for level in record["ladder"]["levels"]:
            for expectation in level["expectations"]:
                expectation["cite"] = f"/v1/expectations/{expectation['id']}@{release_id}"
        RoleRecord.from_dict(record)
    artifacts = {target / (r["id"].removeprefix("role.") + ".json"): r for r in records}
    artifacts[target / "_provenance/merge.json"] = receipt
    if target.exists():
        require({p for p in target.rglob("*") if p.is_file()} <= set(artifacts), "unrelated output artifacts")
    # Preflight the entire tree before any writes; a conflicting output stays unchanged.
    for path, value in artifacts.items():
        guard_output(path, *inputs)
        require(not path.exists() or path.read_text(encoding="utf-8") == canonical(value), "refusing to replace a different artifact")
    for path, value in artifacts.items():
        write_json(path, value, inputs=inputs)
    return {"release_id": release_id, "roles": len(records), "data_kind": records[0]["data_kind"]}
