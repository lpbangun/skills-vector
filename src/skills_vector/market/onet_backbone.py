"""Deterministic, role-scoped O*NET 31.0 lexical grouping. No models or downloads."""
from __future__ import annotations

import csv
import hashlib
import re
from functools import cache
from pathlib import Path

from .role_artifacts import digest, read_json, require, write_json

ZIP_SHA256 = "6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9"
ROOT = Path(__file__).with_name("role_atlas_mappings")
LOOKUP = ROOT / "onet31_lookup.json"
RULES = ROOT / "dwa_rules.json"


@cache
def _pattern(value: str):
    return re.compile(value, re.I)


def derive(database: Path, mappings: Path, output: Path) -> dict:
    root = database / "db_31_0_text" if (database / "db_31_0_text").is_dir() else database
    roles = read_json(mappings)["roles"]
    codes = {slug: row["anchor"]["code"] for slug, row in roles.items()}
    hashes = {}
    def rows(name):
        path = root / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            required = {"GWAs to IWAs to DWAs.txt": {"GWA Element ID", "IWA Element ID", "DWA Element ID", "DWA Element Name"},
                        "Tasks to DWAs.txt": {"O*NET-SOC Code", "Task ID", "DWA Element ID"},
                        "Software Skills.txt": {"O*NET-SOC Code", "Workplace Example", "Element ID", "Element Name"}}[name]
            require(required <= set(reader.fieldnames or []), "missing O*NET upstream columns: " + name)
            return list(reader)
    hierarchy = {r["DWA Element ID"]: r for r in rows("GWAs to IWAs to DWAs.txt")}
    links = rows("Tasks to DWAs.txt")
    software = rows("Software Skills.txt")
    dwas = []
    for slug, code in sorted(codes.items()):
        grouped = {}
        for row in links:
            if row["O*NET-SOC Code"] == code:
                grouped.setdefault(row["DWA Element ID"], []).append(row["Task ID"])
        for key, tasks in sorted(grouped.items()):
            node = hierarchy[key]
            dwas.append({"role": slug, "soc": code, "id": key, "label": node["DWA Element Name"],
                         "task_ids": sorted(set(tasks)), "gwa": node["GWA Element ID"], "iwa": node["IWA Element ID"]})
    existing = {(r["role"], r["id"]) for r in dwas}
    for rule in read_json(RULES):
        for slug in rule.get("roles", []):
            if (slug, rule["id"]) not in existing:
                node = hierarchy[rule["id"]]
                dwas.append({"role": slug, "soc": None, "id": rule["id"], "label": node["DWA Element Name"], "task_ids": [],
                             "gwa": node["GWA Element ID"], "iwa": node["IWA Element ID"],
                             "source_note": "Global DWA lexical candidate; not an occupation-task membership assertion."})
                existing.add((slug, rule["id"]))
    dwas.sort(key=lambda r: (r["role"], r["id"]))
    categories = {}
    for row in software:
        for slug, code in codes.items():
            if row["O*NET-SOC Code"] == code:
                key = row["Element ID"]
                category = categories.setdefault(key, {"id": key, "label": row["Element Name"], "examples": {}})
                category["examples"].setdefault(slug, []).append(row["Workplace Example"])
    for category in categories.values():
        category["examples"] = {r: sorted(set(v)) for r, v in sorted(category["examples"].items())}
    lookup = {"schema": "skills-vector.onet-backbone/1", "version": "31.0", "source_zip_sha256": ZIP_SHA256,
              "source_files": hashes, "source_note": "Derived subset for the four local role anchors; official ids/names, not task equivalence adjudication.",
              "rules_sha256": hashlib.sha256(RULES.read_bytes()).hexdigest(),
              "attribution": "O*NET 31.0 Database, U.S. Department of Labor, Employment and Training Administration. CC BY 4.0. https://www.onetcenter.org/database.html",
              "roles": codes, "dwas": dwas, "software": sorted(categories.values(), key=lambda r: r["id"])}
    write_json(output, lookup, inputs=(database, mappings))
    return {"dwas": len(dwas), "software_categories": len(categories), "lookup_sha256": digest(lookup)}


