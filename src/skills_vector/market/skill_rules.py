"""rules/3 projection filter. Native captures are verified first and never edited."""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re

from .role_artifacts import read_json, require

METHOD = "rules/3"
TABLE = Path(__file__).with_name("role_atlas_mappings") / "non_skills.json"


class SkillRules:
    def __init__(self, path: Path = TABLE):
        self.value = read_json(path)
        require(self.value.get("schema") == "role-atlas.non-skills/1" and self.value.get("method") == METHOD,
                "unsupported skill filter table")
        rows = self.value["rows"]
        require(len({r["id"] for r in rows}) == len(rows) and all(r["rationale"].strip() for r in rows),
                "duplicate or unreviewed skill filter rule")
        self.rows = rows
        self.qual = self.value["qualification"]
        self.education = [(r["value"], re.compile(r["pattern"], re.I)) for r in self.qual["education"]]
        self.years = re.compile(self.qual["years"], re.I)
        self.start = re.compile(self.qual["start"], re.I)
        self.prefix = re.compile(self.qual["domain_prefix"], re.I)
        self.suffix = re.compile(self.qual["domain_suffix"], re.I)
        self.compiled = {}

    def rejection(self, text: str, employer: str, after: str = "") -> tuple[str, str] | None:
        for row in self.rows:
            key = (row["id"], employer)
            if key not in self.compiled:
                self.compiled[key] = re.compile(row["pattern"].replace("{employer}", re.escape(employer)), re.I)
            if self.compiled[key].search(text) and (not row.get("after") or re.search(row["after"], after, re.I)):
                return row["reason"], row["id"]
        return None

    def process(self, posting: dict, description: str) -> None:
        original = posting["expectations"]
        output, drops, fields = [], [], []
        education, years = set(), []

        def receipt(expected, a, b, reason, rule):
            start = expected["start"] + a
            quote = expected["source_wording"][a:b]
            require(description[start:start + len(quote)] == quote, "rules/3 drop is not an exact source span")
            drops.append({"reason": reason, "rule": rule, "quote": quote, "source_id": posting["source_id"],
                          "expectation_id": expected["expectation_id"],
                          "source_sha256": expected["source_sha256"], "start": start, "end": start + len(quote)})

        def domain(expected, a, b):
            text = expected["source_wording"]
            a += self.prefix.match(text[a:b]).end()
            ending = self.suffix.search(text[a:b])
            if ending:
                b = a + ending.start()
            if a >= b or not text[a:b].strip():
                return
            rejection = self.rejection(text[a:b], posting["employer"], description[expected["start"] + b:])
            if rejection:
                receipt(expected, a, b, *rejection)
                return
            start = expected["start"] + a
            identity = posting["source_id"] + "|" + str(start) + "|" + text[a:b]
            output.append({**expected, "source_wording": text[a:b], "start": start, "end": expected["start"] + b,
                           "expectation_id": "exp.rules3." + hashlib.sha256(identity.encode()).hexdigest()[:24],
                           "parent_expectation_id": expected["expectation_id"],
                           "parent_quote_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "basis": "employer_preference" if expected.get("section") == "preferred" else "employer_requirement",
                           "section": "preferred" if expected.get("section") == "preferred" else "requirements",
                           "dimension": "capability", "projection_extraction": METHOD})

        for expected in original:
            text = expected["source_wording"]
            require(description[expected["start"]:expected["end"]] == text, "rules/3 candidate is not source-bound")
            rejection = self.rejection(text, posting["employer"], description[expected["end"]:])
            if rejection:
                receipt(expected, 0, len(text), *rejection)
                continue
            markers = []
            if expected.get("section") in ("requirements", "preferred") or self.start.search(text):
                for value, pattern in self.education:
                    for hit in pattern.finditer(text):
                        markers.append((hit.start(), hit.end(), "education", value))
                for hit in self.years.finditer(text):
                    markers.append((hit.start(), hit.end(), "years", int(hit["years"])))
            if not markers:
                output.append(expected)
                continue
            cursor = 0
            for a, b, reason, value in sorted(markers):
                if a < cursor:
                    continue
                if cursor < a:
                    domain(expected, cursor, a)
                receipt(expected, a, b, reason, "qualification." + reason)
                fields.append({**drops[-1], "value": value, "section": expected.get("section")})
                if reason == "education":
                    education.add(value)
                elif expected.get("section") != "preferred":
                    years.append(value)
                cursor = b
            if cursor < len(text):
                domain(expected, cursor, len(text))
        # Source duplicates cannot multiply either counts or the candidate set.
        drops = list({(d["start"], d["end"], d["reason"]): d for d in drops}.values())
        posting.update(native_extraction_method=posting["extraction_method"], extraction_method=METHOD,
                       expectations=output, min_years=min(years) if years else None, education=sorted(education),
                       qualification_evidence=fields, skill_filter={
                           "method": METHOD, "candidates_before": len(original), "candidates_after": len(output),
                           "drops_by_reason": dict(sorted(Counter(d["reason"] for d in drops).items())),
                           "emptied": bool(original) and not output, "empty_before": not original, "drops": drops})
