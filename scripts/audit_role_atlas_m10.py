"""Read-only M10 acceptance report. Drop text is verified, never printed."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from skills_vector.market.role_artifacts import load_roles, read_json, require
from skills_vector.market.role_publication import EVIDENCE_ROOT, read_audit
from skills_vector.market.skill_rules import SkillRules
from skills_vector.market.structured_career import no_contacts


def report(capture: Path, publication: Path, evidence_root: Path) -> dict:
    manifest = read_json(capture / "manifest.json")
    files = {str(p.relative_to(capture)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in capture.rglob("*") if p.is_file()}
    require(set(files) == set(manifest["files"]) | {"manifest.json"}, "capture inventory changed")
    require(all(files[k] == v for k, v in manifest["files"].items()), "capture bytes changed")
    native = read_json(capture / "postings.json")
    by_source = {p["source_id"]: p for p in native}
    texts = {sid: (capture / p["description_path"]).read_text() for sid, p in by_source.items()}
    records = load_roles(publication / "roles")
    receipt = read_json(publication / "release.json")
    audit, status = read_audit(publication, evidence_root)
    require(audit is not None and status == "audit verified locally", "audit not available locally")
    rules = SkillRules()
    result = {"release_id": receipt["release_id"], "capture_files": len(files), "roles": {}}
    links = drops = 0
    observed_ids = set()
    for record in records:
        slug = record["id"].removeprefix("role.")
        role_audit = audit["roles"][slug]
        rows = role_audit["postings"]
        employers = {p["employer"] for p in rows}
        totals = Counter(p["employer"].casefold().strip() for p in rows)
        counts = Counter()
        emptied = empty_before = 0
        skill_hits = defaultdict(set)
        employer_of = {}
        for posting in rows:
            observed_ids.add(posting["posting_id"])
            employer_of[posting["posting_id"]] = posting["employer"].casefold().strip()
            filt = posting["skill_filter"]
            require(filt["method"] == posting["extraction_method"] == "rules/3", "wrong projection method")
            require(filt["emptied"] == (filt["candidates_before"] > 0 and filt["candidates_after"] == 0), "uncounted empty posting")
            require(filt["empty_before"] == (filt["candidates_before"] == 0), "wrong original-empty count")
            require(Counter(d["reason"] for d in filt["drops"]) == Counter(filt["drops_by_reason"]), "drop count mismatch")
            counts.update(filt["drops_by_reason"])
            emptied += filt["emptied"]
            empty_before += filt["empty_before"]
            years = [e["value"] for e in posting["qualification_evidence"] if e["reason"] == "years" and e["section"] != "preferred"]
            require(posting["min_years"] == (min(years) if years else None), "wrong weak experience bound")
            require(posting["education"] == sorted({e["value"] for e in posting["qualification_evidence"] if e["reason"] == "education"}), "education mismatch")
            for dropped in [*filt["drops"], *posting["qualification_evidence"]]:
                text = texts[dropped["source_id"]]
                require(text[dropped["start"]:dropped["end"]] == dropped["quote"] and
                        hashlib.sha256(text.encode()).hexdigest() == dropped["source_sha256"], "drop/field receipt not source-bound")
            drops += len(filt["drops"])
            for link in posting["skill_links"]:
                text = texts[link["source_id"]]
                require(text[link["start"]:link["end"]] == link["phrase"] and
                        hashlib.sha256(text.encode()).hexdigest() == link["source_sha256"], "skill link not source-bound")
                skill_hits[link["skill"]].add(posting["posting_id"])
                links += 1
        quality = role_audit["rules3"]
        require(dict(counts) == quality["drops_by_reason"] and emptied == quality["postings_emptied"] and
                empty_before == quality["postings_empty_before"], "role counts disagree with postings")
        require(receipt["roles"][record["id"]]["skill_filter"] == quality, "publication summary mismatch")
        skills = record["skill_map"]["skills"]
        expected = sorted(skills, key=lambda s: (-s["windows"][-1]["employers"], -s["windows"][-1]["employer_weighted_share"], s["id"]))
        require(skills == expected, "rank mismatch")
        for skill in skills:
            hits = skill_hits[skill["id"]]
            per_employer = Counter(employer_of[p] for p in hits)
            weighted = float(sum((Fraction(per_employer[e], n) for e, n in totals.items()), Fraction()) / len(totals))
            window = skill["windows"][-1]
            require(window["share"] == len(hits) / len(rows) and window["employers"] == len(per_employer)
                    and abs(window["employer_weighted_share"] - weighted) < 1e-15, "ranking/posting measurement mismatch")
        top = []
        for skill in skills[:20]:
            require(not any(rules.rejection(skill["label"], e) for e in employers), "top-20 node matches non-skill table")
            w = skill["windows"][-1]
            top.append({"id": skill["id"], "label": skill["label"], "employers": w["employers"],
                        "employer_weighted_share": w["employer_weighted_share"], "posting_share": w["share"]})
        result["roles"][slug] = {"postings": len(rows), "employers": len(employers), "nodes": len(skills),
            "levels": role_audit["postings_per_level"], "quality": quality,
            "postings_with_min_years": sum(p["min_years"] is not None for p in rows),
            "postings_with_education": sum(bool(p["education"]) for p in rows),
            "top_20": top, "sector_note": next(n for n in record["limits"] if n.startswith("Sample sectors: "))}
    require(observed_ids == {p["dedup_key"] for p in native}, "admitted cohort changed")
    require(files == {str(p.relative_to(capture)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in capture.rglob("*") if p.is_file()}, "capture changed during audit")
    require(no_contacts(result), "report contains non-data content")
    result.update(postings=len(observed_ids), source_bound_links=links, source_bound_drops=drops)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=EVIDENCE_ROOT)
    args = parser.parse_args()
    print(json.dumps(report(args.snapshot, args.publication, args.evidence_root), ensure_ascii=False, indent=2))
