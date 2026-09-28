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

    run = release["run"]
    publication_status = run.get("publication_status", "legacy_status_unavailable")
    accepted = run.get("model_review_accepted") is True
    if accepted != (publication_status == "model_reviewed_release"):
        raise SystemExit("inconsistent model-review publication marker")
    print(f"Role: {release['role']['canonical_title']}")
    print(f"Run: {run['run_id']} / candidate {run['candidate_revision']}")
    print(f"Publication status: {publication_status}; accepted model review: {accepted}")

    units_by_id = {unit["unit_id"]: unit for unit in release["work_units"]}
    priorities = release.get("skill_priorities", [])
    ranks = [priority.get("rank") for priority in priorities]
    if ranks != list(range(1, len(priorities) + 1)):
        raise SystemExit("skill priorities are not in stable rank order")
    print("Prioritized skills:")
    for priority in priorities:
        if not set(priority["related_work_unit_ids"]) <= set(units_by_id):
            raise SystemExit(f"skill priority {priority.get('skill_id')} references an unknown work unit")
        print(f"- {priority['rank']}. {priority['skill']} — {priority['evidence_strength']['label']}")

    transparency = release.get("extraction_transparency", {})
    admitted_sources = transparency.get("admitted_sources", [])
    provenance_ids = {source["source_id"] for source in release.get("provenance", [])}
    if {source.get("source_id") for source in admitted_sources} != provenance_ids:
        raise SystemExit("source-decision ledger does not reconcile to release provenance")
    decision_counts = transparency.get("decision_counts", {})
    print(
        "Evidence decisions:",
        len(admitted_sources),
        "admitted sources;",
        decision_counts.get("included", 0), "included,",
        decision_counts.get("excluded", 0), "excluded,",
        decision_counts.get("unresolved", 0), "unresolved.",
    )
    print("Secondary evidence view:", release["guide"].get("secondary_evidence_path", "not recorded"))
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
