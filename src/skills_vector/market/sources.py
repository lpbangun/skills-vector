"""Public-source parsing: HTML->text, job-board payload extraction, excerpts.

All published extracts are rights-safe: they contain a metadata header plus
verified short excerpt spans (and, for official foundations, a bounded excerpt
of the public text with attribution). Full raw responses stay in the external
evidence root and are never committed or served.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlsplit

from .limits import RESEARCH_LIMITS

WS_RE = re.compile(r"\s+")

US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la",
    "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}

# Unambiguous US metro names that appear without a state code (e.g. "San Francisco- Remote").
US_CITY_MARKERS = (
    "san francisco", "new york", "los angeles", "san diego", "san jose", "mountain view", "palo alto",
    "sunnyvale", "bellevue", "chicago", "austin", "boston", "seattle", "denver", "atlanta", "phoenix",
    "scottsdale", "tempe", "dallas", "houston", "san antonio", "fort worth", "plano", "miami", "orlando",
    "tampa", "washington", "portland", "philadelphia", "pittsburgh", "minneapolis", "detroit", "raleigh",
    "charlotte", "nashville", "salt lake", "boulder", "irvine", "santa monica", "oakland", "sacramento",
    "las vegas", "kansas city", "st. louis", "columbus", "cleveland", "cincinnati", "indianapolis",
    "milwaukee", "baltimore", "jacksonville",
)

# Two-letter codes that are ISO country codes: written lowercase they are foreign country
# suffixes in public ATS payloads ("Gerlingen, BW, de"), while uppercase forms stay US state
# codes ("Denver, CO"). Checked only for the trailing component, after US-metro recognition.
FOREIGN_COUNTRY_CODES = {
    "de", "in", "ca", "mx", "br", "pl", "hu", "pt", "nl", "ch", "at", "be", "dk", "no", "se", "fi",
    "cz", "ro", "tr", "za", "eg", "vn", "ph", "my", "th", "id", "kr", "tw", "hk", "ar", "cl", "co",
    "pe", "ie", "gb", "fr", "it", "es", "il", "jp", "sg", "ae", "ua", "rs", "sk", "si", "lt", "lv",
    "ee", "gr", "bg", "hr", "is", "lu", "mt", "cy", "nz", "au", "cn", "py", "uy",
}

US_LOCATION_RE = re.compile(r"\b(united states|usa|u\.s\.|us[- ]remote|remote[- ]us|us)\b")

NON_US_MARKERS = (
    "canada", "toronto", "vancouver", "montreal", "ontario", "quebec", "london", "united kingdom", "uk -",
    "england", "ireland", "dublin", "germany", "berlin", "munich", "france", "paris", "netherlands",
    "amsterdam", "spain", "madrid", "barcelona", "italy", "milan", "portugal", "lisbon", "poland",
    "warsaw", "sweden", "stockholm", "denmark", "copenhagen", "norway", "oslo", "switzerland", "zurich",
    "austria", "vienna", "belgium", "brussels", "israel", "tel aviv", "india", "bangalore", "bengaluru",
    "hyderabad", "mumbai", "delhi", "gurgaon", "japan", "tokyo", "singapore", "australia", "sydney",
    "melbourne", "new zealand", "auckland", "brazil", "sao paulo", "mexico", "mexico city", "argentina",
    "colombia", "bogota", "chile", "santiago", "philippines", "manila", "vietnam", "hanoi", "china",
    "shanghai", "beijing", "hong kong", "taiwan", "taipei", "korea", "seoul", "uae", "dubai", "egypt",
    "cairo", "kenya", "nairobi", "nigeria", "lagos", "south africa", "cape town", "turkey", "istanbul",
    "czech", "prague", "romania", "bucharest", "hungary", "budapest", "ukraine", "kyiv", "russia",
)

SENIORITY_BAND_TITLES = (
    r"intern|internship|co-?op|apprentice|junior|jr\.?|entry[- ]level|new grad|graduate|"
    r"principal|staff\+?|director|vp|vice president|head of|chief|president|founder|owner"
)

SENIORITY_EXCLUDE_TITLE_RE = re.compile(
    rf"\b({SENIORITY_BAND_TITLES}|managing partner|partner)\b",
    re.IGNORECASE,
)

# Role-specific bands. For HR generalists, "partner" is the canonical mid-level IC title
# ("HR Business Partner", "People Partner"), so it stays in scope for that occupation.
SENIORITY_BANDS: dict[str, re.Pattern[str]] = {
    "hr-generalist": re.compile(rf"\b({SENIORITY_BAND_TITLES})\b", re.IGNORECASE),
}

SENIOR_SEARCH_TERMS_RE = re.compile(r"\b(principal|staff|director|vp|head|chief|intern|junior)\b", re.IGNORECASE)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
        elif tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def normalize_ws(text: str) -> str:
    return WS_RE.sub(" ", html.unescape(str(text))).strip()


def html_to_text(source: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(source)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not abort retrieval
        pass
    return normalize_ws(" ".join(parser.parts))


def escaped_html_to_text(source: str) -> str:
    """Text for payloads that deliver HTML entity-escaped markup.

    Greenhouse's ``content`` field is escaped (``&lt;p&gt;…``), so the entities
    have to be decoded before tag stripping or the markup survives as text.
    """

    return html_to_text(html.unescape(str(source or "")))


def _json_load(payload: bytes) -> Any:
    return json.loads(payload.decode("utf-8", errors="replace"))


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return None


MAX_PREFIX_ROWS = 5000


def _decode_json_prefix(text: str, container: str | None) -> tuple[list[Any], bool]:
    """Decode complete objects from a (possibly truncated) JSON array prefix.

    Bounded byte scanning with ``JSONDecoder.raw_decode``: a response cut at the
    response-size cap still yields every complete element before the cut. Returns
    ``(rows, complete)`` where ``complete`` says whether the array terminator was
    reached inside the payload.
    """

    if container:
        key = f'"{container}"'
        key_pos = text.find(key)
        if key_pos == -1:
            return [], False
        start = text.find("[", key_pos + len(key))
    else:
        start = text.find("[")
    if start == -1:
        return [], False
    decoder = json.JSONDecoder()
    rows: list[Any] = []
    index = start + 1
    while len(rows) < MAX_PREFIX_ROWS:
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        if index >= len(text):
            return rows, False
        if text[index] == "]":
            return rows, True
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return rows, False
        rows.append(value)
    return rows, False


def _list_rows(payload: bytes, container: str | None) -> tuple[list[Any], bool, str | None]:
    """Return ``(rows, complete, problem)`` for a job-board list payload."""

    text = payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        rows, complete = _decode_json_prefix(text, container)
        if rows:
            return rows, complete, f"json prefix recovered after {exc.__class__.__name__}"
        return [], False, f"json parse failed: {exc}"
    if container is None:
        if isinstance(data, list):
            return data, True, None
        return [], True, "payload is not a json array"
    rows = data.get(container) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], True, f"payload missing {container}[]"
    return rows, True, None


def parse_job_board(url: str, payload: bytes) -> dict[str, Any]:
    """Parse a allowlisted job-board API payload into posting dicts.

    Truncated payloads (capped at the response-size limit mid-array) still yield
    every complete element; ``listing_complete`` records whether the array
    terminator was inside the payload so callers can refetch or bound claims.
    """

    host = (urlsplit(url).hostname or "").lower()
    problems: list[str] = []
    postings: list[dict[str, Any]] = []
    board_total: int | None = None
    if host not in ("boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com", "api.smartrecruiters.com", "apply.workable.com"):
        return {"postings": [], "problems": [f"no job-board parser for host {host!r}"], "listing_complete": False, "board_total": None}

    if host == "boards-api.greenhouse.io":
        rows, listing_complete, problem = _list_rows(payload, "jobs")
    elif host == "api.lever.co":
        rows, listing_complete, problem = _list_rows(payload, None)
    elif host == "api.ashbyhq.com":
        rows, listing_complete, problem = _list_rows(payload, "jobs")
    elif host == "api.smartrecruiters.com":
        rows, listing_complete, problem = _list_rows(payload, "content")
        try:
            header = _json_load(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            header = None
        if isinstance(header, dict) and isinstance(header.get("totalFound"), int):
            board_total = int(header["totalFound"])
    else:
        rows, listing_complete, problem = _list_rows(payload, "jobs")
    rows = [row for row in rows if isinstance(row, dict)]
    if problem:
        problems.append(problem)

    if host == "boards-api.greenhouse.io":
        for job in rows:
            location = _first(job, "location") or {}
            location_name = location.get("name") if isinstance(location, dict) else str(location)
            postings.append(
                {
                    "job_id": str(_first(job, "id", "internal_job_id") or ""),
                    "title": normalize_ws(str(_first(job, "title", "name") or "")),
                    "location": normalize_ws(str(location_name or "")),
                    "url": str(_first(job, "absolute_url", "url") or ""),
                    "posted_at": str(_first(job, "updated_at", "created_at", "first_published") or ""),
                    "text": escaped_html_to_text(str(_first(job, "content", "description") or "")),
                    "team": normalize_ws(str((_first(job, "departments") or [{}])[0].get("name") if isinstance(_first(job, "departments"), list) and job.get("departments") else "")),
                }
            )
    elif host == "api.lever.co":
        for job in rows:
            categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
            postings.append(
                {
                    "job_id": str(_first(job, "id") or ""),
                    "title": normalize_ws(str(_first(job, "text", "title") or "")),
                    "location": normalize_ws(str(_first(categories, "location", "allLocations") or "")),
                    "url": str(_first(job, "hostedUrl", "applyUrl") or ""),
                    "posted_at": str(_first(job, "createdAt", "updatedAt") or ""),
                    "text": normalize_ws(str(_first(job, "descriptionPlain", "description") or ""))[:12000]
                    or html_to_text(str(job.get("description") or "")),
                    "team": normalize_ws(str(_first(categories, "team", "department") or "")),
                }
            )
    elif host == "api.ashbyhq.com":
        for job in rows:
            if job.get("isListed") is False:
                continue
            postings.append(
                {
                    "job_id": str(_first(job, "id") or ""),
                    "title": normalize_ws(str(_first(job, "title") or "")),
                    "location": normalize_ws(str(_first(job, "location", "locationName") or "")),
                    "url": str(_first(job, "jobUrl", "applyUrl", "url") or ""),
                    "posted_at": str(_first(job, "publishedAt", "updatedAt") or ""),
                    "text": html_to_text(str(_first(job, "descriptionHtml", "descriptionPlain", "description") or "")),
                    "team": normalize_ws(str(_first(job, "department", "team") or "")),
                }
            )
    elif host == "api.smartrecruiters.com":
        for job in rows:
            location = job.get("location") if isinstance(job.get("location"), dict) else {}
            location_text = ", ".join(
                str(part) for part in (location.get("city"), location.get("region"), location.get("country")) if part
            )
            postings.append(
                {
                    "job_id": str(_first(job, "id", "uuid") or ""),
                    "title": normalize_ws(str(_first(job, "name", "title") or "")),
                    "location": normalize_ws(location_text),
                    "url": str(_first(job, "ref", "url") or ""),
                    "posted_at": str(_first(job, "releasedDate", "createdOn") or ""),
                    "text": "",
                    "team": normalize_ws(str((job.get("department") or {}).get("label") if isinstance(job.get("department"), dict) else "")),
                }
            )
    elif host == "apply.workable.com":
        for job in rows:
            location = job.get("location") if isinstance(job.get("location"), dict) else {}
            postings.append(
                {
                    "job_id": str(_first(job, "shortcode", "id") or ""),
                    "title": normalize_ws(str(_first(job, "title") or "")),
                    "location": normalize_ws(str(_first(location, "location_str", "city") or "")),
                    "url": str(_first(job, "url", "application_url") or ""),
                    "posted_at": str(_first(job, "published_on", "created_at") or ""),
                    "text": html_to_text(str(_first(job, "description", "requirements") or "")),
                    "team": normalize_ws(str(_first(job, "department", "function") or "")),
                }
            )
    else:
        problems.append(f"no job-board parser for host {host!r}")

    cleaned: list[dict[str, Any]] = []
    for posting in postings:
        if not posting["title"]:
            continue
        posting["text"] = normalize_ws(posting["text"])
        cleaned.append(posting)
    if not cleaned and not problems:
        problems.append("payload contained no titled postings")
    return {
        "postings": cleaned,
        "problems": problems,
        "listing_complete": listing_complete,
        "board_total": board_total,
    }


def parse_job_detail(url: str, payload: bytes) -> dict[str, Any]:
    """Parse a single-posting detail payload (greenhouse, smartrecruiters)."""

    host = (urlsplit(url).hostname or "").lower()
    try:
        data = _json_load(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"text": "", "problems": [f"json parse failed: {exc}"]}
    if not isinstance(data, dict):
        return {"text": "", "problems": ["detail payload is not a json object"]}
    if host == "boards-api.greenhouse.io":
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        text = escaped_html_to_text(str(_first(data, "content", "description") or ""))
        return {
            "text": normalize_ws(text),
            "title": normalize_ws(str(_first(data, "title", "name") or "")),
            "location": normalize_ws(str(location.get("name") or "")),
            "posted_at": str(_first(data, "updated_at", "first_published", "created_at") or ""),
            "problems": [] if text else ["detail payload contained no job text"],
        }
    if host == "api.smartrecruiters.com":
        job_ad = data.get("jobAd") if isinstance(data.get("jobAd"), dict) else {}
        sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else {}

        def section_text(key: str) -> str:
            value = sections.get(key)
            if isinstance(value, dict):
                value = value.get("text") or value.get("description") or ""
            return html_to_text(str(value or ""))

        parts = [section_text(key) for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")]
        text = normalize_ws(" ".join(part for part in parts if part))
        return {
            "text": text,
            "title": normalize_ws(str(_first(data, "name", "title") or "")),
            "location": "",
            "posted_at": str(_first(data, "releasedDate", "createdOn") or ""),
            "problems": [] if text else ["detail payload contained no job text"],
        }
    return {"text": "", "problems": [f"no job-detail parser for host {host!r}"]}


def parse_foundation(url: str, payload: bytes, content_type: str = "") -> dict[str, Any]:
    """Parse an allowlisted occupational foundation page into normalized text."""

    host = (urlsplit(url).hostname or "").lower()
    if "html" in content_type or host in ("www.onetonline.org", "www.bls.gov"):
        text = html_to_text(payload.decode("utf-8", errors="replace"))
    else:
        text = normalize_ws(payload.decode("utf-8", errors="replace"))
    publisher = {
        "www.onetonline.org": "O*NET OnLine (U.S. Department of Labor)",
        "www.onetcenter.org": "O*NET Center",
        "www.bls.gov": "U.S. Bureau of Labor Statistics",
    }.get(host, host)
    return {"text": text, "publisher": publisher, "problems": [] if text else ["empty text after parsing"]}


def us_location_ok(location: str) -> bool:
    """True when a posting location carries at least one explicit US location.

    Multi-location strings are judged segment by segment, so a posting open in
    both a US and a non-US location still counts as US-scope. ``Remote`` without
    a country/city signal is not US evidence, and an empty location never passes.
    """

    text = normalize_ws(location)
    lowered = text.lower()
    if not lowered:
        return False
    for raw_segment in re.split(r"[;|]", text):
        segment = raw_segment.strip()
        if not segment:
            continue
        segment_lower = segment.lower()
        if US_LOCATION_RE.search(segment_lower):
            return True
        if any(marker in segment_lower for marker in NON_US_MARKERS):
            continue
        if any(city in segment_lower for city in US_CITY_MARKERS):
            return True
        components = [part.strip() for part in re.split(r"[,\-–()/]", segment) if part.strip()]
        if components and re.fullmatch(r"[a-z]{2}", components[-1]) and components[-1] in FOREIGN_COUNTRY_CODES:
            continue
        tokens = re.findall(r"[a-z]+", segment_lower)
        if any(token in US_STATE_CODES for token in tokens):
            return True
    return False


def seniority_exclusion_reason(title: str, occupation: str | None = None) -> str | None:
    pattern = SENIORITY_BANDS.get(str(occupation or ""), SENIORITY_EXCLUDE_TITLE_RE)
    match = pattern.search(title or "")
    if match:
        return f"title band excluded ({match.group(1).lower()})"
    return None


def dedup_key(employer: str, job_id: str, url: str) -> str:
    from .release import sha256_text

    basis = "|".join(
        [
            normalize_ws(employer).lower(),
            normalize_ws(job_id).lower(),
            re.sub(r"[?#].*$", "", str(url)).rstrip("/").lower(),
        ]
    )
    return sha256_text(basis)[:24]


def build_published_extract(
    *,
    source_id: str,
    url: str,
    retrieved_at: str,
    publisher: str,
    rights: str,
    raw_sha256: str,
    raw_bytes: int,
    source_type: str,
    excerpts: Iterable[dict[str, Any]],
    foundation_text: str | None = None,
    retrieval_kind: str = "",
    parent_source_id: str = "",
) -> str:
    """Rights-safe published extract: header + verified short excerpt spans.

    ``excerpts`` rows are ``{posting_id, employer, title, location, url, variant,
    span}``; each span is already verified byte-verbatim against the raw text.
    Job-posting detail sources carry ``retrieval_kind=detail`` and the parent
    board listing id so the published lineage distinguishes the response that
    actually contains the span from the listing used for population counts.
    """

    lines = [
        f"# source {source_id}",
        f"# source_type {source_type}",
    ]
    if retrieval_kind:
        lines.append(f"# retrieval_kind {retrieval_kind}")
    if parent_source_id:
        lines.append(f"# parent_source_id {parent_source_id}")
    lines.extend(
        [
            f"# url {url}",
            f"# publisher {publisher}",
            f"# retrieved_at {retrieved_at}",
            f"# rights {rights}",
            f"# raw_sha256 {raw_sha256} raw_bytes {raw_bytes}",
            "# extract_policy verified short excerpt spans only; full raw text retained in the external evidence root",
        ]
    )
    if foundation_text:
        cap = 6000
        excerpt = foundation_text[:cap]
        lines.append(f"# foundation_excerpt_chars {len(excerpt)} of {len(foundation_text)}")
        lines.append(excerpt)
    current_posting: str | None = None
    for row in excerpts:
        posting_id = str(row.get("posting_id") or "")
        if posting_id != current_posting:
            current_posting = posting_id
            lines.append(
                "# posting "
                + " ".join(
                    [
                        f"id={posting_id}",
                        f"employer={normalize_ws(str(row.get('employer') or ''))}",
                        f"title={normalize_ws(str(row.get('title') or ''))}",
                        f"location={normalize_ws(str(row.get('location') or ''))}",
                        f"variant={row.get('variant') or ''}",
                        f"url={row.get('url') or ''}",
                    ]
                )
            )
        span = normalize_ws(str(row.get("span") or ""))
        if span:
            lines.append(span)
    text = "\n".join(lines) + "\n"
    return text


def clamp_quote(text: str) -> str:
    limit = int(RESEARCH_LIMITS["max_quote_chars"])
    value = normalize_ws(text)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