class Backbone:
    def __init__(self, lookup: Path = LOOKUP, rules: Path = RULES):
        self.data, self.rules = read_json(lookup), read_json(rules)
        require(self.data["schema"] == "skills-vector.onet-backbone/1" and self.data["source_zip_sha256"] == ZIP_SHA256, "unsupported O*NET lookup provenance")
        self.fingerprint = digest({"lookup": self.data, "rules": self.rules, "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        self._cache = {}
        self.dwas = {(r["role"], r["id"]): r for r in self.data["dwas"]}
        for row in self.rules:
            require(row["confidence"] in ("exact", "close", "loose") and row["source_note"].strip(), "rule needs confidence/source note")
            require(any(r["id"] == row["id"] for r in self.data["dwas"]), "rule target absent from role-scoped lookup")
            re.compile(row["pattern"], re.I)

    def match(self, role: str, text: str) -> list[dict]:
        key = (role, text)
        if key not in self._cache:
            self._cache[key] = self._find(role, text)
        return [dict(m) for m in self._cache[key]]

    def _find(self, role: str, text: str) -> list[dict]:
        found = []
        clean = " ".join(text.split())
        negated = bool(re.search(r"\b(?:no|not)\s+(?:prior )?(?:experience|knowledge|cold.calling)\b.{0,35}\b(?:required|needed|necessary)\b", clean, re.I))
        if negated:
            return []
        for (slug, key), node in self.dwas.items():
            if slug != role:
                continue
            if clean.casefold().strip(" .") == node["label"].casefold().strip(" ."):
                found.append(self._match("dwa", key, node["label"], "exact-dwa-name", clean, "exact"))
            for rule in self.rules:
                if rule["id"] == key:
                    hit = _pattern(rule["pattern"]).search(clean)
                    if hit:
                        found.append(self._match("dwa", key, node["label"], "lexical-rule:" + rule["name"], hit[0], rule["confidence"]))
        # One primary DWA, preferring exact/full or the most specific lexical span.
        if found:
            found.sort(key=lambda m: (m["confidence"] != "exact", -len(m["matched_text"]), m["id"]))
            found = found[:1]
        for category in self.data["software"]:
            alternatives = []
            for example in category["examples"].get(role, []):
                aliases = [(example, "exact")]
                short = re.sub(r"\s+software$", "", example, flags=re.I)
                if short != example:
                    aliases.append((short, "close"))
                for alias, confidence in aliases:
                    if len(alias) < 4 or alias.casefold() in ("google", "blink", "database", "data entry", "data entry software", "database software"):
                        continue
                    hit = _pattern(r"(?<!\w)" + re.escape(alias) + r"(?!\w)").search(clean)
                    if hit:
                        if alias.casefold() == "linkedin" and re.match(r"\s*sales\s*navigator", clean[hit.end():], re.I):
                            continue  # A specific sales product is not the generic web-publishing example.
                        alternatives.append((len(alias), hit[0], confidence))
            # Explicit category acronyms are names, not inferred proficiency.
            acronym = {"2.E.6.c": r"\bCRM\b", "2.E.1.c": r"\bHRIS\b"}.get(category["id"])
            if role in category["examples"] and acronym:
                hit = re.search(acronym, clean, re.I)
                if hit:
                    alternatives.append((3, hit[0], "close"))
            if alternatives:
                _, matched, confidence = max(alternatives)
                found.append(self._match("software", category["id"], category["label"], "software-example-or-category", matched, confidence))
        return found

    @staticmethod
    def _match(kind, key, label, method, matched, confidence):
        return {"id": "onet31:" + kind + ":" + key, "kind": kind, "label": label, "method": method,
                "matched_text": matched, "confidence": confidence, "source_version": "31.0", "source_zip_sha256": ZIP_SHA256}

    def expand(self, role: str, expectations: list[dict]) -> list[dict]:
        output = []
        for expected in expectations:
            matches = self.match(role, expected["source_wording"])
            grouped = [m for m in matches if m["confidence"] in ("exact", "close")]
            if grouped:
                output.extend({**expected, "backbone_match": m} for m in grouped)
            else:
                output.append({**expected, "backbone_proposals": matches})
        return output
