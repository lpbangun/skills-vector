"""Quarterly public-board capture and versioned no-model extraction. No model/provider dependencies."""
from __future__ import annotations

import hashlib
from copy import deepcopy
import html
import json
import re
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from .fetch import AllowlistRedirectHandler, fetch_url
from .hosts import url_problem
from .limits import POSTING_VOLUME_LIMITS
from .role_artifacts import canonical, digest, guard_output, read_json, require
from .sources import dedup_key, normalize_ws, parse_job_board, us_location_ok

SCHEMA = "skills-vector.posting-snapshot/1"
EXTRACTION = "rules/2"
NORMALIZATION = "phrase-rules/1"
BOARDS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{token}/",
    "jsonld": "{token}",
}
HEADINGS = {
    "preferred": r"^(?:preferred(?: qualifications| skills)?|nice to have|bonus(?: points)?|it.s a plus)",
    "requirements": r"^(?:requirements|qualifications|minimum qualifications|what you(?:.ll| will)? bring|what we.re looking for|who you are|skills and experience|you have)",
    "responsibilities": r"^(?:responsibilities|your responsibilities|what you(?:.ll| will)? do|what you.ll work on|your impact|the role|about the role|duties|job description)",
    "company": r"^(?:about (?:us|the company)|who we are|our company)",
    "benefits": r"^(?:benefits|perks|what we offer|compensation|salary)",
    "legal": r"^(?:equal opportunity|legal|privacy|additional information|accommodations)",
}
BOILERPLATE = re.compile(r"\b(?:equal opportunity employer|reasonable accommodation|401\(?k\)?|paid time off|medical insurance|privacy policy|applicant privacy|all qualified applicants|we do not discriminate)\b", re.I)


def quarter(timestamp: str) -> str:
    stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    require("T" in timestamp and stamp.utcoffset() is not None, "retrieval timestamp needs timezone")
    stamp = stamp.astimezone(UTC)
    return f"{stamp.year:04d}-Q{(stamp.month - 1) // 3 + 1}"


def normalize_phrase(quote: str) -> str:
    """Identity/label only: never edits a quote or asserts an O*NET equivalence."""
    value = normalize_ws(quote).casefold()
    value = re.sub(r"^(?:(?:you (?:will|must|should)|demonstrated|proven)\s+)", "", value)
    while True:
        shorter = re.sub(r"^(?:experience (?:with|in)|ability to|knowledge of|proficiency (?:with|in)|familiarity with)\s+", "", value)
        if shorter == value:
            break
        value = shorter
    value = re.sub(r"\s+(?:is a plus|is preferred|preferred)\s*[.!;:]?$", "", value)
    return value.strip(" .;:")


