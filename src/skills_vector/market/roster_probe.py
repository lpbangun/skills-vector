"""Operator-run ATS token probe; all requests stay on the existing allowlist."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .fetch import fetch_url
from .posting_volume import BOARDS, VolumePolicy, _transport, board_url
from .role_artifacts import guard_output, read_json, require, write_json
from .sources import parse_job_board


def probe(candidates: Path, output: Path, *, payload_root: Path | None = None, live: bool = False, transport=None) -> dict:
    guard_output(output, candidates, *((payload_root,) if payload_root else ()))
    value = read_json(candidates)
    require(live != (payload_root is not None), "select saved probe payloads OR explicit live mode")
    policy = VolumePolicy.from_value(value, candidates)
    value = policy.value
    require(not live or value["data_kind"] == "research", "sample candidates cannot be probed live")
    draft = []
    from .limits import POSTING_VOLUME_LIMITS
    requests = 0
    def reserve():
        nonlocal requests
        require(requests < POSTING_VOLUME_LIMITS["max_retrievals"], "probe retrieval budget exhausted")
        requests += 1
    for board in sorted(value["boards"], key=lambda b: (b["ats"], b["token"])):
        url = board_url(board)
        if board["ats"] == "jsonld":
            from .structured_career import Session, crawl, local_transport
            from .fetch import _now_iso
            try:
                local = None if live else local_transport(payload_root, board["payload"])
                session = Session(value["user_agent"], robots_agent=board.get("robots_agent", value.get("robots_agent")),
                                  transport=transport or local, reserve=reserve, local=not live)
                _, summary = crawl(board, value["roles"], session, board.get("retrieved_at") or _now_iso(), probe=True)
                result = {"ats": "jsonld", "origin": board["origin"], "employer": board["employer"],
                          "exists": summary["sample_parseable"], "reviewed": False, **summary,
                          "source_note": board["source_note"] + " Read-only sitemap probe; human roster review required."}
            except (ValueError, OSError, KeyError, TypeError):
                result = {"ats": "jsonld", "origin": board["origin"], "employer": board["employer"], "exists": None,
                          "robots_allow": None, "sitemap_found": None, "reviewed": False, "error": "structured probe unavailable or denied"}
            draft.append(result)
            continue
        if live:
            response = fetch_url(url, transport=transport or _transport, max_bytes=POSTING_VOLUME_LIMITS["max_response_bytes"],
                                 retries=POSTING_VOLUME_LIMITS["max_retries"])
            status, body, retrieved_at, error = response.status, response.body, response.retrieved_at, response.error
        else:
            path = (payload_root / board["payload"]).resolve()
            require(path.is_relative_to(payload_root.resolve()), "probe payload path escapes local root")
            body, status, retrieved_at, error = path.read_bytes(), board.get("sample_status", 200), board.get("retrieved_at"), None
        parsed = parse_job_board(url, body) if status == 200 else None
        exists = status == 200 and parsed is not None and not any("missing " in p or "parse failed" in p or "not a json" in p for p in parsed["problems"])
        counts = {role: sum(any(re.search(r"(?<!\w)" + re.escape(hint) + r"(?!\w)", p["title"], re.I) for hint in hints)
                            for p in parsed["postings"]) if exists else 0 for role, hints in value["roles"].items()}
        draft.append({k: board[k] for k in ("ats", "token", "employer")} | {
            "exists": exists, "status": status, "retrieved_at": retrieved_at, "response_sha256": hashlib.sha256(body).hexdigest(),
            "postings_total": parsed["board_total"] if exists and parsed["board_total"] is not None else len(parsed["postings"]) if exists else None,
            "listing_complete": parsed["listing_complete"] if exists else False, "matched_titles": counts,
            "error": error, "reviewed": False,
            "source_note": board["source_note"] + " Probe of " + url + "; title counts are not US admission or distinct employer counts. Human roster review required."})
    result = {"schema": "skills-vector.roster-draft/1", "data_kind": value["data_kind"], "reviewed": False,
              "roles": value["roles"], "boards": draft}
    write_json(output, result, inputs=(candidates,) + ((payload_root,) if payload_root else ()))
    return result
