#!/usr/bin/env python3
"""Minimal offline agent consumer for a Structured Job Analysis release.

Example:
  python examples/consume_structured_job_analysis.py outputs/structured-job-analysis-round1/release.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="skills-vector.poc-output.v1 JSON")
    args = parser.parse_args()
    release = json.loads(args.release.read_text(encoding="utf-8"))
    if release.get("schema_version") != "skills-vector.poc-output.v1":
        raise SystemExit("unsupported release schema")
    if release.get("method") != "structured-job-analysis":
        raise SystemExit("this example expects method A")
    if release.get("demand_layer", {}).get("claims_allowed") is not False:
        raise SystemExit("refusing a demand layer that does not block unsupported claims")

    print(f"Role: {release['role']['canonical_title']}")
    print(f"Run: {release['run']['run_id']} / candidate {release['run']['candidate_revision']}")
    print("Tasks:")
    for unit in release["work_units"]:
        if unit["kind"] != "task" or unit["method_fields"].get("classification") != "common_core":
            continue
        citations = "; ".join(
            f"{item['source_id']} ({item['locator']}): {item['quote']}"
            for item in unit["evidence"]
        )
        links = ", ".join(
            link["competency_unit_id"]
            for link in unit["method_fields"]["task_competency_links"]
        )
        print(f"- {unit['unit_id']}: {unit['statement']}")
        print(f"  competencies: {links}")
        print(f"  evidence: {citations}")
        print(f"  phrase hits: {unit['demand']['posting_count']}/{unit['demand']['postings_denominator']} dev postings")
    demand = release["demand_layer"]
    print("Demand claims allowed:", demand["claims_allowed"])
    print("Demand caveat:", demand["statements"][2])
    print("Unlinked/uncertain:", len(release["unlinked_or_ambiguous"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