class _Blocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.parts = [], []
        self.skip = 0
        self.kind = "paragraph"
        self.in_list = 0

    def flush(self):
        text = "".join(self.parts).strip()
        if text:
            self.blocks.append((self.kind, text))
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in ("p", "div", "br") and self.in_list:
            self.parts.append("\n")
        elif tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            if tag == "li":
                self.in_list += 1
            self.kind = "heading" if tag.startswith("h") else "bullet" if self.in_list else "paragraph"

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        elif tag in ("p", "div") and self.in_list:
            self.parts.append("\n")
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            if tag == "li":
                self.in_list = max(0, self.in_list - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extract_sections_v1(description: str, *, is_html: bool = True) -> dict:
    if is_html:
        parser = _Blocks()
        parser.feed(description); parser.close(); parser.flush()
        blocks = parser.blocks
    else:
        blocks = [("bullet" if re.match(r"^\s*[-•*]\s+", line) else "paragraph",
                   re.sub(r"^\s*[-•*]\s+", "", line).strip()) for line in description.splitlines() if line.strip()]
    text = "\n".join(value for _, value in blocks)
    sha = hashlib.sha256(text.encode()).hexdigest()
    section, heading, cursor = "unknown", "", 0
    expectations, sections = [], defaultdict(list)
    for kind, value in blocks:
        start = text.index(value, cursor); cursor = start + len(value)
        name = value.strip().rstrip(":").casefold()
        matched = next((key for key, pattern in HEADINGS.items() if re.search(pattern, name)), None)
        # Heading-like text is bounded: a long duty sentence containing a heading word is not a heading.
        if matched and (kind == "heading" or (kind != "bullet" and len(value) <= 85)):
            section, heading = matched, value
            continue
        if kind == "heading":
            section, heading = "unknown", value
            continue
        if section not in ("responsibilities", "requirements", "preferred") or BOILERPLATE.search(value):
            continue
        if not normalize_phrase(value):
            continue
        entry = {"quote": value, "start": start, "end": cursor, "section": section, "heading": heading,
                 "source_sha256": sha, "dimension": "task" if section == "responsibilities" else "capability",
                 "basis": "employer_preference" if section == "preferred" else "advertised_demand" if section == "responsibilities" else "employer_requirement"}
        expectations.append(entry)
        sections[section].append(value)
    return {"description": text, "source_sha256": sha, "expectations": expectations, "sections": dict(sections),
            "has_responsibilities": bool(sections.get("responsibilities"))}


def extract_sections(description: str, *, is_html: bool = True, method: str = EXTRACTION,
                     omit_contacts: bool = True, flat_headings: bool = True,
                     employer: str | None = None) -> dict:
    from .flat_headings import METHOD as FLAT, eligible, extract as extract_flat
    require(method in ("rules/1", "rules/2", FLAT), "unsupported extraction method")
    if method == FLAT or method == "rules/2" and flat_headings and eligible(description):
        result = extract_flat(description, employer=employer)
        if method != FLAT and not result["section_boundaries"]:
            from .posting_sections import extract
            legacy = extract(description, is_html=is_html)
            if legacy["expectations"]:
                result = legacy
    elif method == "rules/1":
        result = extract_sections_v1(description, is_html=is_html)
    else:
        from .posting_sections import extract
        result = extract(description, is_html=is_html)
    if omit_contacts:
        from .role_quotes import omit_contact_sentences
        result["expectations"], omitted = omit_contact_sentences(result["expectations"], result["description"])
        result["contact_spans_omitted"] = len(omitted)
        result["contact_omissions"] = omitted
    return result


def fair_sample(rows: list[dict], maximum: int) -> list[dict]:
    require(type(maximum) is int and 1 <= maximum <= POSTING_VOLUME_LIMITS["max_postings_per_role"], "posting policy exceeds independent hard ceiling")
    buckets = defaultdict(list)
    for row in sorted(rows, key=lambda p: p["dedup_key"]):
        buckets[normalize_ws(row["employer"]).casefold()].append(row)
    # Largest feasible total. Four employers cannot form a nonempty <=20% sample.
    total = min(maximum, len(rows))
    while total and sum(min(len(v), total // 5) for v in buckets.values()) < total:
        total -= 1
    if not total:
        return []
    cap, selected = total // 5, []
    used = Counter()
    while len(selected) < total:
        for employer in sorted(buckets):
            if used[employer] < min(cap, len(buckets[employer])) and len(selected) < total:
                selected.append(buckets[employer][used[employer]])
                used[employer] += 1
    return sorted(selected, key=lambda p: p["dedup_key"])


@dataclass
class VolumePolicy:
    value: dict
    path: Path

    @classmethod
    def read(cls, path: Path):
        return cls.from_value(read_json(path), path)

    @classmethod
    def from_value(cls, value: dict, path: Path):
        value = deepcopy(value)
        require(value.get("schema") == "skills-vector.posting-policy/1", "unsupported posting policy")
        require(value.get("window_length") == "quarterly", "posting lane uses settled quarterly windows")
        require(value.get("data_kind") in ("fixture", "research"), "posting policy must label data kind")
        maximum = value.get("max_postings_per_role")
        require(type(maximum) is int and 1 <= maximum <= POSTING_VOLUME_LIMITS["max_postings_per_role"], "posting policy exceeds independent hard ceiling")
        require(0 < len(value["boards"]) <= POSTING_VOLUME_LIMITS["max_boards"], "board policy exceeds hard ceiling")
        require(bool(value["roles"]), "no configured roles")
        require(all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) and isinstance(hints, list) and hints and all(isinstance(h, str) and h.strip() for h in hints) for slug, hints in value["roles"].items()), "roles require explicit literal title hints")
        from .structured_career import validate_robots_agent, no_contacts
        require(no_contacts(recorded_policy(value)), "non-data policy metadata")
        validate_robots_agent(value.get("robots_agent"))
        identities = set()
        for board in value["boards"]:
            if board["ats"] == "jsonld":
                from .structured_career import validate_board, safe_url
                validate_board(board)
                board["origin"] = safe_url(board["origin"])
                require(isinstance(value.get("user_agent"), str) and value["user_agent"].strip() or bool(re.fullmatch(r"[a-f0-9]{64}", value.get("user_agent_sha256", ""))), "structured lane requires operator User-Agent")
                board["token"] = hashlib.sha256(board["origin"].encode()).hexdigest()[:16]
                board["source_note"] = board.get("note", board.get("source_note"))
            require(board["ats"] in BOARDS and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", board["token"])), "unsupported board/token")
            require(isinstance(board["employer"], str) and board["employer"].strip() and isinstance(board.get("source_note"), str) and board["source_note"].strip(), "board needs employer and source note")
            key = (board["ats"], board["token"])
            require(key not in identities, "duplicate board")
            identities.add(key)
            if board["ats"] != "jsonld":
                require(url_problem(board_url(board)) is None, "board is not allowlisted")
        return cls(value, path.resolve())


def board_url(board: dict) -> str:
    return board["origin"] if board["ats"] == "jsonld" else BOARDS[board["ats"]].format(token=board["token"])


def recorded_policy(value: dict) -> dict:
    result = deepcopy(value)
    if "user_agent" in result:
        result["user_agent_sha256"] = hashlib.sha256(result.pop("user_agent").encode()).hexdigest()
    return result


def parse_volume_board(ats: str, url: str, body: bytes) -> dict:
    if ats == "jsonld":
        from .structured_career import parse_collection
        return parse_collection(body)
    return parse_job_board(url, body)


def _native_rows(ats: str, payload: bytes) -> dict[str, dict]:
    value = json.loads(payload)
    rows = value if ats == "lever" else value.get("jobs" if ats != "smartrecruiters" else "content", [])
    return {str(r.get("id", r.get("shortcode", ""))): r for r in rows}


def native_description(ats: str, native: dict) -> tuple[str, bool]:
    if ats == "jsonld":
        return native["description"], True
    if ats == "greenhouse":
        return html.unescape(native.get("content") or native.get("description") or ""), True
    if ats == "lever":
        body = native.get("description") or ""
        if not body:
            body = "<p>" + html.escape(native.get("descriptionPlain") or "").replace("\n", "<br>") + "</p>"
        for item in native.get("lists", []):
            body += "<h2>" + html.escape(item["text"]) + "</h2>" + item["content"]
        body += "<h2>Additional information</h2>" + (native.get("additional") or "")
        return body, True
    if ats == "ashby":
        return (native["descriptionHtml"], True) if native.get("descriptionHtml") else (native.get("descriptionPlain", ""), False)
    if ats == "smartrecruiters":
        sections = native.get("jobAd", {}).get("sections", {})
        return "".join("<h2>" + title + "</h2>" + sections.get(key, {}).get("text", "") for key, title in
                       (("companyDescription", "About the company"), ("jobDescription", "Responsibilities"), ("qualifications", "Requirements"), ("additionalInformation", "Additional information"))), True
    return (native.get("description") or "") + "<h2>Requirements</h2>" + (native.get("requirements") or ""), True


def _transport(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "skills-vector-rules/2", "Accept": "application/json"})
    opener = urllib.request.build_opener(AllowlistRedirectHandler)
    with opener.open(request, timeout=POSTING_VOLUME_LIMITS["retrieval_timeout_seconds"]) as response:
        return int(response.status), {k.lower(): v for k, v in response.headers.items()}, response.read(POSTING_VOLUME_LIMITS["max_response_bytes"] + 1)


def capture(policy: VolumePolicy, window: str, *, payload_root: Path | None = None,
            retrieved_at: str | None = None, live: bool = False, transport=None, saved_boards: dict | None = None, frozen_ids: set[str] | None = None, extraction_method: str = EXTRACTION, structured_session=None) -> tuple[dict, dict[str, bytes]]:
    require(extraction_method in ("rules/1", "rules/2"), "unsupported extraction method")
    require(bool(re.fullmatch(r"[0-9]{4}-Q[1-4]", window)), "quarter must be YYYY-Qn")
    require(sum((bool(live), payload_root is not None, saved_boards is not None)) == 1, "select local payloads OR saved capture OR explicit live mode")
    require(not live or policy.value["data_kind"] == "research", "live retrieval cannot use a sample policy")
    require(live or saved_boards is not None or retrieved_at is not None, "local capture requires the recorded retrieval timestamp")
    if retrieved_at:
        require(quarter(retrieved_at) == window, "retrieval timestamp is outside requested quarter")
    payloads, postings, excluded, boards, complete, section_checks = {}, [], [], [], [], []
    retrievals = 0
    def reserve():
        nonlocal retrievals
        require(retrievals < POSTING_VOLUME_LIMITS["max_retrievals"], "posting lane retrieval ceiling exhausted")
        retrievals += 1
    def retrieve(url):
        reserve()
        return fetch_url(url, transport=transport or _transport, max_bytes=POSTING_VOLUME_LIMITS["max_response_bytes"], retries=POSTING_VOLUME_LIMITS["max_retries"])
    for board in sorted(policy.value["boards"], key=lambda b: (b["ats"], b["token"])):
        ats, token, employer = board["ats"], board["token"], board["employer"]
        url = board_url(board)
        stamp = retrieved_at
        structured_summary = None
        if ats == "jsonld" and saved_boards is None:
            from .structured_career import Session, crawl, local_transport
            from .fetch import _now_iso
            require(not live or policy.value.get("user_agent"), "live structured requests require the operator User-Agent")
            stamp = stamp or _now_iso()
            try:
                if structured_session is not None:
                    session = structured_session
                else:
                    local = None if live else local_transport(payload_root, board["payload"])
                    session = Session(policy.value["user_agent"], robots_agent=board.get("robots_agent", policy.value.get("robots_agent")),
                                      transport=transport or local, reserve=reserve, local=not live)
                collection, structured_summary = crawl(board, policy.value["roles"], session, stamp,
                                                       roster=policy.value["boards"])
                body = canonical(collection).encode()
            except (ValueError, OSError, KeyError, TypeError):
                boards.append({"url": url, "error": "structured discovery unavailable or rejected by policy"})
                continue
        elif live:
            result = retrieve(url)
            stamp = result.retrieved_at
            if not result.ok:
                boards.append({"url": url, "error": result.error or "non-200/truncated response"})
                continue
            body = result.body
        elif saved_boards is not None:
            if url not in saved_boards:
                boards.append({"url": url, "error": "No successful response in read-only source capture"})
                continue
            body, stamp = saved_boards[url]
        else:
            source = (payload_root / board["payload"]).resolve()
            require(source.is_relative_to(payload_root.resolve()) and source.is_file() and not any(p.startswith(".env") for p in source.parts), "unsafe/missing board payload")
            require(policy.value["data_kind"] == "fixture" or ".sample." not in source.name.lower(), "sample payload cannot enter research")
            body = source.read_bytes()
        require(quarter(stamp) == window, "retrieved board is outside requested quarter")
        require(len(body) <= POSTING_VOLUME_LIMITS["max_response_bytes"], "board exceeds hard byte ceiling")
        parsed = parse_volume_board(ats, url, body)
        board_hash = hashlib.sha256(body).hexdigest()
        name = "boards/" + board_hash + ".json"
        payloads[name] = body
        boards.append({"url": url, "employer": employer, "retrieved_at": stamp, "sha256": board_hash,
                       "path": name, "listing_complete": parsed["listing_complete"], "problems": parsed["problems"]})
        if structured_summary is not None:
            boards[-1]["structured"] = structured_summary
        if not parsed["listing_complete"] or parsed["problems"] or (parsed["board_total"] is not None and parsed["board_total"] > len(parsed["postings"])):
            boards[-1]["coverage_error"] = "incomplete or paginated listing; excluded from comparison"

            continue
        complete.append((ats, token))
        native = _native_rows(ats, body)
        for candidate in parsed["postings"]:
            matches = [slug for slug, hints in policy.value["roles"].items() if any(re.search(r"(?<!\w)" + re.escape(hint) + r"(?!\w)", candidate["title"], re.I) for hint in hints)]
            key = dedup_key(employer, candidate["job_id"], candidate["url"])
            reason = "ambiguous_or_unmatched_role_title" if len(matches) != 1 else "not_explicit_US" if not us_location_ok(candidate["location"]) else None
            description, is_html = native_description(ats, native[candidate["job_id"]])
            origin_hash, is_detail = board_hash, False
            # Existing list parsers for detail-only boards intentionally contain no body.
            needs_detail = ats == "greenhouse" and not description or ats == "smartrecruiters" and not native[candidate["job_id"]].get("jobAd")
            if needs_detail and live and not reason:
                detail_url = (f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{candidate['job_id']}" if ats == "greenhouse" else f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{candidate['job_id']}")
                detail = retrieve(detail_url)
                if detail.ok:
                    detail_path = "boards/" + detail.sha256 + ".json"
                    payloads[detail_path] = detail.body
                    description, is_html = native_description(ats, json.loads(detail.body))
                    origin_hash, is_detail = detail.sha256, True
                    require(quarter(detail.retrieved_at) == window, "detail retrieval crossed quarter boundary")
                    stamp = detail.retrieved_at
            extracted = extract_sections(description, is_html=is_html, method=extraction_method, employer=employer)
            if len(matches) == 1 and us_location_ok(candidate["location"]):
                section_checks.append({"posting_id": key, "role": matches[0], "job_id": candidate["job_id"], "employer": employer,
                                       "title": candidate["title"], "has_responsibilities": extracted["has_responsibilities"]})
            if not extracted["expectations"] and not extracted["contact_spans_omitted"] and frozen_ids is None:
                reason = reason or "no_recognized_expectation_section"
            if reason:
                excluded.append({"posting_id": key, "title": candidate["title"], "employer": employer, "reason": reason,
                                 "has_responsibilities": extracted["has_responsibilities"]})
                continue
            source_id = "desc." + window.replace("-", "") + "." + key
            text_path = "descriptions/" + source_id + ".txt"
            raw_path = "descriptions/" + source_id + ".raw"
            payloads[text_path] = extracted["description"].encode()
            payloads[raw_path] = description.encode()
            expectations = [{"expectation_id": "expect." + digest([e["dimension"], e["quote"]])[:20],
                             "source_wording": e["quote"], "source_id": source_id, **{k: v for k, v in e.items() if k != "quote"}}
                            for e in extracted["expectations"]]
            postings.append({"occupation_slug": matches[0], "dedup_key": key, "employer": employer,
                             "title": candidate["title"], "url": candidate["url"], "job_id": candidate["job_id"],
                             "location": candidate["location"], "source_id": source_id, "source_sha256": extracted["source_sha256"],
                             "description_path": text_path, "raw_description_path": raw_path,
                             "raw_description_sha256": hashlib.sha256(description.encode()).hexdigest(),
                             "board_sha256": origin_hash, "board_url": url, "ats": ats, "is_detail": is_detail,
                             "description_is_html": is_html, "retrieved_at": stamp, "extraction_method": extracted.get("extraction_method", extraction_method),
                             "has_responsibilities": extracted["has_responsibilities"], "expectations": expectations,
                             "contact_spans_omitted": extracted["contact_spans_omitted"], "contact_omissions": extracted["contact_omissions"],
                             "work_level": "unknown", "advertised_experience": {"value": []},
                             "employer_source_note": board["source_note"]})
            if ats == "jsonld":
                postings[-1].update(source_kind="jsonld", page_sha256=native[candidate["job_id"]]["page_sha256"],
                                    identifier=native[candidate["job_id"]]["identifier"],
                                    hiring_entity=native[candidate["job_id"]].get("hiring_entity"))
    if any(p["ats"] == "jsonld" for p in postings):
        from .structured_career import cross_kind_dedup
        postings, cross_duplicates = cross_kind_dedup(postings)
        excluded.extend(cross_duplicates)
    admitted, seen = [], set()
    for row in sorted(postings, key=lambda p: (p["occupation_slug"], p["dedup_key"], p["source_sha256"])):
        identity = (row["occupation_slug"], row["dedup_key"])
        if identity in seen:
            excluded.append({"posting_id": row["dedup_key"], "reason": "duplicate"})
            continue
        seen.add(identity)
        admitted.append(row)
    selected, reports = [], {}
    for slug in sorted(policy.value["roles"]):
        candidates = [p for p in admitted if p["occupation_slug"] == slug]
        chosen = ([p for p in candidates if p["dedup_key"] in frozen_ids] if frozen_ids is not None else fair_sample(candidates, policy.value["max_postings_per_role"]))
        require(len(chosen) <= policy.value["max_postings_per_role"] and (not chosen or max(Counter(normalize_ws(p["employer"]).casefold() for p in chosen).values()) * 5 <= len(chosen)), "frozen cohort violates admission floors")
        selected.extend(chosen)
        ids = {p["dedup_key"] for p in chosen}
        excluded.extend({"posting_id": p["dedup_key"], "reason": "fair_employer_cap_or_policy_limit"} for p in candidates if p["dedup_key"] not in ids)
        reports[slug] = {"contact_spans_omitted": sum(p.get("contact_spans_omitted", 0) for p in chosen), "candidates": len(candidates), "admitted": len(chosen), "employers": dict(sorted(Counter(p["employer"] for p in chosen).items())),
                         "eligible_postings": sum(p["role"] == slug for p in section_checks),
                         "responsibilities": sum(p["has_responsibilities"] for p in section_checks if p["role"] == slug),
                         "responsibility_failures": [p["posting_id"] for p in section_checks if p["role"] == slug and not p["has_responsibilities"]]}
    public_policy = recorded_policy(policy.value)
    flat_recipe = None
    if extraction_method == "rules/2":
        flat_recipe = {"method": "flat-headings/1", "sha256": hashlib.sha256(Path(__file__).with_name("flat_headings.py").read_bytes()).hexdigest(),
                       "sentence_spans_sha256": hashlib.sha256(Path(__file__).with_name("role_quotes.py").read_bytes()).hexdigest()}
    group = digest({"recipe": extraction_method, "flat_extraction": flat_recipe, "normalization": NORMALIZATION, "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "policy": {k: v for k, v in public_policy.items() if k != "boards"},
                    "boards": [{k: v for k, v in b.items() if k != "payload"} for b in sorted(policy.value["boards"], key=lambda b: (b["ats"], b["token"]))],
                    "complete_boards": complete})
    manifest = {"schema": SCHEMA, "data_kind": policy.value["data_kind"], "quarter": window, "window_length": "quarterly",
                "contact_filter": "sentences/1",
                "contact_spans_omitted": sum(p.get("contact_spans_omitted", 0) for p in selected),
                "comparison_group": extraction_method + ":" + group, "normalization": NORMALIZATION, "policy_sha256": digest(public_policy), "extraction_method": extraction_method,
                "boards": boards, "roles": reports, "exclusions": excluded, "section_checks": section_checks, "policy": public_policy,
                "limits": ["Quarterly cross-sectional captured listings, not new-job flows or market prevalence.",
                           "Rules only: visible text is decoded from native HTML; quote offsets/hash refer to the exact saved description text. Original native description and board bytes are separately hashed.",
                           "No employer sector, title-derived level or official skill equivalence is inferred."] }
    if flat_recipe:
        manifest["flat_extraction"] = flat_recipe
    data = canonical(sorted(selected, key=lambda p: (p["occupation_slug"], p["dedup_key"]))).encode()
    payloads["postings.json"] = data
    manifest["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())}
    manifest["snapshot_id"] = digest(manifest)
    return manifest, payloads


def write_snapshot(policy: VolumePolicy, output: Path, window: str, **kwargs) -> dict:
    require(bool(re.fullmatch(r"[0-9]{4}-Q[1-4]", window)), "quarter must be YYYY-Qn")
    target = guard_output(output / window, policy.path, *((kwargs["payload_root"],) if kwargs.get("payload_root") else ()))
    manifest, files = capture(policy, window, **kwargs)
    files["manifest.json"] = canonical(manifest).encode()
    from .structured_career import no_contacts
    require(no_contacts(manifest["policy"]), "snapshot policy contains non-data metadata; no files written")
    existing = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()} if target.exists() else set()
    require(not existing - files.keys(), "unrelated snapshot output")
    for name, data in files.items():
        path = guard_output(target / name)
        require(not path.exists() or path.read_bytes() == data, "snapshot is immutable; choose a new output root")
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"snapshot_id": manifest["snapshot_id"], "quarter": window, "data_kind": manifest["data_kind"], "roles": manifest["roles"],
            "excluded": len(manifest["exclusions"]), "boards": len(manifest["boards"])}
