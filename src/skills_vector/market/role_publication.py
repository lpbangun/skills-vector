"""Small publication manifests; detailed audits live only in operator evidence storage."""
from __future__ import annotations

import hashlib
import json

from .role_overview import overview
from pathlib import Path

from .role_artifacts import ROOT, canonical, digest, guard_output, read_json, require, write_json

EVIDENCE_ROOT = Path.home() / ".local/share/skills-vector-evidence/role-atlas/releases"


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def publish(records: list[dict], audit: dict, captures: tuple[Path, ...], output: Path,
            evidence_root: Path = EVIDENCE_ROOT, *, protected: tuple[Path, ...] = (),
            tail_label_limit: int | None = None) -> dict:
    from .structured_career import no_contacts
    require(no_contacts([records, audit]), "publication contains non-data content; no files written")
    output = guard_output(output, *captures, *protected)
    audit_path = guard_output(evidence_root / audit["release_id"] / "audit.json", output, *captures, *protected)
    require(not audit_path.is_relative_to(ROOT), "full audits must be outside the repository")
    require({r["release_id"] for r in records} == {audit["release_id"]}, "publication release mismatch")
    inputs = []
    for capture in captures:
        manifest = capture / "manifest.json"
        hashes = {str(p.relative_to(capture)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(capture.rglob("*")) if p.is_file() and not any(s.startswith(".env") for s in p.parts)}
        require(len(hashes) == sum(p.is_file() for p in capture.rglob("*")), "capture contains non-data files")
        inputs.append({"id": read_json(manifest)["snapshot_id"], "sha256": digest(hashes),
                       "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()})
    receipt = {"schema": "skills-vector.role-release/1", "release_id": audit["release_id"],
               "input_release_id": audit["input_release_id"], "captures": inputs,
               "audit": {"sha256": digest(audit), "bytes": len(canonical(audit).encode())},
               "recipe": audit["recipe"],
               "foundation_sha256": digest({k: v for k, v in audit["input_hashes"].items() if k.startswith("release/")}),
               "input_hashes": {k: v for k, v in audit["input_hashes"].items() if not k.startswith(("release/", "snapshot/", "processed/"))},
               "roles": {r["id"]: {"postings": r["demand_window"]["postings"], "employers": r["demand_window"]["employers"],
                           "skills": len(r["skill_map"]["skills"]),
                           "skills_by_status": {s: sum(n["status"] == s for n in r["skill_map"]["skills"]) for s in ("core", "rising", "early", "fading")},
                           "postings_per_level": {**{"L" + str(l["level"]): l["evidence"]["postings"] for l in r["ladder"]["levels"]},
                                "unassigned": r["demand_window"]["postings"] - sum(l["evidence"]["postings"] for l in r["ladder"]["levels"])},
                           "edges": len(r["skill_map"]["asked_together"]),
                           "record_sha256": digest(r),
                           "file_sha256": hashlib.sha256(encoded(r)).hexdigest(),
                           "overview_sha256": hashlib.sha256(encoded(overview(r, tail_label_limit=tail_label_limit))).hexdigest(),
                           "summary": {k: r[k] for k in ("id", "label", "status", "publication", "data_kind", "release_id", "anchors", "demand_window", "limits")}} for r in records}}
    for record in records:
        quality = audit.get("roles", {}).get(record["id"].removeprefix("role."), {}).get("rules3")
        if quality is not None:
            receipt["roles"][record["id"]]["skill_filter"] = quality
    payloads = {output / "roles" / (r["id"].removeprefix("role.") + ".json"): r for r in records}
    payloads.update({output / "views" / (r["id"].removeprefix("role.") + ".json"): overview(r, tail_label_limit=tail_label_limit) for r in records})
    payloads[output / "release.json"] = receipt
    require(not output.exists() or {p for p in output.rglob("*") if p.is_file()} <= set(payloads), "unrelated publication files")
    # Preflight before any write. Never replace a different release or audit.
    for path, value in payloads.items():
        require(not path.exists() or path.read_bytes() == encoded(value), "different publication already exists")
    write_json(audit_path, audit, inputs=(*captures, output, *protected))
    for path, value in payloads.items():
        write_json(path, value, compact=True, inputs=(*captures, *protected))
    return receipt


def read_audit(publication: Path, evidence_root: Path = EVIDENCE_ROOT) -> tuple[dict | None, str]:
    manifest = read_json(publication / "release.json")
    rid = manifest["release_id"]
    require(isinstance(rid, str) and rid and all(c.isalnum() or c in "._-" for c in rid), "invalid release id")
    path = evidence_root / rid / "audit.json"
    if not path.exists():
        return None, "audit not available locally"
    data = path.read_bytes()
    require(len(data) == manifest["audit"]["bytes"] and hashlib.sha256(data).hexdigest() == manifest["audit"]["sha256"], "audit hash/size mismatch")
    value = read_json(path)
    require(value["release_id"] == rid, "audit release mismatch")
    return value, "audit verified locally"
