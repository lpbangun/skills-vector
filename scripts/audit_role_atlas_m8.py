"""Offline M8 match-audit replay and per-role before/after summaries."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from skills_vector.market.role_artifacts import read_json, require, write_json
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.role_publication import read_audit

ROOT = Path(__file__).resolve().parents[1]


def match_pool(audit: dict, native: list[dict]) -> list[dict]:
    byid = {p["dedup_key"]: p for p in native}
    spans = {}
    for role, data in sorted(audit["roles"].items()):
        for p in data["postings"]:
            for link in p["skill_links"]:
                match = link.get("term_match") or link.get("backbone_match")
                if not match or match["kind"] not in ("posting-term", "software-example"):
                    continue
                key = (role, p["posting_id"], link["expectation_id"])
                span = spans.setdefault(key, {"role": role, "job_id": byid[p["posting_id"]]["job_id"], "posting_id": p["posting_id"],
                    "expectation_id": link["expectation_id"], "source_id": p["source_id"], "source_sha256": link["source_sha256"],
                    "quote": link["phrase"], "start": link["start"], "end": link["end"], "matches": []})
                span["matches"].append({k: match[k] for k in ("id", "label", "kind", "family", "method", "matched_text", "confidence")})
    for span in spans.values(): span["matches"].sort(key=lambda m: m["id"])
    return sorted(spans.values(), key=lambda p: (p["role"], p["posting_id"], p["start"], p["expectation_id"]))


def selections(pool: list[dict], roles: list[str]) -> dict:
    sets = {"global": {"seed": 20261113, "pool": pool}}
    for i, role in enumerate(sorted(roles)):
        sets[role] = {"seed": 20261114 + i, "pool": [p for p in pool if p["role"] == role]}
    return {key: {"seed": data["seed"], "pool_n": len(data["pool"]),
                  "cases": random.Random(data["seed"]).sample(data["pool"], min(30, len(data["pool"])))} for key, data in sets.items()}


def top_nodes(record: dict) -> list[dict]:
    skills = sorted(record["skill_map"]["skills"], key=lambda s: (-s["windows"][-1]["share"],
        0 if s["id"].startswith(("skill.onet31.", "skill.posting.term.")) else 1, s["id"]))[:20]
    return [{"id": s["id"], "label": s["label"], "share": s["windows"][-1]["share"], "employers": s["windows"][-1]["employers"], "source_links": s["source_links"]} for s in skills]


def report(snapshot: Path, projection: Path, baseline: Path, cases: Path) -> dict:
    actual, audit_status = read_audit(projection)
    if actual is None:
        return {"release_id": read_json(projection / "release.json")["release_id"], "audit_status": audit_status}
    inputs = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release")
    records, replay = inputs.build()
    require(replay == actual, "projection no longer matches current inputs/producer")
    selection = selections(match_pool(actual, read_json(snapshot / "postings.json")), list(actual["roles"]))
    judged = read_json(cases)
    require(judged["source_snapshot_id"] == read_json(snapshot / "manifest.json")["snapshot_id"], "match audit pins another snapshot")
    require(judged["producer_hashes"] == {k: actual["input_hashes"][k] for k in ("role_terms.py", "software_aliases.json", "software_examples.json", "emerging_terms.json", "role_quotes.py")}, "match audit algorithm/tables changed")
    checks = {}
    for key, data in selection.items():
        prior = judged["selections"][key]
        require((data["seed"], data["pool_n"]) == (prior["seed"], prior["pool_n"]), "match audit frame changed")
        require(len(data["cases"]) == len(prior["cases"]), "match audit count changed")
        for span, case in zip(data["cases"], prior["cases"]):
            require(all(span[k] == case[k] for k in span), "audited span/matches changed")
            text = inputs.extracts[span["source_id"]]
            require(text[span["start"]:span["end"]] == span["quote"] and hashlib.sha256(text.encode()).hexdigest() == span["source_sha256"], "audit quote/hash binding changed")
            require(type(case["judged_correct"]) is bool and case["rationale"].strip(), "missing manual judgment")
        correct = sum(c["judged_correct"] for c in prior["cases"])
        checks[key] = {"seed": data["seed"], "pool_n": data["pool_n"], "n": len(prior["cases"]), "correct": correct,
                       "rate": correct / len(prior["cases"]) if prior["cases"] else None,
                       "errors": [c for c in prior["cases"] if not c["judged_correct"]]}
    summaries = {}
    for record in records:
        current = record.to_dict(); slug = record.id.removeprefix("role.")
        old = read_json(baseline / "roles" / (slug + ".json")); a = actual["roles"][slug]
        summaries[slug] = {"before_nodes": len(old["skill_map"]["skills"]), "after_nodes": len(current["skill_map"]["skills"]),
            "postings": current["demand_window"]["postings"], "employers": current["demand_window"]["employers"], "levels": a["postings_per_level"],
            "official_nodes": sum("O" in s["source_links"] for s in current["skill_map"]["skills"]),
            "canonical_posting_terms": sum(s["id"].startswith("skill.posting.term.") for s in current["skill_map"]["skills"]),
            "publication_notice_exclusions": a["projection_excluded_spans"], "top20": top_nodes(current),
            "overview_visible": sum(s["windows"][-1]["employers"] >= 3 for s in current["skill_map"]["skills"]) if len(current["skill_map"]["skills"]) > 100 else len(current["skill_map"]["skills"])}
    return {"audit_status": audit_status, "source_snapshot_id": judged["source_snapshot_id"], "release_id": actual["release_id"], "roles": summaries, "audit": checks,
            "limits": ["Coding-agent manual lexical audit, not independent expert adjudication or a population accuracy estimate.",
                       "Thirty cases per role where available; all available Growth cases and no padded HR cases. Global 30 is the T2 gate.",
                       "Top-20 ties prefer canonical nodes, then stable id; no share/employer adjustment.",
                       "The canonical frame is tech-heavy and cross-sectional, not a market census or new-job flow."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "docs/role-atlas/audits/M8-matches.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = report(args.snapshot, args.projection, args.baseline, args.cases)
    write_json(args.output, value, inputs=(args.snapshot, args.projection, args.baseline, args.cases))
    print(json.dumps(value, indent=2))


if __name__ == "__main__": main()
