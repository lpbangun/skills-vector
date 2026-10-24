"""Read-only flattened-description measurement; bounded samples, never full bodies."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from skills_vector.market.posting_volume import extract_sections
from skills_vector.market.structured_career import no_contacts


def measure(root: Path, seed: int = 20261024) -> dict:
    # The named capture is the only external input. Never read the adjacent roster.
    root = root.resolve()
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(root.rglob("*")) if p.is_file()}
    manifest = json.loads((root / "manifest.json").read_text())
    boards = {b.get("path"): b for b in manifest["boards"] if b.get("path")}
    rows = []
    for path in sorted((root / "boards").glob("*.json")):
        native = json.loads(path.read_text())
        if not isinstance(native, dict) or native.get("schema") != "skills-vector.jsonld-board/1":
            continue
        board = boards[str(path.relative_to(root))]
        for job in native["jobs"]:
            rows.append({"employer": board["employer"], **job})
    rows.sort(key=lambda row: (row["employer"], row["url"], row["id"]))
    results = [extract_sections(row["description"], employer=row["employer"]) for row in rows]
    baseline = [extract_sections(row["description"], flat_headings=False) for row in rows]
    spans = 0
    for row, result in zip(rows, results):
        assert result["description"] == row["description"]
        assert result["source_sha256"] == hashlib.sha256(row["description"].encode()).hexdigest()
        for quote in result["expectations"]:
            assert row["description"][quote["start"]:quote["end"]] == quote["quote"]
            assert quote["source_sha256"] == result["source_sha256"]
            spans += 1
    samples = []
    for index in random.Random(seed).sample(range(len(rows)), min(15, len(rows))):
        row, result = rows[index], results[index]
        first = {}
        for quote in result["expectations"]:
            if quote["section"] not in first:
                first[quote["section"]] = {
                    "start": quote["start"], "end": quote["end"], "length": len(quote["quote"]),
                    "excerpt": quote["quote"][:160], "truncated": len(quote["quote"]) > 160,
                }
        samples.append({"employer": row["employer"], "identifier": row["identifier"],
                        "id": row["id"], "title": row["title"],
                        "method": result.get("extraction_method", "rules/2"),
                        "has_responsibilities": result["has_responsibilities"],
                        "boundaries": [{k: b[k] for k in ("heading", "section", "start")} for b in result.get("section_boundaries", [])],
                        "first_spans": first})
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob("*")) if p.is_file()}
    assert before == after, "readonly capture changed during measurement"
    report = {
        "capture": str(root), "readonly_files": len(before),
        "capture_tree_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "postings": len(rows), "baseline_responsibilities": sum(r["has_responsibilities"] for r in baseline),
        "responsibilities": sum(r["has_responsibilities"] for r in results),
        "responsibility_percent": round(100 * sum(r["has_responsibilities"] for r in results) / len(rows), 2),
        "methods": dict(Counter(r.get("extraction_method", "rules/2") for r in results)),
        "source_bound_spans": spans, "failures": [
            {"employer": row["employer"], "identifier": row["identifier"], "title": row["title"]}
            for row, result in zip(rows, results) if not result["has_responsibilities"]],
        "seed": seed, "sample_order": "employer, page URL, native id; Random(seed).sample(range(n), 15)",
        "samples": samples,
    }
    if not no_contacts(report):
        raise ValueError("measurement output rejected by metadata privacy guard")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--seed", type=int, default=20261024)
    args = parser.parse_args()
    print(json.dumps(measure(args.capture, args.seed), ensure_ascii=False, indent=2))
