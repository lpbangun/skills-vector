"""Deterministic materialized light views, independently bound to published role bytes."""
from copy import deepcopy


def overview(record: dict, *, tail_label_limit: int | None = None) -> dict:
    skills = record["skill_map"]["skills"]
    info = {"employer_floor": 3 if len(skills) > 100 else 0, "total": len(skills), "tails": [], "deferred": []}
    if tail_label_limit is not None:
        if type(tail_label_limit) is not int or tail_label_limit < 18:
            raise ValueError("tail label limit must be an integer of at least 18")
        info["tail_label_limit"] = tail_label_limit
    light = deepcopy(record)
    if info["employer_floor"]:
        light["skill_map"]["skills"] = [s for s in light["skill_map"]["skills"] if s["windows"][-1]["employers"] >= 3]
        for status in ("core", "rising", "early", "fading"):
            tail = [{"id": s["id"], "label": (s["label"][:tail_label_limit - 1].rstrip() + "…"
                     if tail_label_limit is not None and len(s["label"]) > tail_label_limit else s["label"])}
                    for s in skills if s["status"] == status and s["windows"][-1]["employers"] < 3]
            info["tails"].append({"status": status, "count": len(tail), "skills": tail})
        for level in light["ladder"]["levels"]:
            level["expectations"] = []
        info["deferred"] = ["ladder.levels[].expectations"]
        if any(i["profile"] is not None for i in light["industries"]):
            for industry in light["industries"]:
                if industry["profile"] is not None:
                    industry.update(profile=None, reason="Profile deferred in overview; request the industry endpoint or full view.")
            info["deferred"].append("industries[].profile")
    return {"role": light, "overview": info, "view": "overview"}
