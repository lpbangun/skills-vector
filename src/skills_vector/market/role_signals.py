"""Operator-only adapters for already-downloaded official files. No HTTP clients."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .role_artifacts import digest, load_roles, period_end, publish_roles, read_json, require
from .role_atlas import SOURCE_RULES, Measurement, RoleRecord, Signal, _validator, derive_status

KINDS = {
    "hot-technologies": ("onet_hot_technologies", "O*NET Technology Skills / Hot Technologies"),
    "emerging-tasks": ("onet_emerging_tasks", "O*NET Emerging Tasks"),
    "bls-industry": ("bls_industry_matrix", "BLS National Employment Matrix, occupation by industry"),
    "certifications": ("careeronestop", "CareerOneStop Certification Finder"),
}
SOC = r"[0-9]{2}-[0-9]{4}(?:\.[0-9]{2})?"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _timestamp(value: str) -> datetime:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value)), "retrieved_at needs an ISO timestamp with timezone")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(stamp.utcoffset() is not None, "retrieved_at needs timezone")
    return stamp


def _soc(value: str) -> str:
    require(bool(re.fullmatch(SOC, value)), "invalid occupation code")
    return value


def _number(value: str, *, percent: bool = False) -> float:
    from decimal import Decimal
    text = value.strip().replace(",", "").removesuffix("%")
    try:
        number = Decimal(text)
    except ArithmeticError as exc:
        raise ValueError("invalid matrix number") from exc
    require(number.is_finite() and number >= 0 and (not percent or number <= 100), "invalid nonnegative matrix value")
    result = float(number)
    require(math.isfinite(result), "matrix numeric overflow")
    return result


def _xlsx(path: Path) -> list[list[str]]:
    """Read cached scalar cells, not formulas/macros or external workbook links."""
    with zipfile.ZipFile(path) as book:
        require(sum(info.file_size for info in book.infolist()) <= 100_000_000, "workbook is too large")
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ["".join(node.itertext()) for node in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        relations = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        sheets = workbook.findall("s:sheets/s:sheet", NS)
        require(bool(sheets), "workbook has no sheets")
        rel_id = sheets[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        rel = next((r for r in relations if r.get("Id") == rel_id), None)
        require(rel is not None and rel.get("TargetMode") != "External", "worksheet must be local")
        member = rel.get("Target", "")
        member = member.lstrip("/") if member.startswith("/") else "xl/" + member
        require(".." not in member.split("/"), "invalid worksheet path")
        sheet = ET.fromstring(book.read(member))
        indexed = {}
        for row in sheet.findall("s:sheetData/s:row", NS):
            values = []
            for cell in row.findall("s:c", NS):
                require(cell.find("s:f", NS) is None, "formulas are not source data; export values first")
                ref = cell.get("r", "")
                letters = re.match(r"[A-Z]+", ref)
                require(letters is not None, "cell needs a column reference")
                index = 0
                for letter in letters[0]:
                    index = index * 26 + ord(letter) - 64
                require(index <= 1000, "too many columns")
                while len(values) < index:
                    values.append("")
                value = cell.findtext("s:v", "", NS)
                if cell.get("t") == "s":
                    value = shared[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", NS).itertext())
                values[index - 1] = value
            number = int(row.get("r") or re.search(r"[0-9]+", row.find("s:c", NS).get("r"))[0])
            indexed[number] = values
        def coordinate(ref):
            match = re.fullmatch(r"([A-Z]+)([0-9]+)", ref)
            require(match is not None, "invalid merged-cell reference")
            column = 0
            for letter in match[1]:
                column = column * 26 + ord(letter) - 64
            return int(match[2]), column
        for merged in sheet.findall("s:mergeCells/s:mergeCell", NS):
            start, end = (coordinate(ref) for ref in merged.get("ref", "").split(":"))
            require(1 <= start[0] <= end[0] <= 200000 and 1 <= start[1] <= end[1] <= 1000, "oversized merged range")
            first = indexed.get(start[0], [])
            value = first[start[1] - 1] if len(first) >= start[1] else ""
            for number in range(start[0], end[0] + 1):
                if number not in indexed:
                    continue
                values = indexed[number]
                while len(values) < end[1]:
                    values.append("")
                for column in range(start[1] - 1, end[1]):
                    if not values[column]:
                        values[column] = value
        return [indexed[number] for number in sorted(indexed)]


def _table(path: Path) -> list[list[str]]:
    require(path.suffix.lower() in (".txt", ".tsv", ".csv", ".xlsx"), "expected local TSV, CSV or XLSX")
    if path.suffix.lower() == ".xlsx":
        return _xlsx(path)
    text = path.read_text(encoding="utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text.splitlines()[0] else ",")) if text.strip() else []


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _rows(table: list[list[str]], columns: dict[str, list[str]]) -> list[dict[str, str]]:
    aliases = {key: {_norm(alias) for alias in names} for key, names in columns.items()}
    for i in range(len(table)):
        for depth in (1, 2, 3):
            header_rows = table[i:i + depth]
            width = max(map(len, header_rows), default=0)
            header = [_norm(" ".join(dict.fromkeys(row[j] for row in header_rows if j < len(row) and row[j].strip()))) for j in range(width)]
            positions = {key: [j for j, name in enumerate(header) if name in names] for key, names in aliases.items()}
            if all(positions.values()):
                break
        if all(positions.values()):
            require(all(len(found) == 1 for found in positions.values()), "ambiguous duplicate columns")
            result = []
            for values in table[i + depth:]:
                if not any(value.strip() for value in values):
                    continue
                item = {key: values[indices[0]].strip() if indices[0] < len(values) else "" for key, indices in positions.items()}
                # BLS workbooks carry footnotes after the data. Do not interpret them as rows.
                if "soc" in item and not re.search(SOC, item["soc"]):
                    require(not any(item[key] for key in item if key != "soc"), "malformed occupation row")
                    continue
                result.append(item)
            return result
    raise ValueError("missing upstream columns: " + ", ".join(columns))


def import_signals(kind: str, path: Path, retrieved_at: str, *, sample: bool = False,
                   soc: str | None = None, naics: str | None = None, year: int = 2024) -> dict:
    require(kind in KINDS, "unknown importer")
    require(not any(part.startswith(".env") for part in (*path.parts, *path.resolve().parts)), "not a data file")
    require(sample or ".sample." not in path.name.lower(), "--sample is required for authored sample files")
    _timestamp(retrieved_at)
    source_kind, source = KINDS[kind]
    timing, family = SOURCE_RULES[source_kind]
    rows = []
    if kind == "certifications":
        require(path.suffix.lower() == ".json", "CareerOneStop accepts a saved local JSON response only")
        require(soc is not None, "certifications require the original query --soc")
        _soc(soc)
        require(naics is None or bool(re.fullmatch(r"[0-9]{2,6}", naics)), "invalid query NAICS")
        payload = read_json(path)
        require(isinstance(payload, dict) and isinstance(payload.get("CertList"), list), "expected CareerOneStop CertList response")
        for row in payload["CertList"]:
            require(isinstance(row, dict) and all(isinstance(row.get(key), str) and row[key].strip() for key in ("CertId", "CertName")), "certification needs CertId and CertName")
            organizations = {row[key].strip() for key in ("Organization", "CertOrg") if isinstance(row.get(key), str) and row[key].strip()}
            require(len(organizations) == 1, "certification needs one unambiguous Organization/CertOrg")
            organization = next(iter(organizations))
            rows.append({"soc": soc, "naics": naics, "label": row["CertName"], "native": row,
                         "reading": f"Catalog certification: {row['CertName']}; organization: {organization}. Operator-declared query SOC {soc}; not evidence of employer requirements."})
    elif kind in ("hot-technologies", "emerging-tasks"):
        columns = {"soc": ["O*NET-SOC Code"]}
        if kind == "hot-technologies":
            columns.update(label=["Example", "Workplace Example"], commodity=["Commodity Code", "Element ID"], commodity_title=["Commodity Title", "Element Name"], hot=["Hot Technology"], demand=["In Demand"])
        else:
            columns.update(label=["Task"], task_type=["Task Type"], respondents=["Incumbents Responding"], date=["Date"], domain=["Domain Source"])
        for row in _rows(_table(path), columns):
            _soc(row["soc"])
            require(bool(row["label"]), "blank tool or task")
            if kind == "hot-technologies":
                require(row["hot"] in ("Y", "N") and row["demand"] in ("Y", "N"), "Hot Technology and In Demand must be Y/N")
                if row["hot"] != "Y":
                    continue
                reading = f"Hot Technology: {row['label']}; classification {row['commodity']} ({row['commodity_title']}); In Demand: {row['demand']}. Presence, not an upward time series."
            else:
                require(row["task_type"].lower() == "emerging", "expected Emerging task type")
                require(row["respondents"].isdigit() and int(row["respondents"]) > 0, "invalid incumbent count")
                require(bool(row["date"] and row["domain"]), "emerging task needs native Date and Domain Source")
                reading = f"Emerging task: {row['label']}; native Date: {row['date']}; Domain Source: {row['domain']}; incumbents responding: {row['respondents']}. New in the official task record, not a posting growth estimate."
            rows.append({"soc": row["soc"], "naics": None, "label": row["label"], "native": row, "reading": reading})
    else:
        require(1900 <= year <= 2200, "invalid matrix base year")
        columns = {
            "soc": [f"{year} National Employment Matrix code", "Occupation code", f"{year} National Employment Matrix title and code"],
            "industry": ["Industry code"], "industry_title": ["Industry title"],
            "employment": [f"Employment, {year}", f"{year} Employment"],
            "percent": [f"Percent of occupation employment, {year}", f"Percent of occupation, {year}", f"{year} Percent of occupation"],
        }
        for row in _rows(_table(path), columns):
            code = re.search(SOC, row["soc"])[0]
            _soc(code)
            require(bool(row["industry_title"]) and bool(re.fullmatch(r"[A-Za-z0-9-]{2,12}", row["industry"])), "invalid native industry")
            employment, percent = _number(row["employment"]), _number(row["percent"], percent=True)
            rows.append({"soc": code, "naics": row["industry"], "label": row["industry_title"], "native": row,
                         "reading": f"{year} matrix industry {row['industry']} ({row['industry_title']}): employment {employment:g} thousand; {percent:g}% of occupation employment. Native industry row, not a broad-sector aggregate or a skill trend."})
    records = []
    for row in rows:
        signal = Signal(source, source_kind, family, timing, row.pop("reading"),
                        "up" if kind == "emerging-tasks" else "presence", retrieved_at=retrieved_at).to_dict()
        _validator().evolve(schema={"$defs": _validator().schema["$defs"], "$ref": "#/$defs/Signal"}).validate(signal)
        value = {**row, "signal": signal}
        records.append({"id": "signal." + digest(value)[:24], **value})
    records.sort(key=lambda row: row["id"])
    require(len({row["id"] for row in records}) == len(records), "duplicate source rows")
    return {"schema": "skills-vector.official-signals/1", "data_kind": "sample" if sample else "research",
            "source_kind": source_kind, "retrieved_at": retrieved_at,
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "input_name": path.name,
            "query_scope": {"soc": soc, "naics": naics}, "records": records}


def merge_signals(root: Path, bundles: list[dict], output: Path, *, bindings: list[dict] | None = None,
                  release_id: str | None = None, protected_inputs: tuple[Path, ...] = ()) -> dict:
    records = load_roles(root, release_id)
    indexed = {}
    for bundle in bundles:
        require(bundle.get("schema") == "skills-vector.official-signals/1" and bundle.get("data_kind") in ("sample", "research"), "invalid signal bundle")
        require(bundle["data_kind"] != "sample" or records[0]["data_kind"] == "fixture", "sample signals cannot enter research")
        _timestamp(bundle["retrieved_at"])
        for entry in bundle["records"]:
            require(entry["id"] == "signal." + digest({k: v for k, v in entry.items() if k != "id"})[:24], "signal id/content mismatch")
            require(entry["id"] not in indexed, "duplicate signal id")
            require(entry["signal"]["source_kind"] == bundle["source_kind"] and entry["signal"]["retrieved_at"] == bundle["retrieved_at"], "bundle provenance mismatch")
            indexed[entry["id"]] = entry
    binding_map = {}
    for binding in bindings or []:
        require(set(binding) == {"signal_id", "role_id", "skill_id", "confidence", "rationale"} and isinstance(binding["rationale"], str) and bool(binding["rationale"].strip()), "binding needs signal_id, role_id, skill_id, confidence, rationale")
        require(binding["confidence"] in ("exact", "close", "loose"), "invalid alignment confidence")
        require(binding["signal_id"] in indexed, "binding references unknown signal")
        key = (binding["signal_id"], binding["role_id"], binding["skill_id"])
        require(key not in binding_map, "duplicate binding")
        binding_map[key] = binding
    used_bindings, matched = set(), set()
    for record in records:
        anchors = {anchor["code"] for anchor in record["anchors"] if "onet" in anchor["system"].lower().replace("*", "")}
        for signal_id, entry in sorted(indexed.items()):
            # BLS is parent-SOC context; task/technology links require the exact detailed code.
            matches = (any(code.split(".")[0] == entry["soc"].split(".")[0] for code in anchors)
                       if entry["signal"]["source_kind"] == "bls_industry_matrix" else entry["soc"] in anchors)
            if not matches:
                continue
            require(_timestamp(entry["signal"]["retrieved_at"]).astimezone(timezone.utc).date() <= period_end(record["demand_window"]["id"]), "signal retrieved after the role's as-of window")
            signal = deepcopy(entry["signal"])
            record["foundation"]["sources"].append(signal)
            matched.add(signal_id)
            for skill in record["skill_map"]["skills"]:
                key = (signal_id, record["id"], skill["id"])
                if key not in binding_map:
                    continue
                used_bindings.add(key)
                if binding_map[key]["confidence"] == "loose":
                    continue
                require(signal["family"] in ("O", "M"), "context cannot be bound as skill corroboration")
                skill["evidence"].append(deepcopy(signal))
                if signal["family"] == "O" and "O" not in skill["source_links"]:
                    skill["source_links"].append("O")
                skill["status"] = derive_status([Measurement(**window) for window in skill["windows"]], [Signal(**value) for value in skill["evidence"]])
        RoleRecord.from_dict(record)
    require(used_bindings == set(binding_map), "unmatched role/skill/occupation binding")
    require(bool(matched), "no source rows match selected role anchors")
    receipt = {"schema": "skills-vector.signal-merge/1", "source_release": records[0]["release_id"],
               "bundle_digests": sorted(digest(bundle) for bundle in bundles), "bindings": bindings or [],
               "matched_signals": sorted(matched), "unmatched_signals": sorted(set(indexed) - matched),
               "limits": ["Anchor context is not title equivalence. Skill alignment requires an explicit exact/close operator binding; loose proposals are audit-only.",
                          "No labels, posting counts, edges, sector floors, employment shares or certification requirements are inferred."]}
    return publish_roles(records, output, receipt, root, *protected_inputs)
