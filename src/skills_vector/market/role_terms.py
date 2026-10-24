"""Case-aware literal software/example and posting-term grouping. No models or IO retrieval."""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from .role_artifacts import digest, read_json, require, write_json

ROOT = Path(__file__).with_name("role_atlas_mappings")
SOFTWARE = ROOT / "software_aliases.json"
EXAMPLES = ROOT / "software_examples.json"
TERMS = ROOT / "emerging_terms.json"
ZIP_SHA256 = "6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9"


def derive_examples(database: Path, output: Path, aliases: Path = SOFTWARE) -> dict:
    root = database / "db_31_0_text" if (database / "db_31_0_text").is_dir() else database
    path = root / "Software Skills.txt"
    with path.open(encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        require({"O*NET-SOC Code", "Workplace Example", "Element ID", "Element Name"} <= set(reader.fieldnames or []), "missing native Software Skills columns")
        rows = list(reader)
    bindings = []
    for alias in read_json(aliases)["rows"]:
        matches = {(r["O*NET-SOC Code"], r["Element ID"], r["Element Name"], r["Workplace Example"]) for r in rows if r["Workplace Example"] in alias["source_examples"]}
        require({r[3] for r in matches} == set(alias["source_examples"]), "alias lacks an exact native example: " + alias["id"])
        # One deterministic native binding per example/category; not every occupation row.
        selected = {}
        for soc, element, name, example in sorted(matches):
            selected.setdefault((example, element), {"soc": soc, "element_id": element, "element_name": name, "workplace_example": example})
        bindings.append({"id": alias["id"], "bindings": list(selected.values())})
    value = {"schema": "skills-vector.software-examples/1", "version": "31.0", "source_zip_sha256": ZIP_SHA256,
             "source_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "aliases_sha256": hashlib.sha256(aliases.read_bytes()).hexdigest(),
             "source_note": "Derived literal example bindings, not occupation-task membership, Hot Technology flags or proficiency. Example keys are project identifiers, not official element IDs.",
             "attribution": "O*NET 31.0 Database, U.S. Department of Labor, Employment and Training Administration. CC BY 4.0. https://www.onetcenter.org/database.html",
             "examples": bindings}
    write_json(output, value, inputs=(database, aliases))
    return {"examples": len(bindings), "bindings": sum(len(r["bindings"]) for r in bindings), "sha256": digest(value)}


class ToolTerms:
    def __init__(self, software: Path = SOFTWARE, examples: Path = EXAMPLES, terms: Path = TERMS):
        aliases, self.examples, lexicon = read_json(software), read_json(examples), read_json(terms)
        require(aliases["schema"] == "skills-vector.software-aliases/1" and lexicon["schema"] == "skills-vector.posting-terms/1", "unsupported lexical table")
        require(self.examples["schema"] == "skills-vector.software-examples/1" and self.examples["source_zip_sha256"] == ZIP_SHA256, "unsupported software provenance")
        require(self.examples["aliases_sha256"] == hashlib.sha256(software.read_bytes()).hexdigest(), "software alias/binding hash mismatch")
        self.bindings = {r["id"]: r["bindings"] for r in self.examples["examples"]}
        self.rows = []
        ids, alias_ids = set(), {}
        for family, rows in (("O", aliases["rows"]), ("P", lexicon["rows"])):
            for row in rows:
                require(row["id"] not in ids and row["rationale"].strip() and row["aliases"], "duplicate id or missing lexical rationale/aliases")
                ids.add(row["id"])
                require(family == "O" or row.get("family") == "P", "posting lexicon must stay P")
                require(family == "P" or bool(self.bindings.get(row["id"])), "software lacks native example binding")
                for alias in row["aliases"]:
                    word = alias["text"]
                    require(word.strip() == word and bool(word) and type(alias["case_sensitive"]) is bool, "invalid alias")
                    require(family == "P" or len(word) >= 3 or word in aliases["short_allowlist"], "short software alias not explicitly allowed")
                    key = (word if alias["case_sensitive"] else word.casefold(), alias["case_sensitive"])
                    require(key not in alias_ids or alias_ids[key] == row["id"], "ambiguous lexical alias")
                    alias_ids[key] = row["id"]
                    pattern = re.compile(r"(?<![\w&])" + re.escape(word) + r"(?![\w&])", 0 if alias["case_sensitive"] else re.I)
                    context = re.compile(alias["requires_context"], re.I) if alias.get("requires_context") else None
                    forbidden = re.compile(alias["forbidden_suffix"], re.I) if alias.get("forbidden_suffix") else None
                    prefix = re.compile(alias["forbidden_prefix"], re.I) if alias.get("forbidden_prefix") else None
                    self.rows.append((family, row, pattern, context, forbidden, prefix))
        self.fingerprint = digest({"aliases": aliases, "examples": self.examples, "terms": lexicon,
                                   "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})

    def match(self, text: str) -> list[dict]:
        candidates = []
        for family, row, regex, context, forbidden, prefix in self.rows:
            if context and not context.search(text):
                continue
            for hit in regex.finditer(text):
                if (forbidden and forbidden.match(text[hit.end():])) or (prefix and prefix.search(text[max(0, hit.start() - 100):hit.start()])):
                    continue
                candidates.append((hit.start(), hit.end(), family, row))
        output, occupied, seen = [], {"O": [], "P": []}, set()
        for start, end, family, row in sorted(candidates, key=lambda c: (-(c[1] - c[0]), c[0], c[3]["id"])):
            # Prefer explicit compound products (e.g. React Native), without duplicate roots.
            if (family, row["id"]) in seen or (family == "O" and any(start < b and end > a for a, b in occupied[family])):
                continue
            seen.add((family, row["id"])); occupied[family].append((start, end))
            value = {"id": "onet31:example:" + row["id"] if family == "O" else row["id"], "label": row["label"],
                     "kind": "software-example" if family == "O" else "posting-term", "family": family,
                     "method": "case-aware-software-alias" if family == "O" else "reviewed-posting-term-alias",
                     "matched_text": text[start:end], "start": start, "end": end, "confidence": "close",
                     "rationale": row["rationale"]}
            if family == "O":
                value.update(source_version="31.0", source_zip_sha256=ZIP_SHA256, native_bindings=self.bindings[row["id"]])
            output.append(value)
        return sorted(output, key=lambda m: (m["start"], m["id"]))
