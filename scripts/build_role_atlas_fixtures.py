#!/usr/bin/env python3
"""Rebuild the explicitly illustrative M1 catalog from the frozen prototype snapshot.

This is not an evidence pipeline. It never reads or writes published releases.
See market/role_atlas_contract/README.md for the additional fixture-only assumptions.
"""

from __future__ import annotations

import json
import re

from skills_vector.market.role_atlas import (
    CONTRACT_ROOT, FIXTURE_ROOT, SOURCE_RULES, Direction, RoleRecord, SourceKind,
)

RELEASE_ID = "fixture_role_atlas_2026q3"
SOURCE_KINDS = {
    "posts": "postings", "early": "postings_new_phrasing", "onet": "onet_ratings",
    "emerg": "onet_emerging_tasks", "esco": "esco", "hot": "onet_hot_technologies",
    "sector": "hiring_lab", "aei": "economic_index",
}
SHIFT_IDS = {
    "account-executive": {"aires": "ai-prospecting", "selfsrc": "self-sourced", "consumption": "consumption", "coldcall": "call-volume"},
    "forward-deployed-engineer": {"llmapps": "agents", "evals": "evals"},
    "hr-generalist": {"hrauto": "hr-automation", "analytics": "analytics-gen", "aipolicy": "ai-policy"},
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def signal(source_kind: SourceKind, source: str, reading: str, direction: Direction = "presence", *, as_of: str | None = None) -> dict:
    timing, family = SOURCE_RULES[source_kind]
    return {"source": source, "source_kind": source_kind, "family": family,
            "timing": timing, "reading": reading, "direction": direction, "as_of": as_of}


def counts(postings: int, employers: int, window: str) -> dict:
    return {"postings": postings, "employers": employers, "window": window, "timing": "coincident"}


def build_records(snapshot: dict) -> list[RoleRecord]:
    if snapshot["data_kind"] != "fixture":
        raise ValueError("prototype snapshot must be fixture data")
    records = []
    for role_id, role in snapshot["roles"].items():
        graph = snapshot["graph"][role_id]
        current, previous = role["window"]["current"], role["window"]["previous"]
        skills = []
        for node in graph["nodes"]:
            key, short, label, first, _, _, shares, links = node
            extra = snapshot["signals"][role_id].get(key, {})
            raw_signals = extra.get("sig")
            if raw_signals is None:
                peak = shares.index(max(shares)) + 1
                raw_signals = [["posts", f"Asked in {max(shares)}% of L{peak} postings, steady across two windows."]]
                if "O" in links:
                    raw_signals.append(["onet", f"In the O*NET record for {role['anchors'][0]['code']} (rated {snapshot['onet_year'][role_id]})."])
                for link, reading in (("Ee", "essential"), ("Eo", "optional")):
                    if link in links:
                        raw_signals.append(["esco", f"ESCO lists it as {reading} for the matched occupation."])
            shift_id = SHIFT_IDS[role_id].get(key)
            if shift_id:
                shift = next(item for item in role["shifts"] if item["id"] == shift_id)
                measurements = [(previous, shift["prev"][0] / shift["prev"][1], shift["prev"][2]),
                                (current, shift["cur"][0] / shift["cur"][1], shift["cur"][2])]
                measured_levels = shift["levels"]
            else:
                # The concept explicitly says core peaks are steady; it has no
                # historical per-level counts. These are fixture assumptions.
                measurements = [(window, max(shares) / 100, role["window"]["employers"])
                                for window in (previous, current)]
                measured_levels = [index + 1 for index, share in enumerate(shares) if share >= 25]
            windows = [{"window": window, "share": share, "employers": employers,
                        "max_employer_share": 0.2, "comparison_group": "fixture-prototype",
                        "levels": measured_levels, "timing": "coincident"}
                       for window, share, employers in measurements]
            posting_direction = "up" if measurements[-1][1] > measurements[0][1] else "down" if measurements[-1][1] < measurements[0][1] else "flat"
            evidence = []
            for kind, reading in raw_signals:
                # A newly emerging official task indicates addition; a Hot
                # Technologies listing or high AI use alone is only presence.
                direction = posting_direction if kind in ("posts", "early") else "up" if kind in ("sector", "emerg") else "presence"
                evidence.append(signal(SOURCE_KINDS[kind], snapshot["kinds"][kind]["name"], reading, direction,
                                       as_of=str(snapshot["onet_year"][role_id]) if kind == "onet" else None))
            call = extra.get("call")
            forecast = None if call is None else {
                "id": f"forecast.{role_id}.{key}", "claim": call[0], "probability": call[1] / 100,
                "base_rate": call[2] / 100, "horizon_quarters": 4, "created": current,
                "resolves": call[3], "scored_against": "stratified posting sample",
            }
            skills.append({"id": "skill." + key, "short_label": short, "label": label,
                           "status": extra.get("status", "core"), "first_level": first,
                           "share_by_level": shares, "source_links": links, "windows": windows,
                           "evidence": evidence, "forecast": forecast, "disputed": extra.get("disputed")})
        top = len(role["levels"])
        industries = []
        for index, sector in enumerate(snapshot["industry"][role_id]["list"]):
            profile = None
            if not sector.get("thin"):
                profile = {
                    "share_of_occupation_jobs": sector["share"], "median_pay": sector["pay"],
                    "how_it_differs": sector["differs"], "distinct_duties": sector["duties"],
                    "certifications": [{"name": cert[0], "issuer": cert[1], "asked_as": cert[2],
                                        "posting_share": cert[3] / 100, "source": "CareerOneStop", "timing": "coincident"}
                                       for cert in sector["certs"]],
                    "domain_knowledge": sector["know"],
                    "skill_shares": [{"skill": "skill." + row[0], "share": row[1][index] / 100, "timing": "coincident"}
                                     for row in snapshot["industry"][role_id]["heat"]],
                    "sources": [signal("bls_industry_matrix", "BLS occupation-by-industry matrix", "Employment share for the anchor occupation."),
                                signal("postings", "Job postings", "Duties, credentials and skill shares in this sector."),
                                signal("careeronestop", "CareerOneStop", "Certification Finder matches by O*NET and NAICS.")],
                }
            industries.append({"naics": sector["naics"], "label": sector["label"], "naics_title": sector["naicsTitle"],
                               "sub_industries": sector["sub"], "evidence": counts(sector["postings"], sector["employers"], current),
                               "profile": profile, "reason": "below 30 postings / 8 employers" if profile is None else None})
        foundation = role["foundation"]
        record = {
            "schema": "skills-vector.role/2", "id": "role." + role_id, "label": role["label"],
            "status": role["status"], "publication": "experimental" if role["status"] == "emerging" else "standard",
            "data_kind": "fixture", "release_id": RELEASE_ID, "aliases": role["aliases"], "anchors": role["anchors"],
            "foundation": {"median_pay": foundation["wage"], "pay_note": foundation["wageNote"],
                           "employment": foundation["employment"], "employment_note": foundation["employmentNote"],
                           "outlook_10y": foundation["outlook"], "outlook_note": foundation["outlookNote"],
                           "core_tasks": foundation["tasks"],
                           "sources": [signal("onet_ratings", "O*NET ratings and tasks", f"Anchor tasks rated {snapshot['onet_year'][role_id]}.",
                                              as_of=str(snapshot["onet_year"][role_id])),
                                       signal("bls_projections", "BLS employment projections", "Ten-year headcount outlook, not skill demand.")]},
            "demand_window": {"id": current, "compared_to": previous, "postings": role["window"]["postings"],
                              "employers": role["window"]["employers"], "max_employer_share": 0.2,
                              "timing": "coincident", "window_length": "quarterly"},
            "ladder": {"scale": "shared L1-L5", "track": "individual_contributor",
                       "linked_roles": [{"from_level": role["branch"]["from"], "role": slug(role["branch"]["label"]),
                                         "relation": "people_management_track", "note": role["branch"]["note"]}],
                       "levels": [{"id": f"{role_id}.L{level['L']}", "level": level["L"], "name": level["name"],
                                   "typical_titles": level["titles"], "years_wording": level["years"], "scope": level["scope"],
                                   "owns": level["owns"], "step_up": level["stepUp"], "evidence": counts(level["n"], level["e"], current),
                                   "expectations": [{"id": "exp." + slug(item[0]), "label": item[0],
                                                     "introduced_at": level["L"] if item[1] == "new" else "earlier",
                                                     "basis": "advertised_demand", "timing": "coincident",
                                                     "cite": f"/v1/expectations/exp.{slug(item[0])}@{RELEASE_ID}"} for item in level["exp"]]}
                                  for level in role["levels"]]},
            "skill_map": {"skills": skills,
                          "asked_together": [{"a": "skill." + edge[0], "b": "skill." + edge[1], "lift": edge[2],
                                              "postings": edge[3], "employers": 8, "timing": "coincident"}
                                             for edge in graph["co"] if edge[2] >= 1.5 and edge[3] >= 25],
                          "builds_on": [{"a": "skill." + edge[0], "b": "skill." + edge[1]} for edge in graph["builds"]],
                          "top_level_adds": {"top_level": top, "core_level": graph["mid"],
                                             "skills": [skill["id"] for skill in skills if skill["share_by_level"][top - 1] >= 40
                                                        and skill["share_by_level"][top - 1] - skill["share_by_level"][graph["mid"] - 1] >= 20],
                                             "long_run_bar": signal("onet_ratings", "O*NET level ratings", "The durable proficiency bar, not current posting demand.",
                                                                    as_of=str(snapshot["onet_year"][role_id]))}},
            "industries": industries,
            "observations": [{"id": "observation." + shift["id"], "label": shift["label"], "levels": shift["levels"],
                              "windows": [{"id": window, "mentions": values[0], "postings": values[1], "employers": values[2], "timing": "coincident"}
                                          for window, values in ((previous, shift["prev"]), (current, shift["cur"]))], "note": shift["note"]}
                             for shift in role["shifts"]],
            "limits": ["Fixture data from the illustrative Role Atlas prototype; not research findings.",
                       "Core measurement windows repeat the prototype's steady peak share, using the role-wide employer count as an illustrative assumption, not measured level-specific employer coverage.",
                       "The prototype omits edge employer counts: eight is an explicit fixture-only assumption, not an observation.",
                       "Window employer concentration is the prototype's illustrative 20% cap, not measured employer allocation.",
                       "Counts describe the admitted sample in each window, not the whole market.",
                       "Posting frequency is not importance, proficiency or hiring likelihood.",
                       "Industry splits are only as wide as the employers sampled in each industry.",
                       "Forecast probabilities and base rates are illustrative; no backtest has been run."],
        }
        records.append(RoleRecord.from_dict(record))
    return records


def main() -> None:
    snapshot = json.loads((CONTRACT_ROOT / "prototype.json").read_text(encoding="utf-8"))
    records = build_records(snapshot)
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    for record in records:
        path = FIXTURE_ROOT / f"{record.id.removeprefix('role.')}.json"
        path.write_text(record.to_json(), encoding="utf-8")
        print(f"fixture: {path.name}")


if __name__ == "__main__":
    main()
