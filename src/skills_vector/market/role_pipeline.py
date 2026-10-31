"""Offline, deterministic role/2 projection of one explicitly published snapshot.

No refresh, provider, model or network imports. Output never shares an input tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from fractions import Fraction
from pathlib import Path
from typing import Any

from .role_atlas import (RoleRecord, Measurement, Signal, AskedTogether, EvidenceCounts,
                         derive_status, edge_qualifies, sector_qualifies, SCHEMA_PATH)

MAPPING_ROOT = Path(__file__).with_name("role_atlas_mappings")
RECIPE = "role-evidence/1"
SKILL_DIMENSIONS = {"task", "capability", "tool", "knowledge"}
SCOPE_RULES = {
    1: r"\b(?:under supervision|under guidance|with guidance|closely supervised)\b",
    2: r"\b(?:independently (?:handle|manage|deliver|execute)|own(?:s|ing)? day.to.day (?:execution|operations))\b",
    3: r"\b(?:own(?:s|ing)?|lead(?:s|ing)?)\b.{0,35}\bend.to.end (?:delivery|implementation|projects?)\b",
    4: r"\b(?:own(?:s|ing)?\b.{0,40}\b(?:architecture|ambiguous)|(?:shape|define|set|drive)\b.{0,40}\b(?:strategy|roadmap)|lead\b.{0,20}\bdesign decisions)",
    5: r"\b(?:own(?:s|ing)?|define|set)\b.{0,35}\b(?:company.wide|organization.wide|firm.wide)\b",
}
LEVEL_NAMES = ("Guided", "Independent", "Complex delivery", "Strategic", "Organization-wide")
QUALIFICATION = re.compile(r"^(?:experience\b|previous experience\b|(?:proven|demonstrated) (?:experience|ability)\b|ability to\b|you (?:have|bring)\b|must have\b|\d+\s*\+?\s*(?:[-–]\s*\d+)?\s*years?\b|at least\b)", re.I)
MANAGEMENT = re.compile(r"\b(?:direct reports|manage (?:a|the|your) team|hire and (?:manage|evaluate)|supervise (?:staff|employees))\b", re.I)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def posting_source_note(rows: list[dict]) -> str:
    """Coverage of admitted rows, not the roster or the global Method policy."""
    from urllib.parse import urlsplit
    boards, sites = set(), set()
    names = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
             "smartrecruiters": "SmartRecruiters", "workable": "Workable"}
    for row in rows:
        kind = row.get("ats")
        if kind in names:
            boards.add(names[kind])
        elif kind == "jsonld":
            host = urlsplit(row.get("board_url", "")).hostname or ""
            sites.add("Workday" if host.endswith(".myworkdayjobs.com") else "structured JSON-LD")
    parts = []
    if boards:
        parts.append("public job boards (" + ", ".join(sorted(boards)) + ")")
    if sites:
        parts.append("employer career sites (" + ", ".join(sorted(sites)) + ")")
    return "Posting sources: " + (" and ".join(parts) if parts else "published posting excerpts; source coverage not recorded")


def employer_weighted_share(rows: list[dict], hits: set[str]) -> float:
    totals = Counter(normalized(p["employer"]) for p in rows)
    mentions = Counter(normalized(p["employer"]) for p in rows if p["dedup_key"] in hits)
    return float(sum((Fraction(mentions[e], n) for e, n in sorted(totals.items())), Fraction()) / len(totals)) if totals else 0.0


def sector_sample_note(rows: list[dict], employers: dict, sectors: dict) -> str:
    names = {normalized(p["employer"]) for p in rows}
    verified = Counter(employers[e]["naics"] for e in names if employers[e]["status"] == "verified")
    count = sum(verified.values())
    if not count:
        return "Sample sectors: sector mix unverified"
    top = sorted(verified, key=lambda k: (-verified[k], k))[:2]
    labels = ", ".join(sectors.get(k.replace("–", "-"), sectors.get(k, k)) for k in top)
    other = "; other verified sectors also present" if len(verified) > 2 else ""
    return f"Sample sectors: verified subset: {labels} ({count}/{len(names)} employers verified{other}); " + ("remaining sectors unverified" if count < len(names) else "all admitted employers verified")


def validate_alignment(row: dict) -> None:
    require(row.get("confidence") in ("exact", "close", "loose"), "mapping confidence must be exact, close or loose")
    require(isinstance(row.get("rationale"), str) and bool(row["rationale"].strip()), "mapping needs a nonempty rationale")


def within(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), f"unsafe/missing input: {name}")
    return path


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def window_of(timestamp: str, cadence: str) -> str:
    date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return f"{date.year:04d}-Q{(date.month - 1) // 3 + 1}" if cadence == "quarterly" else f"{date.year:04d}-{date.month:02d}"


@dataclass
class Triage:
    level: int | None
    reason: str
    strong: list[dict[str, Any]]
    weak: dict[str, Any]


def triage(posting: dict, quotes: list[str], *, role_scope_rules: bool = True, segment_policy: dict | None = None) -> Triage:
    """Scope first; only a reviewed role-specific segment may be strong title evidence."""
    weak = {"title": posting.get("title"), "advertised_experience": posting.get("advertised_experience", {}).get("value", [])}
    strong, current_duties = [], []
    segment_role = role_scope_rules and posting.get("occupation_slug") == "account-executive"
    for quote in sorted(set(quotes)):
        search_text = " ".join(quote.split())
        if QUALIFICATION.search(search_text):
            continue
        if MANAGEMENT.search(search_text):
            return Triage(None, "people_management_is_a_linked_track", [{"quote": quote, "track": "management"}], weak)
        current_duties.append(quote)
        for level, pattern in SCOPE_RULES.items():
            if segment_role and level not in (1, 5):
                continue  # Generic selling/project/strategy duties do not separate AE levels.
            if re.search(pattern, search_text, re.I):
                strong.append({"level": level, "quote": quote})
    if posting.get("work_level") == "people_manager":
        return Triage(None, "people_management_is_a_linked_track", strong, weak)
    if segment_role:
        from .role_segments import segment_signals
        segments = segment_signals(posting, current_duties, segment_policy)
        if len({s["level"] for s in segments}) > 1:
            return Triage(None, "conflicting_customer_segments", segments, weak)
        if segments:
            return Triage(segments[0]["level"], "reviewed_customer_segment_scope", segments, weak)
    levels = {signal["level"] for signal in strong}
    if len(levels) > 1:
        return Triage(None, "conflicting_strong_scope_signals", strong, weak)
    if not levels:
        return Triage(None, "no_current_scope_or_ownership_signal", strong, weak)
    return Triage(next(iter(levels)), "source_verified_scope_and_ownership", strong, weak)


def signal(kind: str, source: str, reading: str) -> dict:
    return Signal(source, kind, "P" if kind == "postings" else "O", "coincident" if kind == "postings" else "lagging", reading, "presence").to_dict()


def taxonomy_signal(match: dict) -> dict:
    name = "Detailed Work Activity" if match["kind"] == "dwa" else "Software Skills"
    return signal("onet_ratings", "O*NET 31.0 " + name,
                  f"{match['id']}; {match['method']}; {match['confidence']}; matched text: {match['matched_text']}. O*NET 31.0, CC BY 4.0; taxonomy presence only, not upward confirmation or a Hot Technology flag.")


def counts(rows: list[dict], window: str) -> dict:
    return EvidenceCounts(len(rows), len({normalized(p["employer"]) for p in rows}), window, "coincident").to_dict()


def cooccurrences(rows: list[dict], mentions: dict[str, set[str]]) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    n = len(rows)
    employers = {p["dedup_key"]: normalized(p["employer"]) for p in rows}
    for a, b in combinations(sorted(mentions), 2):
        together = mentions[a] & mentions[b]
        if not together:
            continue
        edge = AskedTogether(a, b, len(together) * n / (len(mentions[a]) * len(mentions[b])), len(together),
                             len({employers[key] for key in together}), "coincident")
        if edge_qualifies(edge):
            kept.append(edge.to_dict())
        else:
            dropped.append({**edge.to_dict(), "failed_floors": [name for name, fails in
                (("lift", edge.lift < 1.5), ("postings", edge.postings < 25), ("employers", edge.employers < 8)) if fails]})
    return kept, dropped


def sector_profile(rows: list[dict], window: str, mentions: dict[str, set[str]]) -> dict | None:
    if not sector_qualifies(EvidenceCounts(**counts(rows, window))):
        return None
    ids = {p["dedup_key"] for p in rows}
    duties = sorted({e["source_wording"] for p in rows for e in p["expectations"] if e["dimension"] == "task"})
    credentials = defaultdict(set)
    for posting in rows:
        for expected in posting["expectations"]:
            if expected["dimension"] == "credential" and expected.get("basis") in ("employer_requirement", "employer_preference"):
                credentials[(expected["source_wording"], expected["basis"])].add(posting["dedup_key"])
    return {"share_of_occupation_jobs": "Not recorded; no BLS industry matrix imported", "median_pay": "Not recorded",
            "how_it_differs": "Observed posting duties in verified employer sectors only; no claim of a difference from other sectors.",
            "distinct_duties": duties,
            "certifications": [{"name": wording, "issuer": "Not recorded", "asked_as": "required" if basis == "employer_requirement" else "preferred",
                                "posting_share": len(postings) / len(rows), "source": "Published job postings", "timing": "coincident"}
                               for (wording, basis), postings in sorted(credentials.items())],
            "domain_knowledge": sorted({e["source_wording"] for p in rows for e in p["expectations"] if e["dimension"] == "knowledge"}),
            "skill_shares": [{"skill": key, "share": len(values & ids) / len(rows), "timing": "coincident"}
                             for key, values in sorted(mentions.items())],
            "sources": [signal("postings", "Published job postings", "Sector counts and skill shares use all admitted postings with verified employer-to-NAICS rows, including unassigned scope. Credentials/knowledge remain literal mentions, with no official catalog or issuer inference.")]}


def skill_identity(expected: dict, slug: str, nodes: dict, phrase_map: dict, *, rules: bool = False) -> tuple:
    quote = expected["source_wording"]
    term = expected.get("term_match")
    if rules and term:
        require(term["family"] == "P" and term["confidence"] in ("exact", "close"), "posting term cannot acquire an official link")
        return "skill.posting." + term["id"], {"label": term["label"], "short_label": term["label"][:18], "node": None,
                "normalized_phrase": normalized(quote), "term_match": term}, None
    match = expected.get("backbone_match")
    if rules and match and match["confidence"] in ("exact", "close"):
        from .posting_volume import normalize_phrase
        label = match["label"]
        node = {"id": match["id"], "short_label": label, "match_source": match, "quote": quote}
        return "skill." + match["id"].replace(":", "."), {"label": label, "short_label": label if len(label) <= 18 else label[:17].rstrip() + "…", "node": node, "normalized_phrase": normalize_phrase(quote)}, None
    phrase = normalized(quote)
    if rules:
        from .posting_volume import normalize_phrase
        phrase = normalize_phrase(quote)
    alignment = phrase_map.get((slug, normalized(quote)))
    if rules and alignment is None:
        matches = [row for (role, _), row in phrase_map.items() if role == slug and normalize_phrase(row["phrase"]) == phrase]
        if matches and len({r["backbone_id"] for r in matches}) == 1:
            # A loose lexical alias must not gain an O-link via a close sibling.
            alignment = next((r for r in matches if r["confidence"] == "loose"), matches[0])
    node = nodes.get(alignment["backbone_id"]) if alignment else None
    if not alignment or alignment["confidence"] == "loose" or node["confidence"] == "loose":
        node = None
    key = "skill." + (node["id"].replace(":", ".") if node else "posting." + digest((expected["dimension"] + "|" + phrase).encode())[:16])
    label = node["short_label"] if node else phrase if rules else quote
    return key, {"label": label, "short_label": label if len(label) <= 18 else label[:17].rstrip() + "…",
                 "node": node, "normalized_phrase": phrase}, alignment


@dataclass
class EvidenceInputs:
    root: Path
    release_root: Path
    mapping_root: Path
    release_id: str
    manifest: dict
    postings: list[dict]
    occupations: dict[str, dict]
    sources: dict[str, dict]
    extracts: dict[str, str]
    skills: dict
    employers: dict[str, dict]
    hashes: dict[str, str]
    history_groups: dict[str, str] | None = None
    data_kind: str = "research"
    snapshot_roots: tuple[Path, ...] = ()

    @classmethod
    def from_snapshots(cls, roots: list[Path], release_root: Path, mapping_root: Path = MAPPING_ROOT,
                       employer_map: Path | None = None, *, upgrade: bool = True, grouping: bool = True, canonical: bool = True, skills_only: bool = True) -> EvidenceInputs:
        from .posting_volume_inputs import read_snapshots
        return read_snapshots(roots, release_root, mapping_root, employer_map, upgrade=upgrade, grouping=grouping, canonical=canonical, skills_only=skills_only)

    @classmethod
    def read(cls, release_root: Path, mapping_root: Path = MAPPING_ROOT, release_id: str | None = None) -> EvidenceInputs:
        release_root, mapping_root = release_root.resolve(), mapping_root.resolve()
        pointer = load(within(release_root, "current.json"))
        selected = release_id or pointer["current"]
        entry = next((r for r in pointer["releases"] if r["release_id"] == selected), None)
        require(entry is not None, "release is not in the published index")
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", selected)), "unsafe release id")
        root = (release_root / "releases" / selected).resolve()
        require(root.is_relative_to(release_root), "release escapes input tree")
        manifest_path = within(root, "manifest.json")
        manifest = load(manifest_path)
        manifest_hash = digest(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
        require(manifest_hash == entry["manifest_sha256"], "published manifest hash mismatch")
        require(manifest["release_id"] == selected and manifest.get("release_schema") == "market-release/2", "manifest release/schema mismatch")
        sources = {s["id"]: s for s in load(within(root, "sources.json"))}
        extracts = {}
        for sid, source in sorted(sources.items()):
            if source.get("inclusion") is True and source.get("extract_path"):
                data = within(root, source["extract_path"]).read_bytes()
                require(digest(data) == source["extract_sha256"], f"extract hash mismatch: {sid}")
                # Metadata headers are not evidence text.
                extracts[sid] = "\n".join(line for line in data.decode().splitlines() if not line.startswith("#"))
        occupations = {o["slug"]: o for o in load(within(root, "occupations.json"))}
        require(all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in occupations), "unsafe role slug")
        postings = load(within(root, "postings.json"))
        seen = set()
        for posting in postings:
            sid = posting["source_id"]
            require(sid in extracts and posting["occupation_slug"] in occupations, "posting lacks an admitted source/role")
            key = (posting["occupation_slug"], posting["dedup_key"])
            require(key not in seen, "duplicate admitted posting identity")
            seen.add(key)
            for evidence in [posting.get("responsibility_evidence", {}), posting.get("work_level_evidence", {}), *posting.get("expectations", [])]:
                quote = evidence.get("source_wording") or evidence.get("quote")
                if quote:
                    require(evidence.get("source_id") == sid and evidence.get("source_sha256") == sources[sid]["sha256"], "posting quote source mismatch")
                    require(quote in extracts[sid], "posting quote is not in its published extract")
        skills = load(within(mapping_root, "skills.json"))
        require(skills["schema"] == "role-atlas-mappings/1", "unsupported mapping schema")
        require(skills["release_id"] == selected, "mapping review is pinned to another release")
        require(set(skills["roles"]) == set(occupations), "role mapping must cover the published roles exactly")
        for role in skills["roles"].values():
            validate_alignment(role)
        nodes = {}
        for node in skills["backbone"]:
            validate_alignment(node)
            sid = node["source_id"]
            require(node["id"] not in nodes, "duplicate backbone id")
            require(node["role"] in occupations and sid in extracts and node["source_note"].strip(), "missing backbone provenance")
            source = sources[sid]
            code = skills["roles"][node["role"]]["anchor"]["code"]
            require(source.get("source_type") == "foundation" and source["url"] == "https://www.onetonline.org/link/summary/" + code, "backbone is not the mapped local O*NET summary")
            require(source["extract_sha256"] == node["extract_sha256"] and node["quote"] in extracts[sid], "unverified local backbone quote")
            require(node["id"].startswith(f"onet:{code}:local-task:"), "unsupported backbone identifier")
            nodes[node["id"]] = node
        seen_phrases = set()
        for row in skills["phrases"]:
            validate_alignment(row)
            key = (row["role"], normalized(row["phrase"]))
            require(key not in seen_phrases and row["source_note"].strip(), "ambiguous/unreviewed phrase mapping")
            require(row["backbone_id"] in nodes and nodes[row["backbone_id"]]["role"] == row["role"], "unknown or cross-role backbone id")
            require(any(p["occupation_slug"] == row["role"] and any(normalized(e["source_wording"]) == key[1] for e in p["expectations"]) for p in postings), "phrase not in the published postings")
            seen_phrases.add(key)
        employers = {}
        sector_path = mapping_root / "sic_sectors.json"
        sector_table = load(sector_path) if sector_path.exists() else None
        for row in load(within(mapping_root, "employers.json")):
            key = normalized(row["employer"])
            require(key not in employers and row["source_note"].strip(), "duplicate employer/missing source note")
            require(row["status"] in ("verified", "unverified"), "invalid employer verification status")
            if row.get("source") == "sec_edgar":
                from .role_sectors import validate_sector
                require(sector_table is not None, "missing SEC sector table")
                validate_sector(row, sector_table)
                require(row["naics"] is None or row["naics"].replace("–", "-") in skills["sectors"], "invalid SEC NAICS")
            elif sector_table is not None and not row.get("source_id"):
                require(row["status"] == "unverified" and row["naics"] is None, "unverified registry employer cannot have a sector")
            else:
                require(any(normalized(p["employer"]) == key and p["source_id"] == row["source_id"] for p in postings), "employer source not tied to an admitted employer posting")
                if row["status"] == "verified":
                    require(row["naics"] in skills["sectors"] and bool(row["quote"]), "verified employer needs NAICS and evidence")
                    require(row["quote"] in extracts[row["source_id"]], "employer evidence not in local source")
                    require(not any(row["quote"] == p.get("context_dimensions", {}).get("customer_industry", {}).get("quote") for p in postings), "customer industry is not employer industry")
                else:
                    require(row["naics"] is None, "unverified employer must not be assigned a sector")
            employers[key] = row
        require({normalized(p["employer"]) for p in postings} <= set(employers), "every employer needs a reviewed or unverified mapping row")
        hashes = {"release/" + str(p.relative_to(root)): digest(within(root, str(p.relative_to(root))).read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}
        hashes.update({"mappings/" + name: digest(within(mapping_root, name).read_bytes()) for name in ("skills.json", "employers.json")})
        if sector_table is not None:
            hashes["sic_sectors.json"] = digest(sector_path.read_bytes())
            hashes["role_sectors.py"] = digest(Path(__file__).with_name("role_sectors.py").read_bytes())
        hashes["recipe.py"] = digest(Path(__file__).read_bytes())
        hashes["contract.py"] = digest(Path(__file__).with_name("role_atlas.py").read_bytes())
        hashes["schema.json"] = digest(SCHEMA_PATH.read_bytes())
        return cls(root, release_root, mapping_root, selected, manifest, postings, occupations, sources, extracts, skills, employers, hashes)

    def build(self, cadence: str = "quarterly") -> tuple[list[RoleRecord], dict]:
        require(cadence in ("quarterly", "monthly"), "invalid window cadence")
        require(not self.history_groups or cadence == "quarterly", "rules snapshots use quarterly windows")
        release = ("role_research_" if self.data_kind == "research" else "role_fixture_") + digest(canonical({"recipe": RECIPE, "inputs": self.hashes, "cadence": cadence}))[:16]
        records, role_reports = [], {}
        node_by_id = {node["id"]: node for node in self.skills["backbone"]}
        phrase_map = {(row["role"], normalized(row["phrase"])): row for row in self.skills["phrases"]}
        for slug, occupation in sorted(self.occupations.items()):
            config = self.skills["roles"][slug]
            all_rows = sorted((p for p in self.postings if p["occupation_slug"] == slug), key=lambda p: p["dedup_key"])
            by_window = defaultdict(list)
            for posting in all_rows:
                by_window[window_of(self.sources[posting["source_id"]]["retrieved_at"], cadence)].append(posting)
            if self.history_groups:
                for window in self.history_groups:
                    by_window.setdefault(window, [])
            require(bool(by_window), f"no observed postings for {slug}")
            current = max(by_window)
            rows = by_window[current]
            comparable_prior = [w for w in sorted(by_window) if w < current and by_window[w] and rows
                                and self.history_groups and self.history_groups[w] == self.history_groups[current]]
            assignments, posting_audit, mentions, skill_data, excluded = {}, [], defaultdict(set), {}, Counter()
            if self.history_groups and rows:
                for posting in all_rows:
                    for expected in posting["expectations"]:
                        if expected["dimension"] in SKILL_DIMENSIONS:
                            key, data, _ = skill_identity(expected, slug, node_by_id, phrase_map, rules=True)
                            skill_data[key] = data
                            mentions[key] = set()
            for posting in rows:
                duty_quotes = [e["source_wording"] for e in posting["expectations"] if e["dimension"] in ("task", "capability")
                               and (not (posting.get("extraction_method", "").startswith("rules/") or posting.get("extraction_method") == "flat-headings/1") or e.get("section") == "responsibilities")]
                if posting.get("responsibility_evidence", {}).get("quote"):
                    duty_quotes.append(posting["responsibility_evidence"]["quote"])
                decision = triage(posting, duty_quotes, role_scope_rules=posting.get("extraction_method") in ("rules/2", "rules/3", "flat-headings/1"), segment_policy=self.skills.get("segment_scope"))
                assignments[posting["dedup_key"]] = decision.level
                linked = []
                for expected in posting["expectations"]:
                    if expected["dimension"] not in SKILL_DIMENSIONS:
                        excluded[expected["dimension"]] += 1
                        continue
                    key, data, alignment = skill_identity(expected, slug, node_by_id, phrase_map, rules=bool(self.history_groups))
                    proposed_id = alignment["backbone_id"] if alignment else None
                    backbone_id = data["node"]["id"] if data["node"] else None
                    if data["node"] and data["node"].get("match_source"):
                        proposed_id = backbone_id
                    skill_data[key] = data
                    mentions[key].add(posting["dedup_key"])
                    linked.append({"skill": key, "backbone_id": backbone_id, "proposed_backbone_id": proposed_id,
                                   "mapping_confidence": alignment["confidence"] if alignment else None,
                                   "mapping_rationale": alignment["rationale"] if alignment else None, "phrase": expected["source_wording"], "normalized_phrase": data["normalized_phrase"], "backbone_match": expected.get("backbone_match"), "term_match": expected.get("term_match"),
                                   "start": expected.get("start"), "end": expected.get("end"), "source_sha256": expected.get("source_sha256"), "projection_extraction": expected.get("projection_extraction"), "backbone_proposals": expected.get("backbone_proposals", []), "source_id": posting["source_id"], "expectation_id": expected["expectation_id"]})
                posting_audit.append({"posting_id": posting["dedup_key"], "source_id": posting["source_id"], "employer": posting["employer"],
                                      "level": decision.level, "reason": decision.reason, "strong_signals": decision.strong,
                                      "weak_signals": decision.weak, "contact_spans_omitted": posting.get("contact_spans_omitted", 0),
                                      "contact_omissions": posting.get("contact_omissions", []), "excluded_projection_spans": posting.get("projection_exclusions", []), "skill_links": sorted(linked, key=lambda x: (x["skill"], x["phrase"]))})
                if posting.get("extraction_method") == "rules/3":
                    posting_audit[-1].update(extraction_method="rules/3", native_extraction_method=posting["native_extraction_method"],
                        min_years=posting["min_years"], education=posting["education"],
                        qualification_evidence=posting["qualification_evidence"], skill_filter=posting["skill_filter"])
                if posting.get("source_kind") == "jsonld":
                    posting_audit[-1].update(source_kind="jsonld", page_url=posting["url"], page_sha256=posting["page_sha256"])
            level_rows = {level: [p for p in rows if assignments[p["dedup_key"]] == level] for level in range(1, 6)}
            strata = sorted({value for value in assignments.values() if value is not None})
            if None in assignments.values():
                strata.append("unassigned")
            historical_mentions = {}
            if self.history_groups:
                strata = [1, 2, 3, 4, 5, "unassigned"]
                for window, past_rows in sorted(by_window.items()):
                    per_skill = defaultdict(set)
                    for posting in past_rows:
                        for expected in posting["expectations"]:
                            if expected["dimension"] in SKILL_DIMENSIONS:
                                past_key, _, _ = skill_identity(expected, slug, node_by_id, phrase_map, rules=True)
                                per_skill[past_key].add(posting["dedup_key"])
                    historical_mentions[window] = per_skill
            ranked = any(p.get("extraction_method") == "rules/3" for p in rows)
            skills = []
            for key, ids in sorted(mentions.items()):
                data = skill_data[key]
                hits = [p for p in rows if p["dedup_key"] in ids]
                employer_counts = Counter(normalized(p["employer"]) for p in hits)
                share = len(hits) / len(rows)
                measurement = Measurement(current, share, len(employer_counts), max(employer_counts.values(), default=0) / len(hits) if hits else 0,
                                          self.history_groups[current] if self.history_groups else RECIPE + ":" + self.release_id + ":snapshot-only", strata, "coincident")
                measurements = []
                if self.history_groups:
                    for window, past_rows in sorted(by_window.items()):
                        if not past_rows:
                            continue
                        past_hits = [p for p in past_rows if p["dedup_key"] in historical_mentions[window][key]]
                        spread = Counter(normalized(p["employer"]) for p in past_hits)
                        measurements.append(Measurement(window, len(past_hits) / len(past_rows), len(spread),
                            max(spread.values(), default=0) / len(past_hits) if past_hits else 0,
                            self.history_groups[window], strata, "coincident"))
                else:
                    measurements = [measurement]
                if ranked:
                    for m in measurements:
                        window_hits = historical_mentions[m.window][key] if self.history_groups else ids
                        m.employer_weighted_share = employer_weighted_share(by_window[m.window], window_hits)
                employer_word = "employer" if len(employer_counts) == 1 else "employers"
                evidence = [signal("postings", "Captured public job postings" if self.history_groups else "Published job postings", f"{len(hits)}/{len(rows)} admitted postings: published expectation excerpts record this node; {len(employer_counts)} {employer_word}. Includes unassigned scope.")]
                if data.get("term_match"):
                    term = data["term_match"]
                    evidence[0]["reading"] += f" Canonical posting-led lexicon term {term['id']}; {term['method']}; literal match: {term['matched_text']}; family P only, no official equivalence."
                if data["node"]:
                    node = data["node"]
                    if node.get("match_source"):
                        match = node["match_source"]
                        evidence.append(taxonomy_signal(match))
                    else:
                        evidence.append(signal("onet_ratings", "O*NET local task anchor", f"{node['id']}; local natural key, not an official task database ID. {self.sources[node['source_id']]['url']}; published source {node['source_id']}. {node['quote']}"))
                shares = [100 * sum(p["dedup_key"] in ids for p in level_rows[level]) / len(level_rows[level]) if level_rows[level] else None for level in range(1, 6)]
                observed = [level for level in range(1, 6) if shares[level - 1] is not None and shares[level - 1] > 0]
                forecast = {"id": "forecast." + slug + "." + key, "claim": "Future published expectation excerpts record this skill; includes unassigned scope",
                            "basis": "none", "reason": "No four-quarter backtest archive." if self.history_groups else "One observed window; no historical base rate or backtest archive.",
                            "probability": None, "base_rate": None, "horizon_quarters": 4, "created": current,
                            "resolves": str(int(current[:4]) + 1) + current[4:], "scored_against": "Same-admission/excerpt sample in four quarters; no scores yet"}
                skills.append({"id": key, "label": data["label"], "short_label": data["short_label"], "status": derive_status(measurements, [Signal(**s) for s in evidence]),
                               "first_level": min(observed) if observed else None, "share_by_level": shares, "source_links": ["P", "O"] if data["node"] else ["P"],
                               "windows": [m.to_dict() for m in measurements], "evidence": evidence, "forecast": forecast if derive_status(measurements, [Signal(**s) for s in evidence]) != "core" else None, "disputed": None})
            if ranked:
                skills.sort(key=lambda s: (-s["windows"][-1]["employers"], -s["windows"][-1]["employer_weighted_share"], s["id"]))
            kept, dropped = cooccurrences(rows, mentions)
            ladder = []
            segment_policy = self.skills.get("segment_scope") if slug == "account-executive" and (not rows or any(p.get("extraction_method") in ("rules/2", "rules/3", "flat-headings/1") for p in rows)) else None
            for level, name in enumerate(LEVEL_NAMES, 1):
                selected = level_rows[level]
                expectations = {}
                for posting in selected:
                    for expected in posting["expectations"]:
                        if expected["dimension"] not in SKILL_DIMENSIONS:
                            continue
                        eid = "exp." + slug + "." + digest(normalized(expected["source_wording"]).encode())[:16]
                        expectations[eid] = {"id": eid, "label": expected["source_wording"], "introduced_at": None,
                                             "basis": "advertised_demand", "cite": f"/v1/expectations/{eid}@{release}", "timing": "coincident"}
                ladder.append({"id": slug + f".L{level}", "level": level, "name": config["category"].capitalize() + f" L{level}" if selected else f"L{level}",
                               "typical_titles": sorted({p["title"] for p in selected}),
                               "years_wording": "; ".join(sorted({wording for p in selected for wording in p.get("advertised_experience", {}).get("value", [])})) or "Not recorded",
                               "scope": ("Reviewed AE segment scope: " + ("; ".join(r["segment"] + " — " + r["rationale"] for r in segment_policy["rows"] if r["level"] == level) or "No reviewed segment maps to this level") + ". Source: " + segment_policy["source_note"] + " Other scope uses the shared rubric; this is not observed progression.") if segment_policy else "Declared algorithmic scope rubric: " + name + ". Not a population hiring standard.",
                               "owns": "; ".join(sorted({("Native title: " if s.get("origin") == "native_title" else "Current duty: " if s.get("origin") == "current_duty" else "") + s["quote"] for p in posting_audit if p["level"] == level for s in p["strong_signals"]})) or "No assigned scope/ownership evidence in this sample.",
                               "step_up": "Scope-rubric distinction only; longitudinal progression and promotion requirements are not established.",
                               "evidence": counts(selected, current), "expectations": [expectations[k] for k in sorted(expectations)]})
            industries = []
            for naics, label in sorted(self.skills["sectors"].items()):
                selected = [p for p in rows if self.employers[normalized(p["employer"])]["status"] == "verified" and self.employers[normalized(p["employer"])]["naics"].replace("–", "-") == naics.replace("–", "-")]
                profile = sector_profile(selected, current, mentions)
                industries.append({"naics": naics, "label": label, "naics_title": label, "sub_industries": "Not verified in local inputs",
                                   "evidence": counts(selected, current), "profile": profile,
                                   "reason": None if profile else "Below 30 postings / 8 employers using verified employer sectors only; unverified employer mappings are excluded, never inferred from customers or job function."})
            top = [s["id"] for s in skills if s["share_by_level"][4] is not None and s["share_by_level"][2] is not None
                   and s["share_by_level"][4] >= 40 and s["share_by_level"][4] - s["share_by_level"][2] >= 20]
            anchor_tasks = [node["quote"] for node in self.skills["backbone"] if node["role"] == slug]
            record = RoleRecord.from_dict({"schema": "skills-vector.role/2", "id": "role." + slug, "label": occupation["label"],
                "status": config["status"], "publication": "experimental" if config["status"] == "emerging" else "standard", "data_kind": self.data_kind, "release_id": release,
                "aliases": occupation["aliases"], "anchors": [config["anchor"]],
                "foundation": {"median_pay": "Not recorded", "pay_note": "No wage series imported", "employment": "Not recorded", "employment_note": "No headcount series imported",
                               "outlook_10y": "Not recorded", "outlook_note": "No projection series imported", "core_tasks": anchor_tasks,
                               "sources": [signal("onet_ratings", "O*NET published task excerpt", self.sources[config["source_id"]]["url"] + "; task-level anchor only; rating date is not established by retrieval date.")]},
                "demand_window": {"id": current, "compared_to": comparable_prior[-1] if comparable_prior else None, **{k: v for k, v in counts(rows, current).items() if k != "window"},
                                  "max_employer_share": max(Counter(normalized(p["employer"]) for p in rows).values(), default=0) / len(rows) if rows else 0, "window_length": cadence},
                "ladder": {"scale": "shared L1-L5", "track": "individual_contributor", "linked_roles": [], "levels": ladder},
                "skill_map": {"skills": skills, "asked_together": kept, "builds_on": [], "top_level_adds": {"top_level": 5, "core_level": 3, "skills": top,
                              "long_run_bar": signal("onet_ratings", "O*NET local task anchor", "Local task alignment is presence only; no numeric proficiency rating or level rating was supplied.")}},
                "industries": industries, "observations": [{"id": "sector.unverified", "label": "Unverified employers", "levels": [1, 2, 3, 4, 5],
                    "windows": [{"id": current, "mentions": len([p for p in rows if self.employers[normalized(p["employer"])]["status"] != "verified"]),
                        "postings": len(rows), "employers": len({normalized(p["employer"]) for p in rows if self.employers[normalized(p["employer"])]["status"] != "verified"}), "timing": "coincident"}],
                    "note": "Role-wide counts including unassigned scope, not a level-demand measurement. Excluded from sector profiles; no primary-employer sector is inferred."}], "limits": [
                    "Research projection of published release " + self.release_id + "; source counts are a bounded admitted sample, not market prevalence.",
                    posting_source_note(rows),
                    sector_sample_note(rows, self.employers, self.skills["sectors"]),
                    "Mention fractions count only recorded published expectation excerpts, not exhaustive full posting text. A missing recorded mention is not verified absence of demand and is not a future probability.",
                    "Quarterly rules snapshots; only identical sampling definitions are comparable. Repeated releases within a quarter are not independent windows." if self.history_groups else "One selected snapshot only. Retrieval dates bin observed mentions; repeated releases are not independent windows. No comparable temporal history is asserted, so all current statuses are early.",
                    "Null first_level means no observed assigned level; null share_by_level means no assigned denominator, not zero demand. Measurement strata explicitly include unassigned postings.",
                    ("AE customer segments in verified native titles or current owned-customer duties are strong scope under reviews/M7-r1.md and M7-r2.md; only different levels conflict; Named is title-only. Generic selling duties, seniority and years never assign. Mapping and rationales: ae_segments.json. Missing level expectations remain empty; progression is not established." if segment_policy else "Level names are a declared algorithmic scope rubric, not observed role progression. Titles and tenure never assign levels. Missing level expectations remain empty; no carry-over or first-introduction claim is inferred."),
                    "O*NET task excerpts are attributed to O*NET OnLine, U.S. Department of Labor, CC BY 4.0, as recorded in the published sources; agent-authored alignments are not official or human-adjudicated equivalence.",
                    "Backbone local-task keys identify exact O*NET task text within its SOC summary, not official task database element IDs; unmapped phrases remain posting-led literal identities.",
                    "Software example keys are project identifiers bound to native O*NET 31.0 examples/categories in software_examples.json, not official element IDs or proficiency claims. Posting-term keys are canonical literal P mentions only, never official equivalence. Sentence projection and narrow notice quarantine do not rewrite the source snapshot." if self.history_groups else "Only the explicitly pinned local mappings were used; no software-example or posting-term lexicon is inferred for this legacy release.",
                    "Unverified employer sectors are excluded, not guessed. Sector profiles need 30 postings and 8 employers. Official industry shares, wages and credential catalogs were not imported; any credential/knowledge text is a literal posting mention.",
                    "No numeric forecast is made without a four-quarter historical backtest. Current recorded mention fractions are measurements, not future probabilities. A separately selected M5 ledger can track these claims without scoring missing probabilities.",
                    "Rules/1 sample data: illustrative, not research findings." if self.data_kind == "fixture" else "Research data: local source-bound observations, not population prevalence."] + ([
                    "Extraction: rules/3; non-skill drops and structured education/experience receipts are in the external audit. No admitted posting is removed. min_years is the smallest explicit non-preferred lower bound, not an inferred total-career requirement or level.",
                    "Ranking counts distinct employers first, then the mean within-employer posting share. Each employer has equal total weight. Raw posting shares and all status/edge/sector thresholds are unchanged."] if ranked else [])})
            records.append(record)
            role_reports[slug] = {"postings_in": len(all_rows), "current_postings": len(rows), "window": current,
                "other_window_postings_not_compared": sum(len(v) for w, v in by_window.items() if w != current and w not in comparable_prior) if self.history_groups else len(all_rows) - len(rows),
                "observed_windows": {w: len(v) for w, v in sorted(by_window.items())},
                "postings_per_level": {**{f"L{k}": len(v) for k, v in level_rows.items()}, "unassigned": sum(v is None for v in assignments.values())},
                "skills_by_status": {status: sum(s["status"] == status for s in skills) for status in ("core", "rising", "early", "fading")},
                "backbone_linked_skills": sum(bool(s["node"]) for s in skill_data.values()),
                "posting_led_skills": sum(not s["node"] for s in skill_data.values()), "excluded_non_skill_mentions": dict(sorted(excluded.items())),
                "edges_kept": len(kept), "edges_dropped": len(dropped),
                "edge_floor_failures": {floor: sum(floor in e["failed_floors"] for e in dropped) for floor in ("lift", "postings", "employers")},
                "sectors_with_profiles": sum(i["profile"] is not None for i in industries), "sectors_without_profiles": sum(i["profile"] is None for i in industries),
                "contact_spans_omitted": sum(p.get("contact_spans_omitted", 0) for p in rows),
                "projection_excluded_spans": sum(len(p.get("projection_exclusions", [])) for p in rows),
                "postings_with_unverified_sectors": sum(self.employers[normalized(p["employer"])]["status"] != "verified" for p in rows),
                "postings": posting_audit, "dropped_edges": dropped}
            if ranked:
                role_reports[slug]["rules3"] = {
                    "drops_by_reason": dict(sorted(sum((Counter(p["skill_filter"]["drops_by_reason"]) for p in rows), Counter()).items())),
                    "postings_emptied": sum(p["skill_filter"]["emptied"] for p in rows),
                    "postings_empty_before": sum(p["skill_filter"]["empty_before"] for p in rows),
                    "candidates_before": sum(p["skill_filter"]["candidates_before"] for p in rows),
                    "candidates_after": sum(p["skill_filter"]["candidates_after"] for p in rows)}
        return records, {"recipe": RECIPE, "release_id": release, "input_release_id": self.release_id, "input_hashes": self.hashes,
                         "window_length": cadence, "roles": role_reports}

    def write(self, output: Path, cadence: str = "quarterly") -> dict:
        from .role_artifacts import guard_output
        output = guard_output(output, self.release_root, self.mapping_root, *self.snapshot_roots)
        for protected in (self.release_root, self.mapping_root):
            require(not output.is_relative_to(protected) and not protected.is_relative_to(output), "output overlaps a protected input tree")
        records, audit = self.build(cadence)
        payloads = {"roles/" + r.id.removeprefix("role.") + ".json": r.to_json().encode() for r in records}
        payloads["audit.json"] = canonical(audit)
        require(not output.exists() or output.is_dir(), "output is not a directory")
        existing_names = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()} if output.exists() else set()
        require(not existing_names - payloads.keys(), "output contains unrelated files; choose a new output location")
        existing = {name: within(output, name).read_bytes() for name in existing_names}
        require(not existing or existing == payloads, "output already contains different data; choose a new output location")
        for name, content in payloads.items():
            path = (output / name).resolve()
            require(path.is_relative_to(output), "unsafe output path")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=Path("preview/release"))
    parser.add_argument("--release-id")
    parser.add_argument("--mapping-root", type=Path, default=MAPPING_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-length", choices=("quarterly", "monthly"), default="quarterly")
    args = parser.parse_args()
    inputs = EvidenceInputs.read(args.release_root, args.mapping_root, args.release_id)
    audit = inputs.write(args.output, args.window_length)
    print(json.dumps({"release_id": audit["release_id"], "roles": {key: {k: v for k, v in value.items() if k not in ("postings", "dropped_edges")} for key, value in audit["roles"].items()}}, indent=2))


if __name__ == "__main__":
    main()
