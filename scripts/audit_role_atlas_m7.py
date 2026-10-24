"""Reproduce M7 diagnostics/audit agreement from read-only local inputs; no retrieval."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

from skills_vector.market.posting_volume import BOARDS, _Blocks, _native_rows, native_description, extract_sections
from skills_vector.market.posting_sections import StructuralBlocks
from skills_vector.market.role_artifacts import read_json, require, write_json
from skills_vector.market.role_pipeline import EvidenceInputs, triage
from skills_vector.market.role_segments import segment_signals
from skills_vector.market.sources import parse_job_board, us_location_ok

ROOT = Path(__file__).resolve().parents[1]
AUDITS = ROOT / "docs/role-atlas/audits"


def eligible(snapshot: Path) -> list[dict]:
    manifest = read_json(snapshot / "manifest.json")
    output = []
    for board in manifest["boards"]:
        if not board.get("path") or not board.get("listing_complete"):
            continue
        body = (snapshot / board["path"]).read_bytes()
        ats = next(b["ats"] for b in manifest["policy"]["boards"] if BOARDS[b["ats"]].format(token=b["token"]) == board["url"])
        natives = _native_rows(ats, body)
        for p in parse_job_board(board["url"], body)["postings"]:
            roles = [role for role, hints in manifest["policy"]["roles"].items() if any(re.search(r"(?<!\w)" + re.escape(h) + r"(?!\w)", p["title"], re.I) for h in hints)]
            if len(roles) != 1 or not us_location_ok(p["location"]):
                continue
            description, is_html = native_description(ats, natives[p["job_id"]])
            before = extract_sections(description, is_html=is_html, method="rules/1")
            after = extract_sections(description, is_html=is_html)
            structure = None
            if not before["has_responsibilities"] and after["has_responsibilities"]:
                duty = next(e for e in after["expectations"] if e["section"] == "responsibilities")
                if not duty["heading"]:
                    structure = "explicit-current-duty-prose/no-heading"
                elif is_html:
                    original, structural = _Blocks(), StructuralBlocks()
                    for parser in (original, structural):
                        parser.feed(description); parser.close(); parser.flush()
                    old_kind = next((kind for kind, value in original.blocks if value == duty["heading"]), "other")
                    new_kind = next((kind for kind, value in structural.blocks if value == duty["heading"]), "other")
                    structure = "bold-paragraph-heading" if old_kind == "paragraph" and new_kind == "heading" else "plain-paragraph/colon-heading" if old_kind == "paragraph" else "h-tag/new-heading-vocabulary" if old_kind == "heading" else "other-structural-heading"
                else:
                    structure = "plain-text-heading"
            output.append({**p, "role": roles[0], "ats": ats, "employer": board["employer"], "before": before, "after": after, "recovered_structure": structure})
    return sorted(output, key=lambda p: p["job_id"])


def matched_spans(inputs) -> list[dict]:
    grouped = {}
    for posting in inputs.postings:
        for expected in posting["expectations"]:
            if not expected.get("backbone_match"):
                continue
            key = (posting["dedup_key"], expected["expectation_id"])
            entry = grouped.setdefault(key, {"job_id": posting["job_id"], "posting_id": posting["dedup_key"],
                "source_id": posting["source_id"], "source_sha256": posting["source_sha256"], "employer": posting["employer"],
                "expectation_id": expected["expectation_id"], "quote": expected["source_wording"], "matches": []})
            entry["matches"].append(expected["backbone_match"])
    return sorted(grouped.values(), key=lambda e: (e["job_id"], e["quote"]))


def excerpt(quote: str) -> str:
    """At most the first real sentence; full spans stay only in external evidence."""
    return re.split(r"(?<=[.!?])\s+(?=[A-Z])", quote)[0]


def report(snapshot: Path) -> dict:
    current = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release", canonical=False)
    records, audit = current.build()
    legacy = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release", upgrade=False, grouping=False, canonical=False)
    old_records, _ = legacy.build()
    baseline_levels, baseline_reasons, baseline_categories = Counter(), Counter(), Counter()
    for posting in legacy.postings:
        duties = sorted({e["source_wording"] for e in posting["expectations"] if e["section"] == "responsibilities"})
        before = triage(posting, duties, role_scope_rules=False)
        baseline_levels[str(before.level)] += 1
        if before.level is None:
            baseline_reasons[before.reason] += 1
            baseline_categories[triage(posting, duties).reason] += 1
    raw = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release", grouping=False, canonical=False)
    ungrouped, _ = raw.build()
    snapshot_id = read_json(snapshot / "manifest.json")["snapshot_id"]
    pool = eligible(snapshot)
    per_ats, per_role = {}, {}
    for name, field in ((per_ats, "ats"), (per_role, "role")):
        for key in sorted({p[field] for p in pool}):
            selected = [p for p in pool if p[field] == key]
            name[key] = {"eligible": len(selected), "before": sum(p["before"]["has_responsibilities"] for p in selected),
                         "after": sum(p["after"]["has_responsibilities"] for p in selected),
                         "recovered_failure_structure": dict(Counter(p["recovered_structure"] for p in selected if p["recovered_structure"]))}
    sections = read_json(AUDITS / "M7-sections.json")
    levels = read_json(AUDITS / "M8-levels.json")
    heldout = read_json(AUDITS / "M7-r1-sections.json")
    matches = read_json(AUDITS / "M7-matches.json")
    for data in (sections, levels, matches, heldout):
        require(data["source_snapshot_id"] == snapshot_id, "audit pins another source snapshot")
    section_selection = random.Random(sections["seed"]).sample(pool, 20)
    require([p["job_id"] for p in section_selection] == [p["job_id"] for p in sections["cases"]], "section audit selection changed")
    for p, case in zip(section_selection, sections["cases"]):
        require(case["quote"] in p["after"]["description"], "section audit quote changed")
    excluded = {p["job_id"] for p in current.postings} | {c["job_id"] for c in sections["cases"]}
    require(excluded == set(heldout["excluded_job_ids"]), "held-out exclusions changed")
    fresh_pool = [p for p in pool if p["after"]["has_responsibilities"] and p["job_id"] not in excluded]
    fresh_selection = random.Random(heldout["seed"]).sample(fresh_pool, 20)
    require(len(fresh_pool) == heldout["pool_n"] and [p["job_id"] for p in fresh_selection] == [c["job_id"] for c in heldout["cases"]], "held-out selection changed")
    require(hashlib.sha256((ROOT / "src/skills_vector/market/posting_sections.py").read_bytes()).hexdigest() == heldout["extractor_sha256"], "held-out extractor changed")
    for posting, case in zip(fresh_selection, heldout["cases"]):
        require(case["quote"] in posting["after"]["description"] and case["description_sha256"] == posting["after"]["source_sha256"], "held-out quote/source changed")
        spans = posting["after"]["sections"]["responsibilities"]
        require([hashlib.sha256(s.encode()).hexdigest() for s in spans] == case["responsibility_span_hashes"], "held-out spans changed")
        for wrong in case["wrong_spans"]:
            require(hashlib.sha256(spans[wrong["index"]].encode()).hexdigest() == wrong["full_span_sha256"], "held-out error binding changed")
    label_selection = random.Random(levels["seed"]).sample(sorted(current.postings, key=lambda p: p["job_id"]), 40)
    require([p["job_id"] for p in label_selection] == [p["job_id"] for p in levels["cases"]], "level audit selection changed")
    predicted = {p["posting_id"]: p for p in audit["roles"]["account-executive"]["postings"]}
    agreement = 0
    mismatches = []
    for posting, case in zip(label_selection, levels["cases"]):
        require(case["source_sha256"] == posting["source_sha256"] and case["board_sha256"] == posting["board_sha256"], "level audit source changed")
        require(case["title"] == posting["title"] and hashlib.sha256(case["quote"].encode()).hexdigest() == case["quote_sha256"], "level audit title/quote hash changed")
        require(case["quote"] == posting["title"] if case["quote_origin"] == "native_title" else case["quote"] in current.extracts[posting["source_id"]], "level audit quote changed")
        actual = predicted[posting["dedup_key"]]["level"]
        agreement += actual == case["level"]
        if actual != case["level"]:
            mismatches.append({"job_id": posting["job_id"], "label": case["level"], "rule": actual, "reason": predicted[posting["dedup_key"]]["reason"]})
    match_selection = random.Random(matches["seed"]).sample(matched_spans(current), 30)
    for span, case in zip(match_selection, matches["cases"]):
        require((span["job_id"], span["expectation_id"]) == (case["job_id"], case["expectation_id"]), "match audit selection changed")
        require(case["quote"] in span["quote"] and hashlib.sha256(span["quote"].encode()).hexdigest() == case["full_span_sha256"], "match audit source changed")
        require([m["id"] for m in span["matches"]] == case["matched_ids"], "audited grouping changed")
    ae = next(r for r in records if r.id == "role.account-executive")
    old_ae = next(r for r in old_records if r.id == ae.id)
    raw_ae = next(r for r in ungrouped if r.id == ae.id)
    top = sorted((s for s in ae.skill_map.skills if s.id.startswith("skill.onet31.")), key=lambda s: (-s.windows[-1].share, s.id))[:25]
    posting_led = sorted((s for s in ae.skill_map.skills if s.id.startswith("skill.posting.")), key=lambda s: (-s.windows[-1].share, s.id))[:25]
    cross, title_cross = {}, {}
    for posting in current.postings:
        column = "L" + str(predicted[posting["dedup_key"]]["level"]) if predicted[posting["dedup_key"]]["level"] is not None else "unassigned"
        segments = sorted({s["segment"] for s in segment_signals(posting, [], current.skills["segment_scope"], title_only=True)})
        for table, key in ((cross, " + ".join(segments) or "none"), (title_cross, posting["title"])):
            row = table.setdefault(key, dict.fromkeys(("L1", "L2", "L3", "L4", "L5", "unassigned"), 0))
            row[column] += 1
    assigned = sum(p["level"] is not None for p in predicted.values())
    return {"source_snapshot_id": snapshot_id, "q1_per_ats": per_ats, "q1_per_role": per_role,
            "section_spot_check": {"n": 20, "wrong": sum(not c["judged_correct"] for c in sections["cases"])},
            "held_out_section_check": {"seed": heldout["seed"], "pool_n": len(fresh_pool), "n": 20,
                "span_n": sum(len(c["responsibility_span_hashes"]) for c in heldout["cases"]),
                "wrong_postings": sum(not c["judged_correct"] for c in heldout["cases"]), "wrong_spans": sum(len(c["wrong_spans"]) for c in heldout["cases"]),
                "errors": [{"job_id": c["job_id"], "quote": c["quote"], "errors": c["wrong_spans"]} for c in heldout["cases"] if c["wrong_spans"]]},
            "q2": {"top_posting_led": [{"id": s.id, "label": s.label, "postings_share": s.windows[-1].share, "postings": round(s.windows[-1].share * len(predicted)), "employers": s.windows[-1].employers} for s in posting_led], "before_nodes": len(old_ae.skill_map.skills), "reextracted_ungrouped_nodes": len(raw_ae.skill_map.skills), "grouped_nodes": len(ae.skill_map.skills),
                   "audit_n": 30, "judged_correct": sum(c["judged_correct"] for c in matches["cases"]),
                   "top_grouped": [{"id": s.id, "label": s.label, "postings_share": s.windows[-1].share, "employers": s.windows[-1].employers} for s in top]},
            "q3": {"title_segment_level_crosstab": dict(sorted(cross.items())), "title_level_crosstab": dict(sorted(title_cross.items())),
                   "level_counts": audit["roles"]["account-executive"]["postings_per_level"],
                   "unassigned_reasons": dict(Counter(p["reason"] for p in predicted.values() if p["level"] is None)),
                   "label_changes": [{"job_id": c["job_id"], "title": c["title"], "previous_level": c["previous_level"], "level": c["level"], "reason": c["rationale"]} for c in levels["cases"] if c["changed"]],
                   "baseline_level_counts": dict(baseline_levels), "baseline_unassigned_reasons": dict(baseline_reasons),
                   "baseline_67_categories_under_ae_rules_on_original_spans": dict(baseline_categories),
                   "postings": len(predicted), "assigned": assigned, "labeled": 40, "agreement": agreement, "agreement_rate": agreement / 40,
                   "mismatches": mismatches, "honest_stop": assigned / len(predicted) < .5 or agreement / 40 < .85},
            "limits": ["Agent-authored rechecked labels measure conformance to the reviewer segment policy, not independent validity or population accuracy. Reviewer spot-check is still pending.",
                       "The original 70-posting admission cohort is frozen; eligible-board diagnostics use all 405 postings.",
                       "No four-quarter archive, scored forecast, or verified employer-sector data is manufactured."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = report(args.snapshot)
    write_json(args.output, result, inputs=(args.snapshot,))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
