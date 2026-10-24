"""Reviewed, role-specific customer-segment scope, not seniority/title heuristics."""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_POLICY = Path(__file__).with_name("role_atlas_mappings") / "ae_segments.json"
# An owned-customer duty must contain both an action and an account/segment object.
ACTION = re.compile(r"\b(?:own(?:s|ing)?|manag(?:e|es|ing)|sell(?:s|ing)?|clos(?:e|es|ing)|build(?:s|ing)?|develop(?:s|ing)?|creat(?:e|es|ing)|driv(?:e|es|ing)|expand(?:s|ing)?|sourc(?:e|es|ing)|prospect(?:s|ing)?|target(?:s|ing)?|handl(?:e|es|ing)|cover(?:s|ing)?|serv(?:e|es|ing)|acquir(?:e|es|ing)|win|engag(?:e|es|ing)|land|grow(?:s|ing)?)\b", re.I)
OBJECT = re.compile(r"^[\s-]+(?:(?:new|existing|potential|business|customer|client|sales|POS|strategic|named|global|key)[\s-]+)*(?:accounts?|customers?|businesses|companies|clients?|organizations?|prospects?|book|segment)\b", re.I)
GENERIC_OBJECT = re.compile(r"^[\s-]+(?:plans?|planning|strateg(?:y|ies)|teams?|software|platform|technology|tools?|solutions?|records|management|meetings|executives?|experience|pain|decision|stakeholders?)\b", re.I)
NEGATIVE = re.compile(r"\b(?:not|without|outside|previous(?:ly)?|prior|experience|background)\b", re.I)


def load_policy(path: Path = DEFAULT_POLICY) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema") != "skills-vector.role-segment-scope/1" or data.get("role") != "account-executive" or not data.get("source_note", "").strip():
        raise ValueError("unsupported/unreviewed segment policy")
    seen = set()
    for row in data["rows"]:
        if row["segment"] in seen or row["level"] not in (2, 3, 4, 5) or not row["rationale"].strip():
            raise ValueError("invalid segment row")
        seen.add(row["segment"]); re.compile(row["pattern"], re.I)
    if not seen:
        raise ValueError("empty segment policy")
    return data


def segment_signals(posting: dict, quotes: list[str], policy: dict | None = None, *, title_only: bool = False) -> list[dict]:
    policy = policy if policy is not None else load_policy()
    if posting.get("occupation_slug") != policy["role"]:
        return []
    output = []
    texts = [("native_title", posting.get("title", ""))]
    if not title_only:
        texts.extend(("current_duty", quote) for quote in sorted(set(quotes)))
    for origin, quote in texts:
        # Never join clauses: an ownership verb in one sentence is not another's scope.
        clauses = [quote] if origin == "native_title" else re.split(r"[.!?;]\s+", quote)
        for clause in clauses:
            candidates = sorted(((hit.start(), hit.end(), row) for row in policy["rows"] for hit in re.finditer(row["pattern"], clause, re.I)), key=lambda item: (-(item[1] - item[0]), item[0], item[2]["segment"]))
            used = []
            for start, end, row in candidates:
                if any(start < right and end > left for left, right in used):
                    continue
                used.append((start, end))
                if origin == "current_duty":
                    prefix = clause[max(0, start - 160):start]
                    suffix = clause[end:end + 80]
                    # Geographic territory and strategic account plans are generic duties,
                    # not a named customer segment. Native segment titles still qualify.
                    obj = OBJECT.search(suffix)
                    compound_object = row["segment"] == "public-sector-named-account"
                    if not ACTION.search(prefix) or NEGATIVE.search(prefix) or (not compound_object and (not obj or GENERIC_OBJECT.search(suffix[obj.end():]))):
                        continue
                output.append({"level": row["level"], "segment": row["segment"], "quote": quote, "matched_text": clause[start:end],
                               "origin": origin, "source_id": posting.get("source_id"), "board_sha256": posting.get("board_sha256"),
                               "rationale": row["rationale"]})
    return output
