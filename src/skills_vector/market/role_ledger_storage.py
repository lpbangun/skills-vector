"""Lossless storage codec for pending none calls. Ledger math stays in role_ledger."""
from copy import deepcopy

from .role_artifacts import digest, require

SCHEMA = "skills-vector.forecast-ledger/2"
COLUMNS = ["id", "skill_id", "created", "resolves", "status"]


def pack(ledger: dict) -> dict:
    groups, entries = {}, []
    for entry in ledger["entries"]:
        f = entry["forecast"]
        eligible = (f["basis"] == "none" and entry["resolution"] is None and entry["event"] is None
                    and entry["event_rationale"] is None and entry["backtest"] is None
                    and f["id"].endswith(entry["skill_id"]))
        if not eligible:
            entries.append(deepcopy(entry)); continue
        shared = deepcopy(entry)
        for key in ("id", "skill_id"): shared.pop(key)
        prefix = f["id"][:-len(entry["skill_id"])]
        for key in ("id", "created", "resolves"): shared["forecast"].pop(key)
        group = groups.setdefault(digest([shared, prefix]), {"shared": shared, "forecast_id_prefix": prefix, "calls": []})
        group["calls"].append([entry["id"], entry["skill_id"], f["created"], f["resolves"], "pending"])
    if not groups:
        return deepcopy(ledger)
    return {**deepcopy(ledger), "schema": SCHEMA, "entries": entries,
            "pending_columns": COLUMNS, "pending": [groups[k] for k in sorted(groups)]}


def unpack(stored: dict) -> dict:
    require(isinstance(stored, dict), "invalid forecast ledger")
    if stored.get("schema") != SCHEMA:
        return stored
    require(stored.get("pending_columns") == COLUMNS and isinstance(stored.get("pending"), list), "invalid pending ledger columns")
    result = deepcopy(stored)
    result["schema"] = "skills-vector.forecast-ledger/1"
    result.pop("pending_columns"); result.pop("pending")
    for group in stored["pending"]:
        require(set(group) == {"shared", "forecast_id_prefix", "calls"}, "invalid pending ledger group")
        shared = group["shared"]
        require(set(shared) == {"role_id", "data_kind", "source_release", "source_sha256", "forecast", "scope", "event", "event_rationale", "resolution", "backtest"}, "invalid pending shared fields")
        require(shared["forecast"]["basis"] == "none" and not {"id", "created", "resolves"} & shared["forecast"].keys()
                and all(shared[k] is None for k in ("event", "event_rationale", "resolution", "backtest")), "only pending none calls can be compacted")
        for row in group["calls"]:
            require(isinstance(row, list) and len(row) == 5 and all(isinstance(v, str) for v in row) and row[4] == "pending", "invalid pending call")
            entry = deepcopy(shared)
            entry.update(id=row[0], skill_id=row[1])
            entry["forecast"].update(id=group["forecast_id_prefix"] + row[1], created=row[2], resolves=row[3])
            result["entries"].append(entry)
    result["entries"].sort(key=lambda e: e["id"])
    return result
