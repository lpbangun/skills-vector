"""Deterministic forecast ledger and causal empirical-base-rate backtest, local files only."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

from jsonschema import ValidationError

from .role_artifacts import digest, load_roles, read_json, require, write_json
from .role_atlas import RoleRecord, _period, _validator

SCHEMA = "skills-vector.forecast-ledger/1"
NO_ARCHIVE = "not run: no historical posting archive"


def score_pairs(pairs: list[tuple[float, int]]) -> dict:
    """Equal-weight Brier mean; ten left-closed bins, with 1 in the last bin."""
    values = []
    for probability, outcome in pairs:
        require(type(probability) in (float, int) and type(outcome) is int and outcome in (0, 1), "score needs numeric probability and binary integer outcome")
        p = Decimal(str(probability))
        require(p.is_finite() and 0 <= p <= 1, "probability outside 0–1")
        values.append((p, outcome))
    if not values:
        return {"scored_count": 0, "brier_score": None, "calibration_bins": []}
    bins = []
    for index in range(10):
        members = [(p, y) for p, y in values if min(int(p * 10), 9) == index]
        count = len(members)
        bins.append({"lower": index / 10, "upper": (index + 1) / 10, "midpoint": (index + 0.5) / 10,
                     "n": count, "mean_probability": float(sum(p for p, _ in members) / count) if count else None,
                     "observed_rate": sum(y for _, y in members) / count if count else None})
    return {"scored_count": len(values),
            "brier_score": float(sum((p - y) ** 2 for p, y in values) / len(values)), "calibration_bins": bins}


def not_run(reason: str = "No historical posting archive was provided. One current snapshot is not history.", *, status: str = NO_ARCHIVE) -> dict:
    return {"status": status, "reason": reason, "forecasts_estimated": 0, "per_forecast": [],
            **score_pairs([])}


def _event(value: dict | None) -> None:
    if value is None:
        return
    require(isinstance(value, dict) and set(value) == {"kind", "threshold"}, "event needs kind and threshold")
    require(value["kind"] in ("share_at_least", "share_below"), "unsupported event")
    threshold = value["threshold"]
    require(type(threshold) in (float, int) and 0 <= threshold <= 1, "event threshold outside 0–1")


def _outcome(event: dict, share: float) -> int:
    return int(share >= event["threshold"] if event["kind"] == "share_at_least" else share < event["threshold"])


def _entry_id(role: str, skill: str, release: str, forecast_id: str) -> str:
    return "call." + digest([role, skill, release, forecast_id])[:24]


def create_ledger(records: list[dict], events: list[dict] | None = None) -> dict:
    require(bool(records), "no source records")
    require(len({r["data_kind"] for r in records}) == 1, "a ledger cannot mix fixtures and research")
    event_map = {}
    for spec in events or []:
        require(isinstance(spec, dict) and set(spec) == {"role_id", "skill_id", "claim", "event", "rationale"}, "event spec needs role_id, skill_id, exact claim, event and rationale")
        require(isinstance(spec["rationale"], str) and bool(spec["rationale"].strip()), "event rationale is blank")
        _event(spec["event"])
        require(spec["event"] is not None, "event spec cannot be null")
        key = (spec["role_id"], spec["skill_id"])
        require(key not in event_map, "duplicate event spec")
        event_map[key] = spec
    entries, used = [], set()
    for record in sorted(records, key=lambda r: (r["release_id"], r["id"])):
        RoleRecord.from_dict(record)
        source_sha256 = digest(record)
        for skill in record["skill_map"]["skills"]:
            if skill["forecast"] is None:
                continue
            forecast = deepcopy(skill["forecast"])
            if record["data_kind"] == "research":
                forecast.update(basis="none", probability=None, base_rate=None,
                                reason=forecast.get("reason") if forecast["basis"] == "none" else "A local historical backtest is required before estimating a ledger probability.")
            key = (record["id"], skill["id"])
            spec = event_map.get(key)
            if spec:
                require(spec["claim"] == forecast["claim"], "event spec claim does not match the source forecast")
                used.add(key)
            current = skill["windows"][-1]
            entries.append({"id": _entry_id(record["id"], skill["id"], record["release_id"], forecast["id"]),
                            "role_id": record["id"], "skill_id": skill["id"], "data_kind": record["data_kind"],
                            "source_release": record["release_id"], "source_sha256": source_sha256,
                            "forecast": forecast, "event": deepcopy(spec["event"]) if spec else None,
                            "event_rationale": spec["rationale"] if spec else None,
                            "scope": {"comparison_group": current["comparison_group"], "levels": list(current["levels"]),
                                      "window_length": record["demand_window"]["window_length"]},
                            "resolution": None, "backtest": None})
    require(used == set(event_map), "event spec references a missing forecast")
    ledger = {"schema": SCHEMA, "entries": sorted(entries, key=lambda e: e["id"]), "backtest": not_run(),
              "limits": ["Research calls start without probabilities. Only a causal local-archive backtest can estimate them.",
                         "Events are operator-bound to exact claims; prose is never parsed into thresholds.",
                         "Missing skills, missing scopes and changed sampling groups are not negative outcomes.",
                         "Fixture probabilities are illustrative and excluded from track-record scores."]}
    validate_ledger(ledger)
    return ledger


def append_ledger(existing: dict, fresh: dict) -> dict:
    validate_ledger(existing)
    validate_ledger(fresh)
    result = deepcopy(existing)
    indexed = {e["id"]: e for e in result["entries"]}
    for entry in fresh["entries"]:
        if entry["id"] in indexed:
            old = indexed[entry["id"]]
            require(all(old[k] == entry[k] for k in entry if k not in ("forecast", "resolution", "backtest")), "issued call source/event is immutable")
            require(all(old["forecast"][k] == entry["forecast"][k] for k in ("id", "claim", "created", "resolves", "horizon_quarters", "scored_against")), "issued claim is immutable")
        else:
            key = lambda e: (e["role_id"], e["skill_id"], e["forecast"]["id"], e["forecast"]["created"])
            require(not any(key(e) == key(entry) for e in indexed.values()), "same issued call in a different release; select one origin release")
            indexed[entry["id"]] = entry
    result["entries"] = sorted(indexed.values(), key=lambda e: e["id"])
    validate_ledger(result)
    return result


def _measurement(record: dict, entry: dict) -> tuple[dict | None, str | None]:
    if record["id"] != entry["role_id"] or record["data_kind"] != entry["data_kind"]:
        return None, "role or data kind mismatch"
    if record["demand_window"]["window_length"] != entry["scope"]["window_length"]:
        return None, "window cadence mismatch"
    skill = next((s for s in record["skill_map"]["skills"] if s["id"] == entry["skill_id"]), None)
    if skill is None:
        return None, "skill not recorded; absence is not a measured zero"
    measurement = skill["windows"][-1]
    if measurement["comparison_group"] != entry["scope"]["comparison_group"] or measurement["levels"] != entry["scope"]["levels"]:
        return None, "sampling group or level scope mismatch"
    levels = record["ladder"]["levels"]
    assigned = sum(level["evidence"]["postings"] for level in levels)
    require(assigned <= record["demand_window"]["postings"], "assigned postings exceed the role denominator")
    denominator = sum(level["evidence"]["postings"] for level in levels if level["level"] in measurement["levels"])
    if "unassigned" in measurement["levels"]:
        denominator += record["demand_window"]["postings"] - assigned
    if denominator <= 0:
        return None, "no postings in the stated scope"
    return {"window": measurement["window"], "release_id": record["release_id"], "share": measurement["share"],
            "postings": denominator, "record_sha256": digest(record)}, None


def resolve_ledger(ledger: dict, records: list[dict]) -> dict:
    validate_ledger(ledger)
    for record in records:
        RoleRecord.from_dict(record)
    result, deferred = deepcopy(ledger), []
    for entry in result["entries"]:
        forecast = entry["forecast"]
        candidates = [r for r in records if r["id"] == entry["role_id"] and r["demand_window"]["id"] == forecast["resolves"]]
        require(len(candidates) <= 1, "ambiguous resolving window; select one release")
        if not candidates:
            deferred.append({"id": entry["id"], "reason": "exact resolving window not supplied; later or earlier windows are not substitutes"})
            continue
        if entry["event"] is None:
            deferred.append({"id": entry["id"], "reason": "no structured event bound to the claim"})
            continue
        measurement, reason = _measurement(candidates[0], entry)
        if reason:
            deferred.append({"id": entry["id"], "reason": reason})
            continue
        resolution = {**measurement, "outcome": _outcome(entry["event"], measurement["share"])}
        require(entry["resolution"] is None or entry["resolution"] == resolution, "resolution is immutable; conflicting evidence")
        entry["resolution"] = resolution
    validate_ledger(result)
    return {"ledger": result, "deferred": deferred}


def _prequential(pairs: list[dict]) -> dict:
    predictions = []
    for pair in pairs:
        # Only outcomes from strictly earlier windows were knowable at this origin.
        prior = [p["outcome"] for p in pairs if _period(p["target"]) < _period(pair["origin"])]
        if prior:
            predictions.append((sum(prior) / len(prior), pair["outcome"]))
    return score_pairs(predictions)


def backtest_ledger(ledger: dict, archive: Path | None) -> dict:
    validate_ledger(ledger)
    result = deepcopy(ledger)
    require(archive is None or not archive.exists() or archive.is_dir(), "archive must be a directory of role/2 snapshots")
    if archive is None or not archive.exists() or not any(archive.rglob("*.json")):
        report = not_run()
    else:
        require(archive.is_dir(), "archive must be a directory of role/2 snapshots")
        snapshots = []
        for path in sorted(archive.rglob("*.json")):
            record = RoleRecord.from_dict(read_json(path)).to_dict()
            require(record["data_kind"] == "research", "fixture archive cannot train research forecasts")
            snapshots.append(record)
        archive_sha256 = digest(sorted((digest(r) for r in snapshots)))
        reports = []
        for entry in result["entries"]:
            if entry["data_kind"] != "research" or entry["event"] is None or entry["resolution"] is not None or entry["forecast"]["basis"] != "none":
                continue
            observations = {}
            for record in snapshots:
                if _period(record["demand_window"]["id"]) >= _period(entry["forecast"]["created"]):
                    continue
                measurement, reason = _measurement(record, entry)
                if reason:
                    continue
                require(measurement["window"] not in observations, "duplicate comparable archive window; no cherry-picking releases")
                observations[measurement["window"]] = measurement
            pairs = []
            for origin, observation in sorted(observations.items(), key=lambda item: _period(item[0])):
                targets = [m for w, m in observations.items() if _period(w) - _period(origin) == 12]
                if targets:
                    target = targets[0]
                    pairs.append({"origin": origin, "target": target["window"], "share": target["share"],
                                  "outcome": _outcome(entry["event"], target["share"]),
                                  "origin_sha256": observation["record_sha256"], "target_sha256": target["record_sha256"]})
            scores = _prequential(pairs)
            if len(pairs) < 2 or scores["scored_count"] == 0:
                continue
            rate = sum(p["outcome"] for p in pairs) / len(pairs)
            receipt = {"archive_sha256": archive_sha256, "cutoff": entry["forecast"]["created"],
                       "recipe": "causal-empirical-event-rate/1", "historical_pairs": pairs, **scores}
            entry["forecast"].update(basis="backtest", probability=rate, base_rate=rate, reason=None)
            entry["backtest"] = receipt
            reports.append({"id": entry["id"], "historical_pairs": len(pairs), **scores})
        report = ({"status": "run", "reason": "Empirical event base rate; strictly past matured pairs and rolling-origin scores. Not a learned or market-wide probability.",
                   "archive_sha256": archive_sha256, "forecasts_estimated": len(reports), "per_forecast": reports}
                  if reports else not_run("No pending call has an event and enough comparable, pre-creation historical pairs for a causal score.", status="not run: insufficient comparable historical pairs"))
    # A rerun without new eligible calls must not erase the provenance of frozen estimates.
    if report["status"] == "run" or not any(e["backtest"] for e in result["entries"]):
        result["backtest"] = report
    validate_ledger(result)
    return {"ledger": result, "report": report}


def validate_ledger(ledger: dict) -> None:
    try:
        _validate_ledger(ledger)
    except (KeyError, TypeError, ValidationError) as exc:
        raise ValueError("invalid forecast ledger structure") from exc


def _validate_ledger(ledger: dict) -> None:
    require(isinstance(ledger, dict) and ledger.get("schema") == SCHEMA and isinstance(ledger.get("entries"), list), "invalid forecast ledger")
    entries = ledger["entries"]
    require(all(isinstance(e, dict) for e in entries), "invalid ledger entry")
    require(len({e["data_kind"] for e in entries}) <= 1, "ledger mixes fixtures and research")
    report = ledger["backtest"]
    require(isinstance(report, dict) and isinstance(report["reason"], str) and bool(report["reason"].strip()), "invalid backtest reason")
    if report["status"].startswith("not run"):
        require(report["forecasts_estimated"] == 0 and report["per_forecast"] == [] and report["scored_count"] == 0 and report["brier_score"] is None and report["calibration_bins"] == [], "unrun backtest cannot have scores")
    else:
        require(report["status"] == "run" and report["forecasts_estimated"] == len(report["per_forecast"]) > 0, "invalid backtest report")
    require(len({e["id"] for e in entries}) == len(entries), "duplicate ledger call")
    require(len({(e["role_id"], e["skill_id"], e["forecast"]["id"], e["forecast"]["created"]) for e in entries}) == len(entries), "duplicate issued claim across releases")
    validator = _validator().evolve(schema={"$defs": _validator().schema["$defs"], "$ref": "#/$defs/Forecast"})
    for entry in entries:
        forecast = entry["forecast"]
        validator.validate(forecast)
        require(entry["id"] == _entry_id(entry["role_id"], entry["skill_id"], entry["source_release"], forecast["id"]), "call identity mismatch")
        require(entry["data_kind"] in ("research", "fixture"), "invalid ledger data kind")
        require(_period(forecast["resolves"]) - _period(forecast["created"]) == 12 and ("Q" in forecast["resolves"]) == ("Q" in forecast["created"]), "ledger needs exact four-quarter horizon and consistent cadence")
        _event(entry["event"])
        require(bool(re.fullmatch(r"[a-f0-9]{64}", entry["source_sha256"])), "invalid source digest")
        scope = entry["scope"]
        require(isinstance(scope["comparison_group"], str) and bool(scope["comparison_group"].strip()) and isinstance(scope["levels"], list) and bool(scope["levels"]) and set(scope["levels"]) <= {1, 2, 3, 4, 5, "unassigned"} and len(set(scope["levels"])) == len(scope["levels"]), "invalid measurement scope")
        require(scope["window_length"] == ("quarterly" if "Q" in forecast["created"] else "monthly"), "scope cadence mismatch")
        require((entry["event"] is None and entry["event_rationale"] is None) or (entry["event"] is not None and isinstance(entry["event_rationale"], str) and bool(entry["event_rationale"].strip())), "event needs an alignment rationale")
        require(entry["data_kind"] != "research" or forecast["basis"] != "fixture", "research call cannot use fixture basis")
        receipt = entry["backtest"]
        if forecast["basis"] == "backtest":
            require(entry["data_kind"] == "research" and isinstance(receipt, dict), "numeric research call needs a backtest receipt")
            pairs = receipt["historical_pairs"]
            require(receipt["recipe"] == "causal-empirical-event-rate/1" and bool(re.fullmatch(r"[a-f0-9]{64}", receipt["archive_sha256"])), "unknown backtest recipe or archive digest")
            require(len(pairs) >= 2 and receipt["cutoff"] == forecast["created"] and entry["event"] is not None, "insufficient backtest provenance")
            require(len({pair["origin"] for pair in pairs}) == len(pairs), "duplicate historical pairs")
            for pair in pairs:
                require(all(bool(re.fullmatch(r"[a-f0-9]{64}", pair[key])) for key in ("origin_sha256", "target_sha256")), "missing historical evidence digests")
                require(_period(pair["target"]) - _period(pair["origin"]) == 12 and _period(pair["target"]) < _period(forecast["created"]), "backtest look-ahead")
                require(type(pair["outcome"]) is int and 0 <= pair["share"] <= 1 and pair["outcome"] == _outcome(entry["event"], pair["share"]), "invalid archived outcome")
            rate = sum(pair["outcome"] for pair in pairs) / len(pairs)
            scores = _prequential(pairs)
            require(scores["scored_count"] > 0 and all(receipt[key] == value for key, value in scores.items()), "invalid causal backtest math")
            require(forecast["probability"] == rate and forecast["base_rate"] == rate, "probability does not match archived base rate")
        else:
            require(receipt is None, "non-backtest call has a backtest receipt")
        resolution = entry["resolution"]
        if resolution is not None:
            require(entry["event"] is not None and resolution["window"] == forecast["resolves"] and resolution["postings"] > 0, "invalid resolving window or scope")
            require(type(resolution["outcome"]) is int and 0 <= resolution["share"] <= 1 and resolution["outcome"] == _outcome(entry["event"], resolution["share"]), "invalid resolved outcome")


def read_ledger(path: Path) -> dict:
    ledger = read_json(path)
    validate_ledger(ledger)
    return ledger


def track_record(records: list[dict], ledger: dict | None) -> dict:
    if ledger is not None:
        validate_ledger(ledger)
    selected = {(r["id"], r["release_id"]): r for r in records}
    entries = [e for e in ledger["entries"] if (e["role_id"], e["source_release"]) in selected] if ledger else []
    source_hashes = {key: digest(selected[key]) for key in {(e["role_id"], e["source_release"]) for e in entries}}
    for entry in entries:
        key = (entry["role_id"], entry["source_release"])
        record = selected[key]
        require(entry["data_kind"] == record["data_kind"] and entry["source_sha256"] == source_hashes[key], "ledger source record mismatch")
    scored = [(e["forecast"]["probability"], e["resolution"]["outcome"]) for e in entries
              if e["data_kind"] == "research" and e["forecast"]["basis"] == "backtest" and e["resolution"] is not None]
    receipts = [e for e in entries if e["backtest"]]
    fallback = ledger["backtest"] if ledger and ledger["backtest"]["status"].startswith("not run") else not_run()
    backtest = ({"status": "run", "reason": "Causal historical backtest receipts exist; these are not live resolved-call scores.",
                 "forecasts_estimated": len(receipts), "per_forecast": [{"id": e["id"], **e["backtest"]} for e in receipts]}
                if receipts else fallback)
    return {"schema": "skills-vector.forecast-track-record/1", "ledger_selected": ledger is not None,
            "tracked_count": len(entries), "pending_count": sum(e["resolution"] is None for e in entries),
            "resolved_count": sum(e["resolution"] is not None for e in entries),
            "unscored_count": len(entries) - len(scored),
            "basis_counts": {basis: sum(e["forecast"]["basis"] == basis for e in entries) for basis in ("none", "fixture", "backtest")},
            "backtest": backtest, **score_pairs(scored),
            "reason": ("No resolved research calls with backtest probabilities. None-basis and fixture calls are never scored."
                       if not scored else "Equal-weight resolved research calls; lower Brier is better. Calibration is descriptive, not a guarantee."),
            "limits": ledger["limits"] if ledger else ["No ledger selected; no track record is claimed."]}


def run_ledger(args) -> dict:
    """Called only by the market operator CLI; no write endpoint exists."""
    action = args.ledger_action
    if action == "create":
        records = load_roles(args.roles_root, args.release_id)
        value = create_ledger(records, read_json(args.events) if args.events else None)
        if args.ledger.exists():
            value = append_ledger(read_ledger(args.ledger), value)
        write_json(args.ledger, value, replace=True, inputs=(args.roles_root,) + ((args.events,) if args.events else ()))
        return track_record(records, value)
    ledger = read_ledger(args.ledger)
    if action in ("show", "score"):
        return track_record(load_roles(args.roles_root, args.release_id), ledger)
    if action == "resolve":
        outcome = resolve_ledger(ledger, load_roles(args.roles_root, args.release_id))
        write_json(args.ledger, outcome["ledger"], replace=True, inputs=(args.roles_root,))
        return {"resolved_count": sum(e["resolution"] is not None for e in outcome["ledger"]["entries"]), "deferred": outcome["deferred"]}
    outcome = backtest_ledger(ledger, args.archive)
    write_json(args.ledger, outcome["ledger"], replace=True, inputs=(args.archive,) if args.archive else ())
    if args.output:
        write_json(args.output, outcome["report"], inputs=(args.ledger,) + ((args.archive,) if args.archive else ()))
    return outcome["report"]
