"""Offline hash/provenance-checked adapter from quarterly rules snapshots to M4 inputs."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .posting_volume import BOARDS, SCHEMA, EXTRACTION, VolumePolicy, extract_sections, native_description, quarter, _native_rows
from .sources import parse_job_board, us_location_ok, dedup_key
from .role_artifacts import digest as object_digest, read_json
from .role_pipeline import EvidenceInputs, digest, normalized, require, within


def read_snapshots(roots: list[Path], release_root: Path, mapping_root: Path, employer_map: Path | None = None, *, upgrade: bool = True, grouping: bool = True) -> EvidenceInputs:
    base = EvidenceInputs.read(release_root, mapping_root)
    postings, sources, extracts, hashes, groups = [], dict(base.sources), dict(base.extracts), dict(base.hashes), {}
    manifests = []
    configured_roles = None
    from .onet_backbone import Backbone, LOOKUP, RULES
    from .posting_volume import capture
    backbone = Backbone() if grouping else None
    for root in sorted((p.resolve() for p in roots)):
        manifest = read_json(within(root, "manifest.json"))
        require(manifest["schema"] == SCHEMA and manifest["window_length"] == "quarterly", "unsupported snapshot schema/cadence")
        require(manifest["snapshot_id"] == object_digest({k: v for k, v in manifest.items() if k != "snapshot_id"}), "snapshot manifest hash mismatch")
        window = manifest["quarter"]
        require(window not in groups, "duplicate quarterly snapshot; releases are not independent windows")
        policy = VolumePolicy.from_value(manifest["policy"], root / "manifest.json").value
        require(manifest["policy_sha256"] == object_digest(policy) and manifest["extraction_method"] in ("rules/1", "rules/2"), "snapshot policy/method mismatch")
        require(policy["data_kind"] == manifest["data_kind"], "snapshot/policy data kind mismatch")
        roles = set(policy["roles"])
        require(roles <= set(base.occupations) and (configured_roles is None or roles == configured_roles), "snapshot roles mismatch")
        configured_roles = roles
        groups[window] = manifest["comparison_group"]
        manifests.append(manifest)
        files = manifest["files"]
        require({str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()} == set(files) | {"manifest.json"}, "unindexed/missing snapshot files")
        for name, expected in files.items():
            data = within(root, name).read_bytes()
            require(hashlib.sha256(data).hexdigest() == expected, "snapshot file hash mismatch: " + name)
            hashes["snapshot/" + window + "/" + name] = expected
        hashes["snapshot/" + window + "/manifest.json"] = digest(within(root, "manifest.json").read_bytes())
        rows = read_json(within(root, "postings.json"))
        seen, native_cache, parsed_cache = set(), {}, {}
        for posting in rows:
            require(posting["extraction_method"] == manifest["extraction_method"] and quarter(posting["retrieved_at"]) == window, "posting method/date mismatch")
            key = (posting["occupation_slug"], posting["dedup_key"])
            require(key not in seen and key[0] in roles, "duplicate/unknown-role posting")
            seen.add(key)
            text = within(root, posting["description_path"]).read_bytes().decode("utf-8")
            raw = within(root, posting["raw_description_path"]).read_bytes().decode("utf-8")
            require(digest(text.encode()) == posting["source_sha256"] and digest(raw.encode()) == posting["raw_description_sha256"], "posting description hash mismatch")
            origin = posting["board_sha256"]
            if origin not in native_cache:
                body = within(root, "boards/" + origin + ".json").read_bytes()
                native_cache[origin] = json.loads(body) if posting["is_detail"] else _native_rows(posting["ats"], body)
                parsed_cache[origin] = None if posting["is_detail"] else {p["job_id"]: p for p in parse_job_board(posting["board_url"], body)["postings"]}
            native = native_cache[origin] if posting["is_detail"] else native_cache[origin][posting["job_id"]]
            board = next((b for b in policy["boards"] if BOARDS[b["ats"]].format(token=b["token"]) == posting["board_url"]), None)
            require(board is not None and board["employer"] == posting["employer"] and board["source_note"] == posting["employer_source_note"], "posting employer/board identity mismatch")
            parsed = parsed_cache[origin]
            if parsed is not None:
                candidate = parsed.get(posting["job_id"])
                require(candidate is not None and all(candidate[k] == posting[k] for k in ("title", "url", "location")), "posting differs from native board row")
                require(us_location_ok(posting["location"]) and dedup_key(posting["employer"], posting["job_id"], posting["url"]) == posting["dedup_key"], "invalid geography/dedup identity")
            original, is_html = native_description(posting["ats"], native)
            require(original == raw and is_html == posting["description_is_html"], "description not bound to retrieved board row")
            replay = extract_sections(raw, is_html=is_html, method=posting["extraction_method"])
            require(replay["description"] == text and replay["has_responsibilities"] == posting["has_responsibilities"], "rules extraction replay mismatch")
            expected_quotes = [{**{k: v for k, v in e.items() if k != "quote"}, "source_wording": e["quote"]} for e in replay["expectations"]]
            actual_quotes = [{k: v for k, v in e.items() if k not in ("expectation_id", "source_id")} for e in posting["expectations"]]
            require(expected_quotes == actual_quotes, "expectations not the exact rules extraction")
            for expected in posting["expectations"]:
                require(expected["source_id"] == posting["source_id"] and expected["source_sha256"] == posting["source_sha256"], "expectation source/hash mismatch")
                require(text[expected["start"]:expected["end"]] == expected["source_wording"], "expectation quote is not an exact description span")
            sid = posting["source_id"]
            require(sid not in sources, "source collision")
            sources[sid] = {"id": sid, "sha256": posting["source_sha256"], "extract_sha256": posting["source_sha256"],
                            "retrieved_at": posting["retrieved_at"], "url": posting["url"], "source_type": "job-posting", "inclusion": True}
            extracts[sid] = text
            postings.append(posting)
        from collections import Counter
        for slug in roles:
            selected = [p for p in rows if p["occupation_slug"] == slug]
            counts = Counter(normalized(p["employer"]) for p in selected)
            require(len(selected) <= policy["max_postings_per_role"] <= 500 and (not selected or max(counts.values()) * 5 <= len(selected)), "snapshot violates independent employer/posting cap")
        if upgrade and manifest["extraction_method"] == "rules/1":
            saved = {b["url"]: (within(root, b["path"]).read_bytes(), b["retrieved_at"])
                     for b in manifest["boards"] if b.get("path") and b.get("listing_complete") and not b.get("problems")}
            processed, payloads = capture(VolumePolicy.from_value(policy, root / "manifest.json"), window, saved_boards=saved, frozen_ids={p["dedup_key"] for p in rows})
            old_ids = {p["source_id"] for p in rows}
            postings = [p for p in postings if p["source_id"] not in old_ids]
            for sid in old_ids:
                sources.pop(sid); extracts.pop(sid)
            rows = [p for p in json.loads(payloads["postings.json"]) if p["source_id"] in old_ids]
            require({p["source_id"] for p in rows} == old_ids, "upgraded extraction must retain the frozen admission cohort")
            processed["projection_cohort"] = {"source_snapshot": manifest["snapshot_id"], "postings": len(rows), "policy": "preserve-original-admission"}
            processed["comparison_group"] += ":admission-" + manifest["extraction_method"]
            for posting in rows:
                sid = posting["source_id"]
                text = payloads[posting["description_path"]].decode("utf-8")
                for expected in posting["expectations"]:
                    require(text[expected["start"]:expected["end"]] == expected["source_wording"] and expected["source_sha256"] == posting["source_sha256"], "upgraded quote provenance mismatch")
                sources[sid] = {"id": sid, "sha256": posting["source_sha256"], "extract_sha256": posting["source_sha256"],
                                "retrieved_at": posting["retrieved_at"], "url": posting["url"], "source_type": "job-posting", "inclusion": True}
                extracts[sid] = text
            postings.extend(rows)
            groups[window] = processed["comparison_group"]
            manifests[-1] = processed
            hashes["processed/" + window + "/manifest"] = object_digest(processed)
    require(bool(manifests) and len({m["data_kind"] for m in manifests}) == 1, "missing/mixed snapshot kinds")
    employers = {}
    for posting in postings:
        key = normalized(posting["employer"])
        employers[key] = {"employer": posting["employer"], "status": "unverified", "naics": None,
                          "source_id": posting["source_id"], "source_note": posting["employer_source_note"] + " No verified primary employer sector supplied; excluded."}
    if employer_map:
        hashes["volume_employers.json"] = digest(employer_map.read_bytes())
        seen = set()
        for row in read_json(employer_map):
            key = normalized(row["employer"])
            require(key in employers and key not in seen and row.get("source_note", "").strip(), "unknown/duplicate employer or missing source note")
            seen.add(key)
            require(row["status"] in ("verified", "unverified"), "invalid employer status")
            if row["status"] == "verified":
                require(row.get("basis") == "primary_employer_sector" and row["naics"] in base.skills["sectors"], "verified sector needs explicit primary-employer basis")
                require(row["source_id"] in extracts and row["quote"] in extracts[row["source_id"]], "unverified employer-sector source")
                require(any(normalized(p["employer"]) == key and p["source_id"] == row["source_id"] for p in postings), "sector evidence belongs to a different employer")
            else:
                require(row["naics"] is None, "unverified employer cannot have sector")
            employers[key] = row
    if backbone:
        for posting in postings:
            posting["expectations"] = backbone.expand(posting["occupation_slug"], posting["expectations"])
        hashes["onet31_lookup.json"] = digest(LOOKUP.read_bytes())
        hashes["dwa_rules.json"] = digest(RULES.read_bytes())
        hashes["onet_backbone.py"] = digest(Path(__file__).with_name("onet_backbone.py").read_bytes())
    hashes["posting_sections.py"] = digest(Path(__file__).with_name("posting_sections.py").read_bytes())
    base.postings, base.sources, base.extracts, base.employers = postings, sources, extracts, employers
    base.occupations = {k: v for k, v in base.occupations.items() if k in configured_roles}
    base.skills = deepcopy(base.skills)
    from .role_segments import load_policy
    base.skills["segment_scope"] = load_policy(mapping_root / "ae_segments.json")
    hashes["ae_segments.json"] = digest((mapping_root / "ae_segments.json").read_bytes())
    hashes["role_segments.py"] = digest(Path(__file__).with_name("role_segments.py").read_bytes())
    base.skills["roles"] = {k: v for k, v in base.skills["roles"].items() if k in configured_roles}
    base.skills["backbone"] = [n for n in base.skills["backbone"] if n["role"] in configured_roles]
    base.skills["phrases"] = [p for p in base.skills["phrases"] if p["role"] in configured_roles]
    base.hashes = hashes
    base.release_id = "snapshots_" + object_digest(sorted(m["snapshot_id"] for m in manifests))[:16]
    base.history_groups = groups
    base.data_kind = manifests[0]["data_kind"]
    base.snapshot_roots = tuple(roots)
    for name in ("posting_volume.py", "posting_volume_inputs.py"):
        base.hashes[name] = digest(Path(__file__).with_name(name).read_bytes())
    # Mapping/normalizer changes are sampling-definition changes, never a fabricated trend.
    suffix = object_digest({"processing_recipe": {name: hashes[name] for name in ("recipe.py", "posting_sections.py", "posting_volume_inputs.py", "role_segments.py")}, "skills": base.skills, "backbone": backbone.fingerprint if backbone else None, "employers": {k: {x: v for x, v in row.items() if x not in ("source_id", "quote")} for k, row in employers.items()}})
    base.history_groups = {window: group + ":" + suffix for window, group in groups.items()}
    return base
