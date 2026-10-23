"""Release-pinned Role Atlas reads shared verbatim by REST and MCP."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from .role_atlas import CONTRACT_ROOT, SCHEMA_VERSION, RoleCatalogStore, _period
from .role_method import method_policy

FIXTURE_CAVEAT = "Fixture data: illustrative prototype numbers and assumptions, not research findings. See limits."
HTTP_STATUS = {
    "invalid_request": 400,
    "unknown_release": 404,
    "unknown_role": 404,
    "unknown_level": 404,
    "unknown_industry": 404,
    "unknown_expectation": 404,
    "role_catalog_unavailable": 503,
}
RELEASE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
ROLE_PATTERN = r"(?:role\.)?[a-z0-9][a-z0-9-]{0,63}"
WINDOW_PATTERN = r"[0-9]{4}-(?:Q[1-4]|0[1-9]|1[0-2])"
EXPECTATION_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}"


@cache
def _fixture_graphs() -> dict[str, Any]:
    return json.loads((CONTRACT_ROOT / "prototype.json").read_text(encoding="utf-8"))["graph"]


def _fixture_presentation(record: dict[str, Any]) -> dict[str, Any] | None:
    if record["data_kind"] != "fixture":
        return None
    graph = _fixture_graphs().get(record["id"].removeprefix("role."))
    if graph is None or {"skill." + node[0] for node in graph["nodes"]} != {skill["id"] for skill in record["skill_map"]["skills"]}:
        return None
    return {"data_kind": "fixture", "source": "design-concepts/role-atlas.html",
            "graph": {"families": [list(family) for family in graph["families"]],
                      "rows": {"skill." + node[0]: node[4] for node in graph["nodes"]}}}


class RoleReadError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _check(condition: bool, detail: str) -> None:
    if not condition:
        raise RoleReadError("invalid_request", detail)


@dataclass
class RoleAtlasReads:
    catalog: RoleCatalogStore | None = None
    fixtures: bool = False
    ledger_path: Path | None = None

    @classmethod
    def configured(cls, root: Path | str | None = None, *, fixtures: bool = False,
                   ledger_path: Path | str | None = None) -> RoleAtlasReads:
        if root is not None and not str(root).strip():
            raise ValueError("role-atlas root must not be blank")
        if root is not None and fixtures:
            raise ValueError("select a research role-atlas root OR the fixture catalog, not both")
        if ledger_path is not None and not str(ledger_path).strip():
            raise ValueError("role-atlas ledger path must not be blank")
        return cls(RoleCatalogStore.fixtures() if fixtures else RoleCatalogStore(Path(root)) if root is not None else None,
                   fixtures, Path(ledger_path) if ledger_path is not None else None)

    def track_record(self, role: str | None = None, release_id: str | None = None) -> dict[str, Any]:
        return self._read("track_record", release_id=release_id, **({"role": role} if role is not None else {}))

    def _track_record(self, records: list[dict]) -> dict:
        from .role_ledger import read_ledger, track_record
        ledger = read_ledger(self.ledger_path) if self.ledger_path is not None else None
        if ledger is not None and any(e["data_kind"] != ("fixture" if self.fixtures else "research") for e in ledger["entries"]):
            raise ValueError("ledger data kind does not match the catalog selection")
        return track_record(records, ledger)

    def list_roles(self, release_id: str | None = None) -> dict[str, Any]:
        return self._read("roles", release_id=release_id)

    def role(self, role: str, release_id: str | None = None) -> dict[str, Any]:
        return self._read("role", role=role, release_id=release_id)

    def level(self, role: str, level: str, release_id: str | None = None) -> dict[str, Any]:
        return self._read("level", role=role, level=level, release_id=release_id)

    def graph(self, role: str, release_id: str | None = None) -> dict[str, Any]:
        return self._read("graph", role=role, release_id=release_id)

    def industry(self, role: str, naics: str, release_id: str | None = None) -> dict[str, Any]:
        return self._read("industry", role=role, naics=naics, release_id=release_id)

    def shifts(self, since: str, release_id: str | None = None) -> dict[str, Any]:
        return self._read("shifts", since=since, release_id=release_id)

    def expectation(self, expectation_id: str, release_id: str) -> dict[str, Any]:
        return self._read("expectation", expectation_id=expectation_id, release_id=release_id)

    def _read(self, resource: str, *, release_id: str | None, **params: Any) -> dict[str, Any]:
        context = {"data_kind": "fixture" if self.fixtures else "research",
                   "fixture_caveat": FIXTURE_CAVEAT if self.fixtures else None}
        try:
            if release_id is not None:
                _check(bool(re.fullmatch(RELEASE_PATTERN, release_id)), "release must be an immutable release id")
            if "role" in params:
                _check(bool(re.fullmatch(ROLE_PATTERN, params["role"])), "role must be a lowercase role slug or role id")
            if resource == "level":
                _check(bool(re.fullmatch(r"L?[0-9]{1,2}", params["level"])), "level must be L1–L5 (or its number)")
            if resource == "industry":
                _check(bool(re.fullmatch(r"[0-9]{2}(?:[–-][0-9]{2})?", params["naics"])), "naics must be a broad sector code")
            if resource == "shifts":
                _check(bool(re.fullmatch(WINDOW_PATTERN, params["since"])), "since must be YYYY-Qn or YYYY-MM")
            if resource == "expectation":
                _check(bool(re.fullmatch(EXPECTATION_PATTERN, params["expectation_id"])), "invalid expectation id")
                _check(release_id is not None, "expectations require an explicit release id")
            if self.catalog is None:
                raise RoleReadError("role_catalog_unavailable", "No role/2 catalog selected. Configure a research catalog or explicitly select fixtures.")
            all_records = self.catalog.list_roles()
            expected_kind = "fixture" if self.fixtures else "research"
            if any(record["data_kind"] != expected_kind for record in all_records):
                raise RoleReadError("role_catalog_unavailable", "Catalog data kind does not match the explicit selection; fixtures require fixture mode.")
            if not all_records:
                raise RoleReadError("role_catalog_unavailable", "The selected role/2 catalog has no records.")
            release_keys: dict[str, tuple[int, str]] = {}
            for record in all_records:
                rid = record["release_id"]
                release_keys[rid] = max(release_keys.get(rid, (-1, rid)), (_period(record["demand_window"]["id"]), rid))
            rid = release_id if release_id is not None else max(release_keys, key=release_keys.get)
            context["release_id"] = rid
            records = [record for record in all_records if record["release_id"] == rid]
            if not records:
                raise RoleReadError("unknown_release", "Unknown role/2 release; no fallback was used.")
            context.update(record_schema=SCHEMA_VERSION,
                           limits=list(dict.fromkeys(limit for record in records for limit in record["limits"])))
            payload: dict[str, Any] = {"status": "ok", **context}
            if resource == "track_record" and "role" not in params:
                return {**payload, "track_record": self._track_record(records)}
            if resource == "roles":
                fields = ("id", "label", "status", "publication", "data_kind", "release_id", "anchors", "demand_window", "limits")
                return {**payload, "roles": [{field: record[field] for field in fields} for record in records]}
            if resource == "shifts":
                return {**payload, **self._shifts(records, all_records, release_keys, rid, params["since"])}
            if resource == "expectation":
                occurrences = [
                    {"role_id": record["id"], "publication": record["publication"], "level": level["level"],
                     "evidence": level["evidence"], "expectation": expectation}
                    for record in records for level in record["ladder"]["levels"] for expectation in level["expectations"]
                    if expectation["id"] == params["expectation_id"]
                ]
                field = "expectation"
                if not occurrences:
                    field = "skill"
                    occurrences = [{"role_id": record["id"], "publication": record["publication"], "skill": skill}
                                   for record in records for skill in record["skill_map"]["skills"]
                                   if skill["id"] == params["expectation_id"]]
                if not occurrences:
                    raise RoleReadError("unknown_expectation", "Unknown expectation or skill in the pinned release.")
                return {**payload, "citation_id": f"{params['expectation_id']}@{rid}",
                        field: occurrences[0][field], "occurrences": occurrences,
                        "postings": [], "excerpts": [], "evidence_status": "not_recorded",
                        "evidence_note": "The role/2 contract records level counts, not posting ids or quote excerpts. None are fabricated."}
            record = next((record for record in records if params["role"] in (record["id"], record["id"].removeprefix("role."))), None)
            if record is None:
                raise RoleReadError("unknown_role", "Unknown role in the pinned release.")
            payload.update(role_id=record["id"], publication=record["publication"], limits=record["limits"])
            if resource in ("role", "graph"):
                presentation = _fixture_presentation(record) if self.fixtures else None
                if presentation is not None:
                    payload["presentation"] = presentation
            if resource == "track_record":
                return {**payload, "track_record": self._track_record([record])}
            if resource == "role":
                return {**payload, "role": record, "track_record": self._track_record([record]), "method": method_policy()}
            if resource == "graph":
                return {**payload, "graph": record["skill_map"]}
            if resource == "level":
                number = int(params["level"].removeprefix("L"))
                level = next((item for item in record["ladder"]["levels"] if item["level"] == number), None)
                if level is None:
                    raise RoleReadError("unknown_level", "Unknown level for this role in the pinned release.")
                return {**payload, "level": level}
            naics = params["naics"].replace("-", "–")
            industry = next((item for item in record["industries"] if item["naics"].replace("-", "–") == naics), None)
            if industry is None:
                raise RoleReadError("unknown_industry", "Unknown sector for this role in the pinned release.")
            return {**payload, "industry": industry}
        except RoleReadError as exc:
            return {"status": exc.code, "detail": str(exc), **context}
        except (OSError, ValueError):
            return {"status": "role_catalog_unavailable", "detail": "The role/2 catalog is unreadable or invalid.", **context}

    def _shifts(self, records: list[dict], all_records: list[dict], release_keys: dict,
                rid: str, since: str) -> dict[str, Any]:
        changes = []
        current = []
        uncompared = []
        for record in records:
            for skill in record["skill_map"]["skills"]:
                latest = skill["windows"][-1]
                if skill["status"] == "core" or _period(latest["window"]) <= _period(since):
                    continue
                windows = [window for window in skill["windows"]
                           if window["comparison_group"] == latest["comparison_group"]
                           and set(window["levels"]) == set(latest["levels"])
                           and ("Q" in window["window"]) == ("Q" in latest["window"])]
                current.append({"role_id": record["id"], "skill_id": skill["id"], "label": skill["label"],
                                "short_label": skill["short_label"], "status": skill["status"], "disputed": skill["disputed"],
                                "windows": windows, "evidence_families": sorted({item["family"] for item in skill["evidence"]}),
                                "evidence": skill["evidence"], "forecast": skill["forecast"],
                                "release_id": rid, "data_kind": record["data_kind"], "publication": record["publication"],
                                "fixture_caveat": FIXTURE_CAVEAT if self.fixtures else None,
                                "cite": f"/v1/expectations/{skill['id']}@{rid}"})
            history = sorted((item for item in all_records if item["id"] == record["id"]
                              and release_keys[item["release_id"]] <= release_keys[rid]),
                             key=lambda item: release_keys[item["release_id"]])
            if len(history) < 2:
                uncompared.append(record["id"])
            for before, after in zip(history, history[1:]):
                if _period(after["demand_window"]["id"]) <= _period(since):
                    continue
                previous = {skill["id"]: skill["status"] for skill in before["skill_map"]["skills"]}
                for skill in after["skill_map"]["skills"]:
                    if skill["id"] in previous and previous[skill["id"]] != skill["status"]:
                        changes.append({"role_id": after["id"], "skill_id": skill["id"], "label": skill["label"],
                                        "previous_status": previous[skill["id"]], "status": skill["status"],
                                        "window": after["demand_window"]["id"], "release_id": after["release_id"],
                                        "data_kind": after["data_kind"], "fixture_caveat": FIXTURE_CAVEAT if self.fixtures else None,
                                        "evidence": skill["evidence"], "forecast": skill["forecast"]})
        changes.sort(key=lambda item: (release_keys[item["release_id"]], item["role_id"], item["skill_id"]))
        order = {"rising": 0, "early": 1, "fading": 2}
        current.sort(key=lambda item: (order[item["status"]], item["forecast"]["probability"] is None,
                                       -item["forecast"]["probability"] if item["forecast"]["probability"] is not None else 0,
                                       item["role_id"], item["skill_id"]))
        return {"since": since, "current": current, "state_changes": changes, "uncompared_roles": uncompared,
                "history_status": "not_recorded" if len(uncompared) == len(records) else "partial" if uncompared else "available",
                "history_note": "State changes compare stored role/2 release snapshots, not inferred historical non-posting signals. A single snapshot cannot establish a change."}


def add_role_atlas_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--role-atlas-root", type=Path, default=None, help="explicit research role/2 catalog directory")
    group.add_argument("--role-atlas-fixtures", action="store_true", help="explicitly serve the illustrative role/2 fixture catalog")
    parser.add_argument("--role-atlas-ledger", type=Path, default=None, help="explicit local forecast ledger; does not enable a catalog")


def role_atlas_from_environment() -> RoleAtlasReads:
    raw = os.environ.get("SKILLS_VECTOR_ROLE_ATLAS_FIXTURES", "0").lower()
    if raw not in ("0", "1", "false", "true"):
        raise ValueError("SKILLS_VECTOR_ROLE_ATLAS_FIXTURES must be 0/1 or false/true")
    return RoleAtlasReads.configured(os.environ.get("SKILLS_VECTOR_ROLE_ATLAS_ROOT"), fixtures=raw in ("1", "true"),
                                     ledger_path=os.environ.get("SKILLS_VECTOR_ROLE_ATLAS_LEDGER"))
