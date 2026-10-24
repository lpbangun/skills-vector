"""Deterministic materialized light views, independently bound to published role bytes."""
from copy import deepcopy


def overview(record: dict) -> dict:
    skills = record["skill_map"]["skills"]
    info = {"employer_floor": 3 if len(skills) > 100 else 0, "total": len(skills), "tails": [], "deferred": []}
    light = deepcopy(record)
    if info["employer_floor"]:
        light["skill_map"]["skills"] = [s for s in light["skill_map"]["skills"] if s["windows"][-1]["employers"] >= 3]
        for status in ("core", "rising", "early", "fading"):
            tail = [{"id": s["id"], "label": s["label"]} for s in skills if s["status"] == status and s["windows"][-1]["employers"] < 3]
            info["tails"].append({"status": status, "count": len(tail), "skills": tail})
        for level in light["ladder"]["levels"]:
            level["expectations"] = []
        info["deferred"] = ["ladder.levels[].expectations"]
    return {"role": light, "overview": info, "view": "overview"}
