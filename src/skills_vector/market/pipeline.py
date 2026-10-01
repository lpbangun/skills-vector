"""Bounded live research pipeline: discovery -> retrieval -> candidate selection
-> admission -> reconciliation -> challenge -> validated release.

The pipeline makes real decisions through the pinned subscription task model
(via :mod:`skills_vector.market.agent`), retrieves only allowlisted public
sources with hard caps, verifies every quote byte-for-byte against retrieved
text, and publishes nothing unless the assembled release passes structural
validation.

Evidence integrity rules enforced here:

* every successfully fetched posting detail is its own source record (original
  detail URL, response hash, retrieved_at, byte count, rights) linked to its
  parent board listing, so a quote is attributed to the response that actually
  contains it — never to a compact listing that carries only metadata;
* board listing records stay the population denominators: detail sources never
  inflate boards attempted/used, retrieved/sampled postings or employer counts;
* scope admission is explicit and fail-closed: the agent classifies each
  posting's work level (individual_contributor|people_manager|unknown) with a
  grounded rationale and a byte-verbatim people-management quote when direct
  reports are claimed; only literal individual_contributor decisions with a
  rationale and no people-management evidence enter the sample, and every
  scope rejection is recorded with its category;
* learning priorities carry agent-selected recorded claim ids from a dedicated
  bounded linking pass (identity/role/basis/variant validated deterministically,
  unknown or cross-variant ids dropped fail-closed), not lexical overlap;
* the candidate cap is allocated fairly across retrieved employers and
  provisional growth-variant/title buckets, with per-bucket cap exclusions
  reported honestly, and a single bounded discovery-feedback pass reacts to low
  real yield and to missing required growth buckets.

Failures keep the last good release; nothing is fabricated.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .agent import CallBudget, OmpAgentRunner, ResearchError
from .fetch import FetchResult, Transport, fetch_url
from .limits import MODEL_PIN, RESEARCH_LIMITS
from .publish import PublishError, merge_slice, publish_release
from .release import canonical_json, safe_https_url, sha256_file, sha256_text
from .sources import (
    build_published_extract,
    clamp_quote,
    dedup_key,
    normalize_ws,
    parse_foundation,
    parse_job_board,
    parse_job_detail,
    seniority_exclusion_reason,
    us_location_ok,
)
from .hosts import url_problem

MAX_TEXT_PER_POSTING_PROMPT = 1800
ADMISSION_BATCH = 10
LINK_LIMIT = 6                    # bounded recorded-claim links retained per learning priority

# Scope admission: only a literal individual-contributor decision with a
# nonempty rationale and no people-management evidence is admitted.
WORK_LEVELS = ("individual_contributor", "people_manager", "unknown")
WORK_LEVEL_ADMIT = "individual_contributor"
WORK_LEVEL_REASON_MAX = 240

# Bounded discovery-feedback and candidate-text policy (resource ceilings stay fixed).
FEEDBACK_BOARD_LIMIT = 8          # replacement boards requested in one feedback pass
FEEDBACK_MIN_RETRIEVALS = 4       # do not open a feedback pass without room for boards + details
DETAIL_FETCH_RESERVE = 3          # retrievals kept in reserve while boards are still being listed

CANDIDATE_ALLOCATION_METHOD = (
    "deterministic fair allocation: one candidate per employer per provisional bucket "
    "(growth variants first) in employer importance order, then a round-robin remainder across "
    "employers; within an employer bucket, most recently published first, then title and posting id"
)

POSTING_DETAIL_RIGHTS = (
    "Public employer job posting via public job-board API; short excerpts published with employer attribution"
)

ATS_URL_TEMPLATES: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
}

# Compact listing variants used when the content-bearing listing exceeds the response cap.
ATS_COMPACT_URL_TEMPLATES: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
}

# Single-posting endpoints used to fetch description text for candidates whose listing has none.
ATS_DETAIL_URL_TEMPLATES: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings/{job_id}",
}

SAMPLE_ORDER = "most recently published first, then title and posting id (board API page order is not preserved)"

VARIANT_HINTS: dict[str, tuple[str, ...]] = {
    "product-growth": (
        "product-led", "plg", "activation", "onboarding", "retention", "engagement", "self-serve",
        "conversion", "experimentation", "funnel", "product analytics",
    ),
    "growth-marketing": (
        "demand gen", "demand generation", "lifecycle", "email", "paid", "seo", "content", "brand",
        "campaign", "acquisition", "performance marketing", "marketing operations", "social",
    ),
    "sales-account-executive": (
        "account executive", "sales", "quota", "pipeline", "prospect", "closing", "revenue", "ae",
        "business development", "outbound", "enterprise", "mid-market",
    ),
}

OCCUPATION_CONFIG: dict[str, dict[str, Any]] = {
    "hr-generalist": {
        "label": "HR Generalist",
        "family": "People Operations & Talent",
        "aliases": ["hr generalist", "people operations", "human resources generalist", "people ops"],
        "onet_hint": "13-1071.00",
        "title_hints": (
            "hr",
            "hris",
            "hrbp",
            "human resources",
            "people ops",
            "people operations",
            "people partner",
            "people experience",
            "hr business partner",
            "people business partner",
        ),
        "role_focus": "generalist HR operations: employee lifecycle, policies, onboarding/offboarding, benefits administration, HRIS data, employee relations support, compliance basics",
        "seniority": "mid-level individual contributor (excludes intern/junior/entry and principal/director-and-above titles)",
        "geography": "United States (onsite, hybrid or US-remote roles located in the US)",
    },
    "growth-manager": {
        "label": "Growth Manager",
        "family": "Go-to-market & growth",
        "aliases": ["growth manager", "growth marketing", "product growth", "growth lead"],
        "onet_hint": "13-1161.00",
        "title_hints": (
            "growth", "demand generation", "demand gen", "lifecycle marketing", "product-led",
            "acquisition marketing", "performance marketing", "account executive", "sales",
        ),
        "role_focus": (
            "growth roles, kept as three distinct variants: product-growth (activation/retention/PLG/"
            "experimentation), growth-marketing (acquisition campaigns/lifecycle/paid/SEO/content), and "
            "sales-account-executive (quota-carrying closing roles). Variants are never conflated."
        ),
        "seniority": "mid-level individual contributor (excludes intern/junior/entry and principal/director-and-above titles)",
        "geography": "United States (onsite, hybrid or US-remote roles located in the US)",
    },
    "account-executive": {
        "label": "Account Executive",
        "family": "Sales",
        "aliases": ["account executive", "sales executive", "ae", "closing sales"],
        "onet_hint": "41-4012.00",
        "title_hints": ("account executive", "sales executive", "account manager", "sales representative", "sales"),
        "role_focus": "quota-carrying closing roles: pipeline management, discovery, demos, negotiation, closing, account growth",
        "seniority": "mid-level individual contributor (excludes intern/junior/entry and principal/director-and-above titles)",
        "geography": "United States (onsite, hybrid or US-remote roles located in the US)",
    },
}

HONESTY_BANNED_RE = re.compile(
    r"(?:\bprevalence\b|\bof the market\b|\bmost (?:employers|postings|companies)\b|\btrend\b|"
    r"\btypically\b|\balways\b|\bnever\b|\beveryone\b|\b\d+(?:\.\d+)?\s*%)",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{sha256_text('|'.join(parts))[:20]}"


# Admission prompt compaction: a long posting description is windowed around the
# first responsibilities-style heading when one exists, so the duties that decide
# the work level stay visible instead of the company-description prefix.
RESPONSIBILITY_SECTION_RE = re.compile(
    r"\b(?:responsibilities|key responsibilities|what you(?:'|\u2019)ll do|what you will do|"
    r"what you(?:'|\u2019)ll be doing|the role|your role|about the role|duties|day[- ]to[- ]day)\b",
    re.IGNORECASE,
)
RESPONSIBILITY_LEAD_CHARS = 160


def admission_prompt_text(text: str, limit: int = MAX_TEXT_PER_POSTING_PROMPT) -> str:
    """Bounded posting text for the admission prompt with responsibility context.

    Within the existing per-posting prompt bound, prefer a window anchored at the
    first responsibilities/duties heading (with a short lead-in) over blindly
    taking the description prefix, which is often company boilerplate. Falls back
    to the plain prefix when no responsibility heading exists.
    """

    value = str(text or "")
    if len(value) <= limit:
        return value
    match = RESPONSIBILITY_SECTION_RE.search(value)
    if not match:
        return value[:limit]
    start = max(0, min(match.start() - RESPONSIBILITY_LEAD_CHARS, len(value) - limit))
    return value[start : start + limit]


@dataclass
class ResearchConfig:
    occupation: str
    evidence_root: Path
    release_root: Path
    overlay: Path
    question: str | None = None
    operator_boards: list[tuple[str, str, str]] = field(default_factory=list)
    min_postings: int = 8
    max_postings: int = int(RESEARCH_LIMITS["max_postings_per_role"])
    max_boards: int = int(RESEARCH_LIMITS["max_boards_per_role"])
    max_foundations: int = int(RESEARCH_LIMITS["max_foundations_per_role"])
    publish: bool = True
    transport: Transport | None = None
    runner: Callable[..., tuple[int, str, str]] | None = None
    run_id: str | None = None
    title_hints: tuple[str, ...] | None = None

    def clamped(self) -> "ResearchConfig":
        self.max_postings = max(1, min(int(self.max_postings), int(RESEARCH_LIMITS["max_postings_per_role"])))
        self.max_boards = max(1, min(int(self.max_boards), int(RESEARCH_LIMITS["max_boards_per_role"])))
        self.max_foundations = max(1, min(int(self.max_foundations), int(RESEARCH_LIMITS["max_foundations_per_role"])))
        self.min_postings = max(1, min(int(self.min_postings), self.max_postings))
        return self


class ResearchRun:
    def __init__(self, config: ResearchConfig) -> None:
        if config.occupation not in OCCUPATION_CONFIG:
            raise ResearchError(f"unknown occupation {config.occupation!r}; known: {sorted(OCCUPATION_CONFIG)}")
        self.config = config.clamped()
        self.spec = OCCUPATION_CONFIG[self.config.occupation]
        self.run_id = self.config.run_id or self._make_run_id()
        self.run_dir = Path(self.config.evidence_root) / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "raw").mkdir(exist_ok=True)
        (self.run_dir / "slice" / "extracts").mkdir(parents=True, exist_ok=True)
        self.ledger_path = Path(self.config.evidence_root) / "ledger.jsonl"
        self.started_at = _now_iso()
        self.budget = CallBudget()
        self.runner = OmpAgentRunner(
            run_dir=self.run_dir,
            overlay=Path(self.config.overlay),
            budget=self.budget,
            runner=self.config.runner,
        )
        self.retrievals = 0
        self.sources: list[dict[str, Any]] = []
        self.postings: list[dict[str, Any]] = []
        self.exclusions: list[dict[str, Any]] = []
        self.mappings: list[dict[str, Any]] = []
        self.disagreements: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []
        self.requirements: list[dict[str, Any]] = []
        self.extracts: dict[str, str] = {}
        self.discovery: dict[str, Any] = {}
        self.discovery_feedback: dict[str, Any] | None = None
        self.candidates: list[dict[str, Any]] = []
        self.selection_summary: dict[str, Any] = {}
        self.work_level_rejections: dict[str, int] = {}
        self._raw_text: dict[str, str] = {}
        self._posting_text: dict[str, dict[str, Any]] = {}
        self._attempted_boards: set[tuple[str, str]] = set()
        self._prefiltered_keys: set[str] = set()
        self._detail_parent: dict[str, str] = {}
        self._board_meta: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _make_run_id() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"run_{stamp}_{secrets.token_hex(3)}"

    # -- ledger ----------------------------------------------------------

    def _ledger(self, **row: Any) -> None:
        row = {"ts": _now_iso(), "run_id": self.run_id, "occupation": self.config.occupation, **row}
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(row) + "\n")

    # -- retrieval -------------------------------------------------------

    def _retrieve(self, url: str, source_id: str) -> FetchResult:
        reason = url_problem(url)
        if reason:
            raise ResearchError(f"retrieval refused: {reason}")
        if self.retrievals >= int(RESEARCH_LIMITS["max_retrievals"]):
            raise ResearchError("retrieval budget exhausted for this run")
        self.retrievals += 1
        result = fetch_url(url, transport=self.config.transport)
        raw_path = self.run_dir / "raw" / f"{source_id}.bin"
        meta_path = self.run_dir / "raw" / f"{source_id}.meta.json"
        if result.body:
            raw_path.write_bytes(result.body)
        meta_path.write_text(json.dumps(result.as_metadata(), indent=2) + "\n", encoding="utf-8")
        self._ledger(kind="retrieval", source_id=source_id, **result.as_metadata())
        return result

    # -- stages ----------------------------------------------------------

    def _discovery_prompt(self, hints: list[str]) -> str:
        spec = self.spec
        known = "\n".join(f"- {hint}" for hint in hints) if hints else "- (none carried forward)"
        return f"""You are the discovery pass of a bounded occupational research pipeline for the role "{spec['label']}".
Scope: {spec['geography']}; {spec['seniority']}.
Role focus: {spec['role_focus']}

Your decisions must be grounded in public sources that this pipeline may retrieve. Allowed source hosts:
- Official foundations: www.onetonline.org (e.g. https://www.onetonline.org/link/summary/{spec['onet_hint']}), www.bls.gov/ooh/…
- Employer job-board APIs (tokens only; the pipeline builds the URL): ats in {{greenhouse, lever, ashby, smartrecruiters, workable}} with an employer slug token.

Carried-forward hints from the last validated release or the operator (verify or replace):
{known}

Return ONE JSON object, no markdown fence, exactly this shape:
{{
  "foundations": [{{"url": "https://www.onetonline.org/link/summary/…", "why": "…"}}],
  "boards": [{{"ats": "greenhouse|lever|ashby|smartrecruiters|workable", "token": "company-slug", "employer": "Company Name", "why": "…"}}],
  "search_terms": ["…", "…"]
}}

Rules: at most {self.config.max_foundations} foundations and {self.config.max_boards} boards; tokens must be plausible slugs you have seen in public careers URLs; do not invent tokens for companies that do not use that ATS — a wrong token returns 404 or an empty board and contributes nothing, and a later feedback pass sees the measured per-board outcomes; search_terms must be literal phrases used in job titles for this role (they only guide retrieval; they are never evidence)."""

    def stage_discovery(self) -> dict[str, Any]:
        hints = [f"{ats}:{token} ({employer})" for ats, token, employer in self.config.operator_boards]
        current = self._current_release()
        if current is not None:
            for source in current.sources:
                if str(source.get("occupation_slug") or "") != self.config.occupation:
                    continue
                if str(source.get("source_type") or "") == "job-board" and source.get("inclusion") is not False:
                    hints.append(f"last-release board: {source.get('url')}")
        payload, result = self.runner.require_json("discovery", self._discovery_prompt(hints))

        foundations: list[dict[str, Any]] = []
        for row in payload.get("foundations") or []:
            url = str(row.get("url") or "").strip()
            if url_problem(url):
                continue
            foundations.append({"url": url, "why": normalize_ws(str(row.get("why") or ""))})
        boards: list[dict[str, Any]] = []
        for ats, token, employer in self.config.operator_boards:
            if ats in ATS_URL_TEMPLATES:
                boards.append({"ats": ats, "token": token, "employer": employer, "why": "operator-provided candidate"})
        for row in payload.get("boards") or []:
            ats = str(row.get("ats") or "").strip().lower()
            token = str(row.get("token") or "").strip()
            employer = normalize_ws(str(row.get("employer") or ""))
            if ats not in ATS_URL_TEMPLATES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", token):
                continue
            if not employer:
                continue
            if any(existing["ats"] == ats and existing["token"] == token for existing in boards):
                continue
            boards.append({"ats": ats, "token": token, "employer": employer, "why": normalize_ws(str(row.get("why") or ""))})
        search_terms = [normalize_ws(str(term)) for term in (payload.get("search_terms") or []) if str(term).strip()]

        self.discovery = {
            "model_call": result.as_metadata(),
            "foundations": foundations[: self.config.max_foundations],
            "boards": boards[: self.config.max_boards],
            "search_terms": search_terms[:12],
            "carried_forward_hints": hints,
        }
        self._ledger(kind="discovery", **{k: v for k, v in self.discovery.items() if k != "model_call"})
        return self.discovery

    def _current_release(self):
        try:
            from .core import CatalogStore

            return CatalogStore(self.config.release_root).release()
        except Exception:  # noqa: BLE001 - a missing/broken current release must not abort discovery
            return None

    def stage_retrieval(self) -> None:
        discovery = self.discovery
        if not discovery.get("foundations") and not discovery.get("boards"):
            raise ResearchError("discovery returned no retrievable targets")

        for target in discovery["foundations"]:
            url = target["url"]
            source_id = _id("src", self.run_id, url)
            fetch = self._retrieve(url, source_id)
            rights = (
                "O*NET OnLine, U.S. Department of Labor (CC BY 4.0); short excerpts published with attribution"
                if "onetonline.org" in url or "onetcenter.org" in url
                else "U.S. Bureau of Labor Statistics public domain; short excerpts published with attribution"
            )
            if fetch.ok:
                parsed = parse_foundation(url, fetch.body, fetch.content_type)
                text = parsed["text"]
                self._raw_text[source_id] = text
                self.sources.append(
                    {
                        "id": source_id,
                        "url": url,
                        "publisher": parsed["publisher"],
                        "source_type": "foundation",
                        "occupation_slug": self.config.occupation,
                        "retrieved_at": fetch.retrieved_at,
                        "sha256": fetch.sha256,
                        "bytes": fetch.bytes_read,
                        "rights": rights,
                        "inclusion": True,
                        "role_scope": self._role_scope(),
                    }
                )
            else:
                self.sources.append(self._failed_source(source_id, url, fetch, "foundation"))
        found_foundation = any(source["source_type"] == "foundation" and source["inclusion"] for source in self.sources)
        if not found_foundation:
            self._ledger(kind="warning", detail="no foundation source retrieved successfully")

        for target in discovery["boards"]:
            self._retrieve_board(target)
        if not self._posting_text:
            self._ledger(kind="warning", detail="no postings retrieved; demand evidence absent")

    # -- board retrieval -------------------------------------------------

    def _remaining_retrievals(self) -> int:
        return int(RESEARCH_LIMITS["max_retrievals"]) - self.retrievals

    @staticmethod
    def _attempt_row(url: str, fetch: FetchResult, postings: int, note: str = "") -> dict[str, Any]:
        return {
            "url": url,
            "status": fetch.status,
            "bytes": fetch.bytes_read,
            "sha256": fetch.sha256,
            "truncated": bool(fetch.truncated),
            "parsed_postings": postings,
            "note": note,
        }

    @staticmethod
    def _posted_sort_value(posted_at: str) -> float:
        text = str(posted_at or "").strip()
        if not text:
            return 0.0
        if text.isdigit():
            return float(text) / 1000.0
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    def _retrieve_board(self, target: dict[str, Any]) -> bool:
        """Retrieve one board listing, salvage truncated payloads, record provenance.

        Returns True when the board contributed enumerated postings.
        """

        ats = str(target.get("ats") or "").strip().lower()
        token = str(target.get("token") or "").strip()
        employer = str(target.get("employer") or "").strip()
        if ats not in ATS_URL_TEMPLATES or (ats, token) in self._attempted_boards:
            return False
        if len(self._attempted_boards) >= self.config.max_boards:
            self._ledger(kind="warning", detail="board retrieval skipped: total board-attempt ceiling reached", ats=ats, token=token)
            return False
        self._attempted_boards.add((ats, token))

        rights = POSTING_DETAIL_RIGHTS
        url = ATS_URL_TEMPLATES[ats].format(token=token)
        source_id = _id("src", self.run_id, url)
        fetch = self._retrieve(url, source_id)
        if not fetch.ok and not (fetch.truncated and fetch.status == 200):
            self.sources.append(self._failed_source(source_id, url, fetch, "job-board", employer=employer))
            return False
        parsed = parse_job_board(url, fetch.body)
        attempts = [self._attempt_row(url, fetch, len(parsed["postings"]), "json prefix salvaged from truncated payload" if fetch.truncated else "")]
        listing_truncated = bool(fetch.truncated or not parsed["listing_complete"])

        # Content-bearing listings can exceed the response cap on large boards. Where the ATS
        # exposes a compact metadata listing, refetch it so enumeration covers the whole board.
        if listing_truncated and ats in ATS_COMPACT_URL_TEMPLATES and self._remaining_retrievals() > DETAIL_FETCH_RESERVE:
            compact_url = ATS_COMPACT_URL_TEMPLATES[ats].format(token=token)
            compact_id = _id("src", self.run_id, compact_url)
            compact = self._retrieve(compact_url, compact_id)
            if compact.ok or (compact.truncated and compact.status == 200):
                compact_parsed = parse_job_board(compact_url, compact.body)
                attempts.append(
                    self._attempt_row(compact_url, compact, len(compact_parsed["postings"]), "compact listing fallback")
                )
                if compact_parsed["postings"]:
                    url, source_id, fetch, parsed = compact_url, compact_id, compact, compact_parsed
                    listing_truncated = bool(compact.truncated or not compact_parsed["listing_complete"])
            else:
                attempts.append(self._attempt_row(compact_url, compact, 0, "compact listing unavailable"))

        if not parsed["postings"]:
            reason = "; ".join(parsed["problems"]) or (
                "response truncated at the response-size cap and no complete posting object was inside the prefix"
                if fetch.truncated
                else "no postings parsed from board payload"
            )
            self.sources.append(self._failed_source(source_id, url, fetch, "job-board", employer=employer, reason=reason))
            self._ledger(kind="board", source_id=source_id, employer=employer, postings=0, outcome="no-postings", reason=reason)
            return False

        seen: set[str] = set()
        enumerated: list[dict[str, Any]] = []
        for posting in parsed["postings"]:
            key = dedup_key(employer, posting["job_id"], posting["url"])
            if key in seen:
                continue
            seen.add(key)
            posting["employer"] = employer
            posting["source_id"] = source_id
            posting["board_source_id"] = source_id
            posting["dedup_key"] = key
            enumerated.append(posting)
        enumerated.sort(key=lambda posting: (-self._posted_sort_value(posting["posted_at"]), posting["title"], posting["job_id"]))
        self._raw_text[source_id] = "\n".join(
            f"[{posting['job_id']}] {posting['title']} :: {posting['text']}" for posting in enumerated
        )
        for posting in enumerated:
            self._posting_text[posting["dedup_key"]] = {
                "text": posting["text"],
                "employer": employer,
                "title": posting["title"],
                "location": posting["location"],
                "url": posting["url"],
                "job_id": posting["job_id"],
                "source_id": source_id,
                "board_source_id": source_id,
                "posted_at": posting["posted_at"],
                "ats": ats,
                "token": token,
            }
        self._board_meta[source_id] = {"ats": ats, "token": token, "employer": employer}
        self.sources.append(
            {
                "id": source_id,
                "url": url,
                "publisher": employer,
                "source_type": "job-board",
                "retrieval_kind": "listing",
                "occupation_slug": self.config.occupation,
                "retrieved_at": fetch.retrieved_at,
                "sha256": fetch.sha256,
                "bytes": fetch.bytes_read,
                "rights": rights,
                "inclusion": True,
                "ats": ats,
                "token": token,
                "postings_enumerated": len(seen),
                "postings_retrieved": len(enumerated),
                "board_total": parsed.get("board_total"),
                "listing_truncated": listing_truncated,
                "listing_attempts": attempts,
                "sample_order": SAMPLE_ORDER,
                "role_scope": self._role_scope(),
            }
        )
        self._ledger(
            kind="board",
            source_id=source_id,
            employer=employer,
            postings=len(enumerated),
            outcome="enumerated",
            listing_truncated=listing_truncated,
        )
        return True

    def _failed_source(
        self,
        source_id: str,
        url: str,
        fetch: FetchResult,
        source_type: str,
        *,
        employer: str = "",
        reason: str | None = None,
    ) -> dict[str, Any]:
        exclusion_reason = reason or fetch.error or (
            f"response truncated at the {int(RESEARCH_LIMITS['max_response_bytes'])}-byte response cap"
            if fetch.truncated
            else f"http status {fetch.status}"
        )
        self._ledger(kind="retrieval_failed", source_id=source_id, url=url, reason=exclusion_reason)
        return {
            "id": source_id,
            "url": url,
            "publisher": employer or url.split("/")[2],
            "source_type": source_type,
            "occupation_slug": self.config.occupation,
            "retrieved_at": fetch.retrieved_at,
            "sha256": fetch.sha256 or sha256_text(""),
            "bytes": fetch.bytes_read,
            "rights": "not published (retrieval failed)",
            "inclusion": False,
            "exclusion_reason": exclusion_reason,
            "role_scope": self._role_scope(),
        }

    def _role_scope(self) -> dict[str, str]:
        return {"geography": self.spec["geography"], "seniority": self.spec["seniority"]}

    # -- admission -------------------------------------------------------


    def _eligible_candidates(self) -> list[dict[str, Any]]:
        """Enumerated postings that pass scope rules, with a provisional bucket.

        Location/seniority/title rejections are permanent and recorded once; cap
        surplus is decided by :meth:`_allocate_candidates` so a bounded feedback
        pass can still re-allocate before anything is reported as cap-excluded.
        """

        hints = self.config.title_hints or tuple(self.spec["title_hints"])
        candidates: list[dict[str, Any]] = []
        for key, posting in self._posting_text.items():
            if key in self._prefiltered_keys:
                continue
            title = posting["title"]
            location = posting["location"]
            if not us_location_ok(location):
                self._exclude_posting(posting, key, str(posting["source_id"]), "location outside United States scope", stage="prefilter")
                continue
            seniority_reason = seniority_exclusion_reason(title, self.config.occupation)
            if seniority_reason:
                self._exclude_posting(posting, key, str(posting["source_id"]), seniority_reason, stage="prefilter")
                continue
            if hints and not title_matches_hints(title, hints):
                self._exclude_posting(posting, key, str(posting["source_id"]), "title outside role title scope", stage="prefilter")
                continue
            item = {"key": key, **posting}
            item["bucket"] = self._provisional_bucket(item)
            candidates.append(item)
        return candidates

    def _provisional_bucket(self, posting: dict[str, Any]) -> str:
        """Deterministic provisional bucket used only for cap allocation.

        For the growth manager role this is the lexical variant hint over the
        title and any listing text (``unassigned`` when the hint is silent) — a
        retrieval-selection guide, never an admission decision; the agent still
        classifies the actual variant from the posting text.
        """

        if self.config.occupation == "growth-manager":
            hint = self._variant_hint(f"{posting.get('title') or ''} {str(posting.get('text') or '')[:600]}")
            return hint or "unassigned"
        return "role"

    def _allocation_sort_key(self, item: dict[str, Any]) -> tuple[float, str, str]:
        return (
            -self._posted_sort_value(str(item.get("posted_at") or "")),
            str(item.get("title") or ""),
            str(item.get("job_id") or ""),
        )

    def _allocate_candidates(
        self, candidates: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fair, deterministic cap allocation across employers and buckets.

        One candidate per employer per provisional bucket is taken first, in
        employer importance order (most recent posting first, then name) and
        canonical bucket order for the role; the remainder is a round-robin
        across employers. Within an employer bucket, the most recently published
        posting wins the tie (then title and posting id). A large or
        alphabetically early employer can therefore never take the whole cap.
        """

        cap = max(1, int(self.config.max_postings))
        by_employer: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for item in candidates:
            employer = str(item.get("employer") or "")
            bucket = str(item.get("bucket") or "role")
            by_employer.setdefault(employer, {}).setdefault(bucket, []).append(item)
        for buckets in by_employer.values():
            for queue in buckets.values():
                queue.sort(key=self._allocation_sort_key)

        def employer_key(employer: str) -> tuple[float, str]:
            freshest = max(
                (self._posted_sort_value(str(item.get("posted_at") or "")) for queue in by_employer[employer].values() for item in queue),
                default=0.0,
            )
            return (-freshest, employer)

        employer_order = sorted(by_employer, key=employer_key)
        if self.config.occupation == "growth-manager":
            bucket_order = [*VARIANT_HINTS, "unassigned"]
        else:
            bucket_order = ["role"]

        selected: list[dict[str, Any]] = []
        taken: set[str] = set()

        def take(queue: list[dict[str, Any]]) -> None:
            while queue and queue[0]["key"] in taken:
                queue.pop(0)
            if queue and len(selected) < cap:
                item = queue.pop(0)
                taken.add(item["key"])
                selected.append(item)

        # Pass 1: employer/bucket coverage (growth variants first when present).
        for bucket in bucket_order:
            for employer in employer_order:
                if len(selected) >= cap:
                    break
                queue = by_employer[employer].get(bucket)
                if queue:
                    take(queue)
        # Pass 2: fairest-first remainder, one per employer per round.
        while len(selected) < cap:
            progressed = False
            for employer in employer_order:
                if len(selected) >= cap:
                    break
                queues = by_employer[employer]
                best_bucket = ""
                best_key: tuple[float, str, str] | None = None
                for bucket, queue in queues.items():
                    if not queue:
                        continue
                    key = self._allocation_sort_key(queue[0])
                    if best_key is None or key < best_key:
                        best_key = key
                        best_bucket = bucket
                if best_key is not None:
                    take(queues[best_bucket])
                    progressed = True
            if not progressed:
                break

        selected_keys = {item["key"] for item in selected}
        surplus = [item for item in candidates if item["key"] not in selected_keys]
        return selected, surplus

    def _record_cap_exclusions(self, surplus: list[dict[str, Any]]) -> None:
        for item in surplus:
            self._exclude_posting(
                item,
                item["key"],
                str(item.get("source_id") or ""),
                "posting cap for this role reached (fair employer/bucket allocation)",
                stage="prefilter",
            )

    def _missing_required_buckets(self, candidates: list[dict[str, Any]]) -> list[str]:
        if self.config.occupation != "growth-manager":
            return []
        buckets = {str(item.get("bucket") or "") for item in candidates}
        return [variant for variant in VARIANT_HINTS if variant not in buckets]

    def _allocation_summary(
        self,
        candidates: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        surplus: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Transparent per-bucket and per-employer cap outcomes (honest shortage report)."""

        def blank() -> dict[str, int]:
            return {"eligible": 0, "selected": 0, "excluded_by_cap": 0}

        buckets: dict[str, dict[str, int]] = {}
        employers: dict[str, dict[str, int]] = {}
        for item in candidates:
            buckets.setdefault(str(item.get("bucket") or "role"), blank())["eligible"] += 1
            employers.setdefault(str(item.get("employer") or ""), blank())["eligible"] += 1
        for item in selected:
            buckets.setdefault(str(item.get("bucket") or "role"), blank())["selected"] += 1
            employers.setdefault(str(item.get("employer") or ""), blank())["selected"] += 1
        for item in surplus:
            buckets.setdefault(str(item.get("bucket") or "role"), blank())["excluded_by_cap"] += 1
            employers.setdefault(str(item.get("employer") or ""), blank())["excluded_by_cap"] += 1
        return {
            "method": CANDIDATE_ALLOCATION_METHOD,
            "cap": int(self.config.max_postings),
            "eligible_candidates": len(candidates),
            "selected_candidates": len(selected),
            "excluded_by_cap": len(surplus),
            "bucket_outcomes": buckets,
            "employer_outcomes": employers,
        }

    def _exclude_posting(self, posting: dict[str, Any], key: str, source_id: str, reason: str, *, stage: str) -> None:
        if stage == "prefilter":
            if key in self._prefiltered_keys:
                return
            self._prefiltered_keys.add(key)
        self.exclusions.append(
            {
                "kind": "posting",
                "posting_id": posting.get("job_id") or key,
                "dedup_key": key,
                "occupation_slug": self.config.occupation,
                "employer": posting.get("employer", ""),
                "title": posting.get("title", ""),
                "location": posting.get("location", ""),
                "url": posting.get("url", ""),
                "source_id": source_id,
                "stage": stage,
                "reason": reason,
            }
        )

    def _reject_work_level(self, category: str, item: dict[str, Any], key: str, reason: str) -> None:
        """Record one scope-admission rejection and its inspectable category."""

        self.work_level_rejections[category] = self.work_level_rejections.get(category, 0) + 1
        self._exclude_posting(item, key, str(item["source_id"]), reason, stage="admission")

    # -- candidate selection ---------------------------------------------

    def stage_candidate_selection(self) -> list[dict[str, Any]]:
        """Allocate the candidate cap fairly, run one bounded discovery-feedback
        pass on low yield or missing growth buckets, then fetch description text
        for candidates whose listing carried none."""

        eligible = self._eligible_candidates()
        selected, surplus = self._allocate_candidates(eligible)
        feedback_triggered = False
        missing_buckets = self._missing_required_buckets(selected)
        if (
            len(selected) < self.config.min_postings or missing_buckets
        ) and len(self._attempted_boards) < self.config.max_boards and self._remaining_retrievals() >= FEEDBACK_MIN_RETRIEVALS:
            feedback_triggered = True
            boards = self.stage_discovery_feedback(selected, missing_buckets=missing_buckets)
            for target in boards[:FEEDBACK_BOARD_LIMIT]:
                if self._remaining_retrievals() <= DETAIL_FETCH_RESERVE:
                    self._ledger(kind="warning", detail="replacement board skipped: retrieval budget kept for candidate text")
                    break
                self._retrieve_board(target)
            eligible = self._eligible_candidates()
            selected, surplus = self._allocate_candidates(eligible)
        self._record_cap_exclusions(surplus)
        allocation = self._allocation_summary(eligible, selected, surplus)
        detail_summary = self._acquire_candidate_text(selected)
        self.candidates = selected
        self.selection_summary = {
            "prefilter_candidates": len(selected),
            "feedback_triggered": feedback_triggered,
            "missing_required_buckets": self._missing_required_buckets(selected),
            "detail_fetches": detail_summary["fetched"],
            "detail_text_failures": detail_summary["failed"],
            "candidates_excluded_without_text": detail_summary["excluded"],
            "remaining_retrievals": self._remaining_retrievals(),
            "cap_allocation": allocation,
        }
        self._ledger(kind="candidate_selection", **self.selection_summary)
        return selected

    def _board_outcomes(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidate_counts: dict[str, int] = {}
        for item in candidates:
            source_id = str(item.get("board_source_id") or item.get("source_id") or "")
            candidate_counts[source_id] = candidate_counts.get(source_id, 0) + 1
        outcomes: list[dict[str, Any]] = []
        for source in self.sources:
            if source.get("source_type") != "job-board":
                continue
            source_id = str(source.get("id") or "")
            meta = self._board_meta.get(source_id, {})
            outcomes.append(
                {
                    "ats": meta.get("ats") or source.get("ats"),
                    "token": meta.get("token") or source.get("token"),
                    "employer": source.get("publisher"),
                    "inclusion": source.get("inclusion"),
                    "outcome": "enumerated" if source.get("inclusion") else "retrieval failed",
                    "exclusion_reason": source.get("exclusion_reason"),
                    "postings_enumerated": source.get("postings_enumerated"),
                    "in_scope_candidates": candidate_counts.get(source_id, 0),
                }
            )
        return outcomes

    def _provisional_bucket_coverage(self, candidates: list[dict[str, Any]]) -> dict[str, int]:
        coverage: dict[str, int] = {}
        for item in candidates:
            bucket = str(item.get("bucket") or "role")
            coverage[bucket] = coverage.get(bucket, 0) + 1
        return coverage

    def _discovery_feedback_prompt(self, candidates: list[dict[str, Any]], missing_buckets: tuple[str, ...] = ()) -> str:
        spec = self.spec
        outcomes = self._board_outcomes(candidates)
        attempted = [
            {"ats": ats, "token": token}
            for ats, token in sorted(self._attempted_boards)
        ]
        gaps: list[str] = []
        if len(candidates) < self.config.min_postings:
            gaps.append(
                f"the in-scope candidate yield is {len(candidates)} postings, below the required minimum of {self.config.min_postings}"
            )
        if missing_buckets:
            gaps.append(
                "required growth variant buckets without any in-scope candidate: " + ", ".join(missing_buckets)
            )
        return f"""You are the discovery feedback pass of a bounded occupational research pipeline for "{spec['label']}".
Scope: {spec['geography']}; {spec['seniority']}.
Role focus: {spec['role_focus']}

Measured outcomes of the board listings retrieved so far (real retrieval facts, not guesses):
{json.dumps(outcomes, ensure_ascii=False)}
Provisional in-scope candidate coverage by bucket: {json.dumps(self._provisional_bucket_coverage(candidates), ensure_ascii=False)}
Boards already attempted (never repeat these):
{json.dumps(attempted, ensure_ascii=False)}

Measured gaps that triggered this pass: {'; '.join(gaps)}.
Return ONE JSON object, no markdown fence, exactly this shape:
{{"boards": [{{"ats": "greenhouse|lever|ashby|smartrecruiters|workable", "token": "company-slug", "employer": "Company Name", "why": "…"}}]}}

Rules: return at most {FEEDBACK_BOARD_LIMIT} REPLACEMENT boards on the same allowed ATS hosts; no foundations.
Tokens must be real public ATS slugs you have seen in public careers URLs — an invented or stale token returns
404 or an empty board and yields nothing. Prefer employers that actually post US-based mid-level {spec['label']}
roles — and, when a growth variant bucket is missing, employers likely to post that variant — in volume
(state why for each). Do not repeat any attempted board."""

    def stage_discovery_feedback(
        self, candidates: list[dict[str, Any]], *, missing_buckets: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        prompt = self._discovery_feedback_prompt(candidates, missing_buckets)
        payload, result = self.runner.require_json("discovery_feedback", prompt)
        boards: list[dict[str, Any]] = []
        for row in payload.get("boards") or []:
            ats = str(row.get("ats") or "").strip().lower()
            token = str(row.get("token") or "").strip()
            employer = normalize_ws(str(row.get("employer") or ""))
            if ats not in ATS_URL_TEMPLATES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", token):
                continue
            if not employer or (ats, token) in self._attempted_boards:
                continue
            if any(existing["ats"] == ats and existing["token"] == token for existing in boards):
                continue
            boards.append({"ats": ats, "token": token, "employer": employer, "why": normalize_ws(str(row.get("why") or ""))})
        self.discovery_feedback = {
            "model_call": result.as_metadata(),
            "observed_outcomes": self._board_outcomes(candidates),
            "missing_variant_buckets": list(missing_buckets),
            "boards": boards[:FEEDBACK_BOARD_LIMIT],
        }
        self._ledger(
            kind="discovery_feedback",
            candidates=len(candidates),
            missing_variant_buckets=list(missing_buckets),
            replacement_boards=len(self.discovery_feedback["boards"]),
        )
        return self.discovery_feedback["boards"]

    def _acquire_candidate_text(self, candidates: list[dict[str, Any]]) -> dict[str, int]:
        """Fetch description text for candidates whose board listing returned none.

        Each successful detail fetch becomes its own source record (original
        detail URL, response hash, retrieved_at, rights, byte count, parent board
        linkage) and becomes the posting's cited origin: the quote is attributed
        to the response that actually contains it, never to a metadata-only
        compact listing. Board listing records keep the sampling denominators.
        """

        fetched = 0
        failed = 0
        excluded = 0
        for item in candidates:
            if str(item.get("text") or "").strip():
                continue
            ats = str(item.get("ats") or "")
            token = str(item.get("token") or "")
            template = ATS_DETAIL_URL_TEMPLATES.get(ats)
            board_source_id = str(item.get("board_source_id") or item.get("source_id") or "")
            if not template or not token:
                self._exclude_posting(
                    item, item["key"], board_source_id,
                    "posting description unavailable (board listing carries no text and no detail endpoint exists)",
                    stage="admission",
                )
                excluded += 1
                continue
            if self._remaining_retrievals() <= 0:
                self._exclude_posting(
                    item, item["key"], board_source_id,
                    "posting description not fetched within the retrieval budget",
                    stage="admission",
                )
                excluded += 1
                continue
            url = template.format(token=token, job_id=item["job_id"])
            if url_problem(url):
                self._exclude_posting(item, item["key"], board_source_id, "posting detail url refused by allowlist", stage="admission")
                failed += 1
                continue
            detail_id = _id("src", self.run_id, url)
            fetch = self._retrieve(url, detail_id)
            if not fetch.ok:
                self._exclude_posting(
                    item, item["key"], board_source_id,
                    f"posting description fetch failed ({fetch.error or f'http status {fetch.status}'})",
                    stage="admission",
                )
                failed += 1
                continue
            parsed = parse_job_detail(url, fetch.body)
            text = str(parsed.get("text") or "").strip()
            if not text:
                self._exclude_posting(
                    item, item["key"], board_source_id,
                    "posting description fetch returned no usable text",
                    stage="admission",
                )
                failed += 1
                continue
            item["text"] = text
            item["source_id"] = detail_id
            stored = self._posting_text.get(str(item["key"]), {})
            stored["text"] = text
            stored["source_id"] = detail_id
            stored["board_source_id"] = board_source_id
            if parsed.get("posted_at") and not stored.get("posted_at"):
                stored["posted_at"] = str(parsed["posted_at"])
            self._raw_text[detail_id] = text
            self._detail_parent[detail_id] = board_source_id
            self.sources.append(
                {
                    "id": detail_id,
                    "url": url,
                    "publisher": str(item.get("employer") or ""),
                    "source_type": "job-posting-detail",
                    "retrieval_kind": "detail",
                    "parent_source_id": board_source_id,
                    "occupation_slug": self.config.occupation,
                    "retrieved_at": fetch.retrieved_at,
                    "sha256": fetch.sha256,
                    "bytes": fetch.bytes_read,
                    "rights": POSTING_DETAIL_RIGHTS,
                    "inclusion": True,
                    "posting_ids": [str(item.get("job_id") or "")],
                    "role_scope": self._role_scope(),
                }
            )
            fetched += 1
            self._ledger(
                kind="detail_fetch",
                source_id=detail_id,
                parent_source_id=board_source_id,
                url=url,
                posting_id=item["job_id"],
                bytes=fetch.bytes_read,
                sha256=fetch.sha256,
            )
        return {"fetched": fetched, "failed": failed, "excluded": excluded}

    def _variant_hint(self, text: str) -> str | None:
        lowered = text.lower()
        scores = {
            variant: sum(lowered.count(keyword) for keyword in keywords)
            for variant, keywords in VARIANT_HINTS.items()
        }
        best = max(scores, key=lambda variant: scores[variant])
        return best if scores[best] > 0 else None

    def _admission_prompt(self, batch: list[dict[str, Any]]) -> str:
        spec = self.spec
        variant_rule = (
            "This role's postings MUST be classified into exactly one of: product-growth, growth-marketing, "
            "sales-account-executive. If a posting does not fit any variant, exclude it."
            if self.config.occupation == "growth-manager"
            else 'Set "variant" to null for this role.'
        )
        rows = []
        for item in batch:
            text = admission_prompt_text(item["text"])
            hint = self._variant_hint(item["text"]) if self.config.occupation == "growth-manager" else None
            rows.append(
                {
                    "posting_id": item["key"],
                    "employer": item["employer"],
                    "title": item["title"],
                    "location": item["location"],
                    "url": item["url"],
                    "variant_hint": hint,
                    "text": text,
                }
            )
        return f"""You are the extraction/admission pass for "{spec['label']}".
Role focus: {spec['role_focus']}
{variant_rule}

For each posting below decide admission into the evidence sample, classify the work level and map the advertised skills.
Return ONE JSON object, no markdown fence:
{{"admissions": [{{"posting_id": "…", "decision": "admit"|"exclude", "reason": "…",
  "variant": "product-growth"|"growth-marketing"|"sales-account-executive"|null,
  "seniority": "mid"|"senior"|"mixed",
  "work_level": "individual_contributor"|"people_manager"|"unknown", "work_level_reason": "…",
  "people_management_quote": "byte-verbatim phrase or empty string",
  "skills": ["short phrase", "…"], "excerpt": "verbatim phrase copied from that posting's text"}}]}}

Rules: every excerpt must be copied character-for-character from the posting text you were given;
skills must be short phrases that appear in the text; exclude postings that are not genuinely this role
or that are clearly outside the stated scope, and say why. Include one row per posting_id.

Work level rules: judge the posting's own duties, never the title alone. Use "individual_contributor" only
when the posting personally performs the work (for example carrying a personal quota, owning accounts or
building workflows itself) and assigns ownership of no direct reports. Use "people_manager" when the
posting owns direct reports — hiring/staffing, coaching, managing or evaluating a team — even when the
title looks like an individual-contributor title or the posting also mentions quota or personal account
ownership; "managing" a book of business, accounts, projects or processes is not people management. Use
"unknown" when the text does not establish the level. A title containing "Manager" alone is not
people-management evidence (product, growth and marketing manager titles are often individual
contributors), and advising, supporting or coordinating managers and colleagues is not managing direct
reports. Give a short work_level_reason grounded in the posting text. When work_level is "people_manager",
copy into people_management_quote a byte-verbatim phrase from the posting text showing ownership of direct
reports (staffing, coaching or evaluating a team); otherwise set people_management_quote to "".

POSTINGS:
{json.dumps(rows, ensure_ascii=False)}"""

    def stage_admission(self) -> None:
        candidates = [item for item in self.candidates if str(item.get("text") or "").strip()]
        admitted_keys: set[str] = set()
        for start in range(0, len(candidates), ADMISSION_BATCH):
            batch = candidates[start : start + ADMISSION_BATCH]
            payload, result = self.runner.require_json("admission", self._admission_prompt(batch))
            by_key = {item["key"]: item for item in batch}
            seen_ids: set[str] = set()
            for row in payload.get("admissions") or []:
                key = str(row.get("posting_id") or "")
                if key not in by_key or key in seen_ids:
                    continue
                seen_ids.add(key)
                item = by_key[key]
                raw_text = item["text"]
                if not safe_https_url(str(item["url"])):
                    self._exclude_posting(item, key, item["source_id"], "posting link is not a plain https url", stage="admission")
                    continue
                decision = str(row.get("decision") or "").lower()
                reason = normalize_ws(str(row.get("reason") or ""))
                if decision != "admit":
                    self._exclude_posting(item, key, item["source_id"], reason or "model excluded posting", stage="admission")
                    continue
                # Scope admission is deterministic: the agent must declare a work level.
                # Only a literal individual-contributor decision with a rationale and no
                # people-management evidence enters the sample; manager ownership, unknown
                # levels and missing/malformed decisions are excluded and recorded.
                malformed_fields = [
                    name for name in ("work_level", "work_level_reason", "people_management_quote")
                    if not isinstance(row.get(name), str)
                ]
                if malformed_fields:
                    self._reject_work_level(
                        "missing_or_malformed_scope_decision", item, key,
                        "scope admission rejected: missing or non-string fields " + ", ".join(malformed_fields),
                    )
                    continue
                work_level_value = row["work_level"].strip()
                work_level = work_level_value.lower()
                work_level_reason = normalize_ws(row["work_level_reason"])[:WORK_LEVEL_REASON_MAX]
                people_management_quote = row["people_management_quote"].strip()[:int(RESEARCH_LIMITS["max_quote_chars"])]
                verified_management_quote = people_management_quote if people_management_quote in raw_text else ""
                if not work_level_value:
                    self._reject_work_level(
                        "missing_or_malformed_work_level", item, key,
                        "scope admission rejected: admission decision has no work_level "
                        "(individual_contributor|people_manager|unknown)",
                    )
                    continue
                if work_level not in WORK_LEVELS:
                    self._reject_work_level(
                        "missing_or_malformed_work_level", item, key,
                        f"scope admission rejected: malformed work_level {work_level_value!r}",
                    )
                    continue
                if work_level == "people_manager":
                    detail = work_level_reason
                    if verified_management_quote:
                        detail = f"{detail}; people-management quote: {verified_management_quote}" if detail else (
                            f"people-management quote: {verified_management_quote}"
                        )
                    self._reject_work_level(
                        "people_manager", item, key,
                        "scope admission rejected: posting owns direct reports (work_level=people_manager)"
                        + (f" — {detail}" if detail else ""),
                    )
                    continue
                if work_level == "unknown":
                    self._reject_work_level(
                        "unknown_work_level", item, key,
                        "scope admission rejected: work level not established by the posting text (work_level=unknown)"
                        + (f" — {work_level_reason}" if work_level_reason else ""),
                    )
                    continue
                if not work_level_reason:
                    self._reject_work_level(
                        "missing_work_level_reason", item, key,
                        "scope admission rejected: individual_contributor decision without work_level_reason",
                    )
                    continue
                if people_management_quote:
                    self._reject_work_level(
                        "people_management_quote", item, key,
                        "scope admission rejected: people-management evidence contradicts the individual_contributor decision"
                        + (f": {verified_management_quote}" if verified_management_quote else ""),
                    )
                    continue
                variant = row.get("variant")
                if self.config.occupation == "growth-manager":
                    if variant not in VARIANT_HINTS:
                        self._exclude_posting(item, key, item["source_id"], "variant classification failed (admission refused)", stage="admission")
                        continue
                else:
                    variant = None
                seniority = str(row.get("seniority") or "mid")
                if seniority not in ("mid", "senior", "mixed"):
                    seniority = "mid"
                skills: list[str] = []
                for skill in row.get("skills") or []:
                    phrase = normalize_ws(str(skill))
                    if phrase and phrase.lower() in raw_text.lower() and phrase not in skills:
                        skills.append(phrase[:80])
                excerpt = clamp_quote(str(row.get("excerpt") or ""))
                verified_excerpt = excerpt if excerpt and excerpt in raw_text else ""
                if not verified_excerpt:
                    self.disagreements.append(
                        {
                            "kind": "quote_verification",
                            "occupation_slug": self.config.occupation,
                            "posting_id": item["job_id"],
                            "dedup_key": key,
                            "issue": "admission excerpt not byte-verbatim in retrieved text",
                            "resolution": "excerpt dropped; no quote published",
                            "challenger": "deterministic_quote_verifier",
                            "severity": "warning",
                        }
                    )
                admitted_keys.add(key)
                self.postings.append(
                    {
                        "id": _id("pst", key, self.run_id),
                        "occupation_slug": self.config.occupation,
                        "variant": variant,
                        "employer": item["employer"],
                        "title": item["title"],
                        "location": item["location"],
                        "url": item["url"],
                        "posted_at": item["posted_at"],
                        "source_id": item["source_id"],
                        "seniority": seniority,
                        "work_level": WORK_LEVEL_ADMIT,
                        "work_level_reason": work_level_reason,
                        "people_management_quote": "",
                        "skills": skills,
                        "dedup_key": key,
                        "admission_reason": reason or "admitted by model pass",
                        "excerpt": verified_excerpt or None,
                    }
                )
                for skill in skills:
                    self.mappings.append(
                        {
                            "kind": "posting_skill",
                            "occupation_slug": self.config.occupation,
                            "posting_id": item["job_id"],
                            "dedup_key": key,
                            "employer": item["employer"],
                            "variant": variant,
                            "skill": skill,
                            "evidence": "verbatim phrase in retrieved posting text",
                            "run_id": self.run_id,
                        }
                    )
            for key, item in by_key.items():
                if key not in seen_ids:
                    self._exclude_posting(item, key, item["source_id"], "admission pass returned no decision for posting", stage="admission")
            self._ledger(kind="admission_batch", call=result.as_metadata(), admitted=len(admitted_keys))

    # -- reconciliation --------------------------------------------------

    def _synthesis_prompt(self) -> str:
        spec = self.spec
        foundations = [
            source for source in self.sources if source.get("source_type") == "foundation" and source.get("inclusion")
        ]
        foundation_blocks = []
        for source in foundations[:3]:
            text = self._raw_text.get(str(source["id"]), "")[:4000]
            foundation_blocks.append({"source_id": source["id"], "url": source["url"], "text": text})
        posting_rows = [
            {
                "posting_id": posting["dedup_key"],
                "employer": posting["employer"],
                "title": posting["title"],
                "variant": posting["variant"],
                "skills": posting["skills"][:8],
                "excerpt": posting.get("excerpt") or "",
                "source_id": posting["source_id"],
            }
            for posting in self.postings
        ]
        variant_counts: dict[str, int] = {}
        for posting in self.postings:
            variant = str(posting.get("variant") or "unspecified")
            variant_counts[variant] = variant_counts.get(variant, 0) + 1
        counts = {
            "admitted_postings": len(self.postings),
            "employers": len({posting["employer"] for posting in self.postings}),
            "variant_counts": variant_counts,
            "sampled_at": self.started_at[:10],
        }
        return f"""You are the reconciliation/synthesis pass for "{spec['label']}".
Counts already computed by the pipeline (use exactly these numbers, never invent counts): {json.dumps(counts)}

Produce evidence claims and learning priorities. Return ONE JSON object, no markdown fence:
{{"foundation_claims": [{{"statement": "…", "quote": "verbatim phrase from a foundation text below", "source_id": "…", "confidence": "bounded"}}],
  "demand_claims": [{{"topic_label": "short topic (2-6 words)", "signal": "short noun phrase (max 12 words) naming what the postings show",
     "detail": "one or two sentences of context, optional", "posting_ids": ["…"],
     "quote": "verbatim phrase of at least 4 words from one of those postings that shows the signal", "source_id": "…", "confidence": "bounded"}}],
  "learning_priorities": [{{"label": "…", "learning_outcome": "…", "rationale": "…", "uncertainty": "…",
     "confidence": "bounded"|"low", "basis": "advertised_demand"|"foundation",
     "topic_label": "the exact topic_label of one of your demand claims, or of the foundation evidence used",
     "search_terms": ["phrase that appears in the postings/foundations"]}}]}}

Rules:
- Every quote must be a self-contained phrase copied character-for-character from the provided text (at least 4
  words; not a bare keyword); the pipeline drops claims whose quote is not byte-verbatim and appends the drop reason
  to disagreements.
- "signal" must be a short noun phrase, not a sentence or count: the pipeline writes the count sentence itself from
  your topic_label and signal, so never include counts, prevalence, trends or importance in it.
- Demand claims count only within the admitted sample; never claim market prevalence, trends, or importance.
- Learning priorities are analyst recommendations derived from the evidence, not measured importance or
  proficiency. Each priority must reuse a claim topic_label above (so the pipeline can link it to recorded
  evidence), state an explicit uncertainty, and keep its search_terms inside the evidence you can see.
- Return learning_priorities in the order you recommend them (highest first); the pipeline preserves that
  authoring order and a separate bounded linking pass selects the recorded claims each priority cites.

FOUNDATIONS:
{json.dumps(foundation_blocks, ensure_ascii=False)}

ADMITTED POSTINGS:
{json.dumps(posting_rows, ensure_ascii=False)}"""

    def _linking_prompt(self, drafts: list[dict[str, Any]]) -> str:
        """Bounded linking pass: agent-selected recorded claim ids per priority.

        The prompt carries the actual recorded claims (ids, type, variant,
        statement, quote) so the links reference real recorded evidence; the
        deterministic resolver afterwards only drops ids it cannot validate.
        """

        claims = [
            {
                "claim_id": claim["id"],
                "claim_type": claim["claim_type"],
                "variant": claim.get("variant"),
                "statement": claim["statement"],
                "quote": claim.get("quote"),
            }
            for claim in self.claims
        ]
        priorities = [
            {
                "priority_id": draft["id"],
                "label": draft["label"],
                "learning_outcome": draft["learning_outcome"],
                "basis": draft["basis"],
                "topic_label": draft["topic_label"],
            }
            for draft in drafts
        ]
        return f"""You are the learning-priority evidence-linking pass for "{self.spec['label']}" ({self.config.occupation}).
Scope: {self.spec['geography']}; {self.spec['seniority']}.

Recorded claims from this run (the only valid claim_id values):
{json.dumps(claims, ensure_ascii=False)}

Draft learning priorities already authored by the reconciliation pass (labels, outcomes, rationale and order are
fixed; do not rewrite them):
{json.dumps(priorities, ensure_ascii=False)}

For every priority, select the recorded claims that substantively support it and say why. Return ONE JSON object,
no markdown fence:
{{"links": [{{"priority_id": "…", "claim_ids": ["…"], "rationale": "one sentence tying the selected claims to this priority"}}]}}

Rules:
- Use only claim_id values listed above; unknown ids are rejected and a priority whose links do not survive is dropped.
- Do not link every claim: choose only claims that genuinely support the priority; shared words are not support.
- A priority with basis "foundation" may cite only foundation claims; "advertised_demand" only advertised_demand claims.
- Growth roles: never mix variants inside one priority's links; a priority's demand claims must share one variant.
- Return one row per priority_id; an empty claim_ids list is the honest answer when no recorded claim supports it.
- At most {LINK_LIMIT} claim ids per priority; keep each rationale short."""

    def stage_synthesis(self) -> None:
        payload, result = self.runner.require_json("reconciliation", self._synthesis_prompt())
        admittted = {posting["dedup_key"]: posting for posting in self.postings}
        source_ids = {source["id"] for source in self.sources if source.get("inclusion")}
        # foundation claims
        foundation_source_ids = [
            str(source["id"]) for source in self.sources if source.get("source_type") == "foundation" and source.get("inclusion")
        ]
        for row in payload.get("foundation_claims") or []:
            statement = normalize_ws(str(row.get("statement") or ""))
            quote = clamp_quote(str(row.get("quote") or ""))
            preferred = str(row.get("source_id") or "")
            ordered = sorted(foundation_source_ids, key=lambda sid: sid != preferred)
            if not statement or not ordered:
                continue
            verified_source = ""
            for sid in ordered:
                if quote and quote in self._raw_text.get(sid, ""):
                    verified_source = sid
                    break
            if not verified_source:
                self.disagreements.append(self._quote_disagreement("foundation", statement, preferred or ",".join(ordered)))
                continue
            if HONESTY_BANNED_RE.search(statement):
                self.disagreements.append(self._honesty_disagreement(statement, "foundation claim statement"))
                continue
            claim_id = _id("clm", self.run_id, self.config.occupation, statement, quote)
            self.claims.append(
                {
                    "id": claim_id,
                    "occupation_slug": self.config.occupation,
                    "claim_type": "foundation",
                    "variant": None,
                    "statement": statement,
                    "quote": quote,
                    "source_ids": [verified_source],
                    "scope": self._role_scope(),
                    "evidence": {"kind": "official foundation excerpt", "source_id": verified_source},
                    "confidence": str(row.get("confidence") or "bounded"),
                    "tags": _tag_terms(statement),
                    "agent_run_id": self.run_id,
                    "asserted_at": _now_iso(),
                    "agent_attribution": "agent-authored synthesis over retrieved official foundation text",
                }
            )
        # demand claims ---------------------------------------------------
        for row in payload.get("demand_claims") or []:
            topic = normalize_ws(str(row.get("topic_label") or ""))
            signal = normalize_ws(str(row.get("signal") or ""))
            posting_ids = [str(pid) for pid in (row.get("posting_ids") or [])]
            matched = [admittted[pid] for pid in posting_ids if pid in admittted]
            if not topic or not matched:
                continue
            quote = clamp_quote(str(row.get("quote") or ""))
            quote_source = ""
            verified_quote = ""
            candidate_sources = [str(posting["source_id"]) for posting in matched]
            preferred = str(row.get("source_id") or "")
            if preferred in candidate_sources:
                candidate_sources.sort(key=lambda sid: sid != preferred)
            for source_id in candidate_sources:
                raw = self._raw_text.get(source_id, "")
                if quote and quote in raw:
                    verified_quote = quote
                    quote_source = source_id
                    break
            if not verified_quote:
                for posting in matched:
                    raw = self._raw_text.get(str(posting["source_id"]), "")
                    if posting.get("excerpt") and posting["excerpt"] in raw:
                        verified_quote = str(posting["excerpt"])
                        quote_source = str(posting["source_id"])
                        break
            if not verified_quote:
                self.disagreements.append(self._quote_disagreement("advertised_demand", topic, ",".join(candidate_sources)))
                continue
            matched_employers = len({posting["employer"] for posting in matched})
            admitted_total = len(self.postings)
            employers_total = len({posting["employer"] for posting in self.postings})
            variant_scope = sorted({str(posting.get("variant")) for posting in matched if posting.get("variant")})
            variant_note = f"variant scope {', '.join(variant_scope)}" if variant_scope else "no variant split for this role"
            skill_terms = [normalize_ws(str(skill)) for posting in matched for skill in (posting.get("skills") or [])]
            detail = normalize_ws(str(row.get("detail") or ""))
            signal_phrase = signal if 0 < len(signal.split()) <= 12 else topic
            statement = (
                f"{topic}: {signal_phrase} — recorded in {len(matched)} of {admitted_total} admitted {self.spec['label']} "
                f"postings ({variant_note}) from {matched_employers} of {employers_total} employers in the {self.started_at[:10]} US sample."
            )
            matched_keys = ",".join(sorted(str(posting["dedup_key"]) for posting in matched))
            claim_id = _id("clm", self.run_id, self.config.occupation, topic, matched_keys)
            self.claims.append(
                {
                    "id": claim_id,
                    "occupation_slug": self.config.occupation,
                    "claim_type": "advertised_demand",
                    "variant": variant_scope[0] if len(variant_scope) == 1 else None,
                    "statement": statement,
                    "quote": verified_quote,
                    "source_ids": sorted({quote_source, *[str(posting["source_id"]) for posting in matched]}),
                    "scope": self._role_scope(),
                    "evidence": {
                        "kind": "count within admitted sample",
                        "postings_considered": admitted_total,
                        "postings_matched": len(matched),
                        "employers_matched": matched_employers,
                        "employers_considered": employers_total,
                        "quote_source": quote_source,
                        "detail": detail,
                        "method": "agent-identified signals over the admitted retrieved sample",
                    },
                    "confidence": str(row.get("confidence") or "bounded"),
                    "tags": sorted({term for term in [topic, *_tag_terms(topic), *skill_terms] if term})[:20],
                    "agent_run_id": self.run_id,
                    "asserted_at": _now_iso(),
                    "agent_attribution": "agent-authored synthesis over admitted public postings",
                }
            )
        # learning priorities --------------------------------------------
        # The reconciliation pass authors labels/outcomes/rationale/order. A separate
        # bounded linking pass selects the recorded claim ids each priority cites;
        # deterministic validation below only enforces identity, role, basis and
        # variant discipline — it never invents relevance by lexical overlap.
        drafts: list[dict[str, Any]] = []
        for index, row in enumerate(payload.get("learning_priorities") or []):
            label = normalize_ws(str(row.get("label") or ""))
            outcome = normalize_ws(str(row.get("learning_outcome") or ""))
            rationale = normalize_ws(str(row.get("rationale") or ""))
            uncertainty = normalize_ws(str(row.get("uncertainty") or ""))
            topic = normalize_ws(str(row.get("topic_label") or "")) or label
            terms = [normalize_ws(str(term)) for term in (row.get("search_terms") or []) if normalize_ws(str(term))]
            if not (label and outcome and rationale and uncertainty):
                continue
            if HONESTY_BANNED_RE.search(rationale) or HONESTY_BANNED_RE.search(outcome):
                self.disagreements.append(self._honesty_disagreement(rationale or outcome, "learning priority"))
                continue
            drafts.append(
                {
                    "id": _id("req", self.run_id, self.config.occupation, str(index), label, outcome),
                    "index": index,
                    "label": label,
                    "learning_outcome": outcome,
                    "rationale": rationale,
                    "uncertainty": uncertainty,
                    "confidence": str(row.get("confidence") or "low"),
                    "basis": "foundation" if str(row.get("basis") or "").strip() == "foundation" else "advertised_demand",
                    "topic_label": topic,
                    "search_terms": terms,
                }
            )
        links_by_priority: dict[str, dict[str, Any]] = {}
        if drafts and self.claims:
            links_payload, links_result = self.runner.require_json("evidence_linking", self._linking_prompt(drafts))
            for link_row in links_payload.get("links") or []:
                priority_id = str(link_row.get("priority_id") or "")
                if priority_id and priority_id not in links_by_priority:
                    links_by_priority[priority_id] = link_row
            self._ledger(
                kind="evidence_linking",
                call=links_result.as_metadata(),
                priorities=len(drafts),
                link_rows=len(links_by_priority),
            )
        for draft in drafts:
            resolved = resolve_priority_links(
                occupation=self.config.occupation,
                claims=self.claims,
                priority_id=draft["id"],
                basis=draft["basis"],
                link_row=links_by_priority.get(draft["id"]),
            )
            self.disagreements.extend(resolved["disagreements"])
            if not resolved["claim_ids"]:
                continue
            evidence_rationale = str(resolved["rationale"] or "")
            if evidence_rationale and HONESTY_BANNED_RE.search(evidence_rationale):
                self.disagreements.append(
                    {
                        "kind": "honesty_filter",
                        "occupation_slug": self.config.occupation,
                        "priority_id": draft["id"],
                        "issue": f"link rationale used prevalence/trend/absolute language: {evidence_rationale[:160]!r}",
                        "resolution": "link rationale omitted; validated links retained",
                        "challenger": "deterministic_honesty_filter",
                        "severity": "warning",
                    }
                )
                evidence_rationale = ""
            self.requirements.append(
                {
                    "id": draft["id"],
                    "occupation_slug": self.config.occupation,
                    "label": draft["label"],
                    "learning_outcome": draft["learning_outcome"],
                    "rationale": draft["rationale"],
                    "uncertainty": draft["uncertainty"],
                    "confidence": draft["confidence"],
                    "basis": draft["basis"],
                    "variant": resolved["variant"],
                    "search_terms": self._grounded_terms(draft["search_terms"]),
                    "evidence_claim_ids": resolved["claim_ids"],
                    "evidence_rationale": evidence_rationale,
                    "model_order": draft["index"],
                }
            )
        for rank, requirement in enumerate(self.requirements, start=1):
            requirement["priority"] = rank
        self._ledger(
            kind="reconciliation",
            call=result.as_metadata(),
            foundation_claims=len([c for c in self.claims if c["claim_type"] == "foundation"]),
            demand_claims=len([c for c in self.claims if c["claim_type"] == "advertised_demand"]),
            learning_priorities=len(self.requirements),
        )

    def _quote_disagreement(self, kind: str, label: str, source_id: str) -> dict[str, Any]:
        return {
            "kind": "quote_verification",
            "occupation_slug": self.config.occupation,
            "issue": f"{kind} quote for {label!r} was not byte-verbatim in retrieved text",
            "resolution": "claim dropped; no unverified quote was published",
            "challenger": "deterministic_quote_verifier",
            "severity": "blocker",
        }

    def _honesty_disagreement(self, text: str, what: str) -> dict[str, Any]:
        return {
            "kind": "honesty_filter",
            "occupation_slug": self.config.occupation,
            "issue": f"{what} used prevalence/trend/absolute language: {text[:160]!r}",
            "resolution": "dropped by deterministic honesty filter",
            "challenger": "deterministic_honesty_filter",
            "severity": "blocker",
        }

    def _published_evidence_text(self) -> str:
        """Lowercased text mirroring what the published release will carry.

        Used to ground recommendation search terms in material that survives
        release validation (claims, postings, foundation extracts).
        """

        parts: list[str] = []
        for claim in self.claims:
            parts.extend(
                [
                    str(claim.get("statement") or ""),
                    str(claim.get("quote") or ""),
                    " ".join(str(tag) for tag in (claim.get("tags") or [])),
                ]
            )
        for posting in self.postings:
            parts.extend(
                [
                    str(posting.get("title") or ""),
                    str(posting.get("employer") or ""),
                    " ".join(str(skill) for skill in (posting.get("skills") or [])),
                    str(posting.get("excerpt") or ""),
                ]
            )
        for source in self.sources:
            if source.get("source_type") == "foundation" and source.get("inclusion"):
                parts.append(self._raw_text.get(str(source.get("id")), "")[:6000])
        return " ".join(parts).lower()

    def _grounded_terms(self, terms: list[str]) -> list[str]:
        text = self._published_evidence_text()
        return [term for term in terms if term and term.lower() in text]

    # -- challenge -------------------------------------------------------

    def _challenge_prompt(self) -> str:
        claims = [
            {
                "claim_id": claim["id"],
                "type": claim["claim_type"],
                "statement": claim["statement"],
                "quote": claim.get("quote"),
                "evidence": claim.get("evidence"),
            }
            for claim in self.claims
        ]
        return f"""You are the challenge/skeptic pass for "{self.spec['label']}".
Attack these draft claims: find unsupported statements, prevalence/trend overreach, quotes that cannot support
the claim, and conflation of the growth variants product-growth / growth-marketing / sales-account-executive.
Return ONE JSON object, no markdown fence:
{{"challenges": [{{"claim_id": "…", "issue": "…", "severity": "blocker"|"warning", "action": "drop"|"keep"}}],
  "verdict": "approve"|"revise"}}
Every claim with a blocker challenge is dropped by the pipeline; record why.

DRAFT CLAIMS:
{json.dumps(claims, ensure_ascii=False)}"""

    def stage_challenge(self) -> None:
        payload, result = self.runner.require_json("challenge", self._challenge_prompt())
        by_id = {claim["id"]: claim for claim in self.claims}
        dropped: list[str] = []
        seen: set[str] = set()
        for row in payload.get("challenges") or []:
            claim_id = str(row.get("claim_id") or "")
            issue = normalize_ws(str(row.get("issue") or ""))
            severity = str(row.get("severity") or "warning")
            action = str(row.get("action") or "keep")
            if claim_id not in by_id or claim_id in seen:
                continue
            seen.add(claim_id)
            resolution = "retained after challenge"
            if severity == "blocker" or action == "drop":
                dropped.append(claim_id)
                resolution = "dropped by challenge pass"
            self.disagreements.append(
                {
                    "kind": "challenge",
                    "occupation_slug": self.config.occupation,
                    "claim_id": claim_id,
                    "claim_statement": by_id[claim_id]["statement"][:300],
                    "issue": issue or "challenge recorded without detail",
                    "severity": severity,
                    "resolution": resolution,
                    "challenger": "agent challenge pass (pinned task model)",
                }
            )
        if dropped:
            self.claims = [claim for claim in self.claims if claim["id"] not in set(dropped)]
            valid_claim_ids = {claim["id"] for claim in self.claims}
            self.requirements = [
                {
                    **requirement,
                    "evidence_claim_ids": [
                        cid for cid in requirement["evidence_claim_ids"] if cid in valid_claim_ids
                    ],
                }
                for requirement in self.requirements
            ]
            self.requirements = [requirement for requirement in self.requirements if requirement["evidence_claim_ids"]]
        self._ledger(kind="challenge", call=result.as_metadata(), challenges=len(seen), dropped=len(dropped))

    # -- slice assembly and publish --------------------------------------

    def _origin_board_id(self, source_id: str) -> str:
        """The board listing id backing a posting's cited origin source."""

        return self._detail_parent.get(str(source_id), str(source_id))

    def _claim_excerpt_row(self, claim: dict[str, Any]) -> dict[str, Any]:
        return {
            "posting_id": f"claim:{claim['id']}",
            "employer": "",
            "title": "",
            "location": "",
            "url": "",
            "variant": None,
            "span": str(claim.get("quote") or ""),
        }

    def _excerpts_for_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        """Verified short spans attributable to this exact source response.

        A span is published only under the source whose raw retrieved bytes
        contain it: foundation quotes under the foundation page, a posting's
        verified excerpt under the origin source that supplied its text (board
        listing or per-posting detail response), and claim quotes under their
        verified quote origin. Full job descriptions are never published.
        """

        source_id = str(source["id"])
        raw = self._raw_text.get(source_id, "")
        excerpts: list[dict[str, Any]] = []
        if str(source.get("source_type") or "") == "foundation":
            for claim in self.claims:
                quote = str(claim.get("quote") or "")
                evidence = claim.get("evidence") if isinstance(claim.get("evidence"), dict) else {}
                if str(evidence.get("source_id") or "") != source_id or not quote or quote not in raw:
                    continue
                excerpts.append(self._claim_excerpt_row(claim))
            return excerpts
        for posting in self.postings:
            if str(posting["source_id"]) != source_id or not posting.get("excerpt"):
                continue
            excerpts.append(
                {
                    "posting_id": posting["dedup_key"],
                    "employer": posting["employer"],
                    "title": posting["title"],
                    "location": posting["location"],
                    "url": posting["url"],
                    "variant": posting.get("variant"),
                    "span": posting["excerpt"],
                }
            )
        for claim in self.claims:
            quote = str(claim.get("quote") or "")
            if not quote:
                continue
            evidence = claim.get("evidence") if isinstance(claim.get("evidence"), dict) else {}
            verified_sources = [
                str(sid) for sid in (claim.get("source_ids") or []) if quote in self._raw_text.get(str(sid), "")
            ]
            if not verified_sources:
                continue
            target = str(evidence.get("quote_source") or verified_sources[0])
            if target not in verified_sources:
                target = verified_sources[0]
            if source_id != target:
                continue
            if any(
                str(posting["source_id"]) == source_id and posting.get("excerpt") == quote
                for posting in self.postings
            ):
                continue
            excerpts.append(self._claim_excerpt_row(claim))
        return excerpts

    def _build_slice(self) -> dict[str, Any]:
        slice_dir = self.run_dir / "slice"
        extracts_dir = slice_dir / "extracts"
        extracts_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(self.sources):
            source_id = str(source["id"])
            source_row = dict(source)
            source_type = str(source.get("source_type") or "")
            raw = self._raw_text.get(source_id, "")
            excerpts = self._excerpts_for_source(source_row)
            text = ""
            if source_type == "foundation":
                text = build_published_extract(
                    source_id=source_id,
                    url=str(source["url"]),
                    retrieved_at=str(source.get("retrieved_at") or ""),
                    publisher=str(source.get("publisher") or ""),
                    rights=str(source.get("rights") or ""),
                    raw_sha256=str(source.get("sha256") or ""),
                    raw_bytes=int(source.get("bytes") or 0),
                    source_type="foundation",
                    excerpts=excerpts,
                    foundation_text=raw[:6000],
                )
            elif excerpts:
                text = build_published_extract(
                    source_id=source_id,
                    url=str(source["url"]),
                    retrieved_at=str(source.get("retrieved_at") or ""),
                    publisher=str(source.get("publisher") or ""),
                    rights=str(source.get("rights") or ""),
                    raw_sha256=str(source.get("sha256") or ""),
                    raw_bytes=int(source.get("bytes") or 0),
                    source_type=source_type,
                    excerpts=excerpts,
                    retrieval_kind=str(source.get("retrieval_kind") or ""),
                    parent_source_id=str(source.get("parent_source_id") or ""),
                )
            if text:
                (extracts_dir / f"{source_id}.txt").write_text(text, encoding="utf-8")
                self.extracts[source_id] = text
                source_row["extract_path"] = f"extracts/{source_id}.txt"
                source_row["extract_sha256"] = sha256_text(text)
            self.sources[index] = source_row

        stats = {
            "total_seen": len(self._posting_text),
            "sampled": len(self._posting_text),
            "prefilter_candidates": len(self.candidates),
            "detail_text_fetches": int(self.selection_summary.get("detail_fetches") or 0),
            "detail_sources_used": len(
                {str(posting["source_id"]) for posting in self.postings if str(posting["source_id"]) in self._detail_parent}
            ),
            "postings_dedup": len(self.postings),
            "employers_dedup": len({posting["employer"] for posting in self.postings}),
            "boards_attempted": len([source for source in self.sources if source.get("source_type") == "job-board"]),
            "boards_used": len({self._origin_board_id(str(posting["source_id"])) for posting in self.postings}),
            "excluded_total": len(self.exclusions),
            "foundations_used": len(
                [source for source in self.sources if source.get("source_type") == "foundation" and source.get("inclusion")]
            ),
        }
        occupation_row: dict[str, Any] = {
            "slug": self.config.occupation,
            "label": self.spec["label"],
            "family": self.spec["family"],
            "aliases": self.spec["aliases"],
            "role_scope": self._role_scope(),
            "sampled_at": self.started_at,
            "sampling_note": (
                "Counts describe only the job-board postings enumerated in the retrieval window for this role; "
                f"they are not market prevalence. Enumerated boards were listed once per board API at the timestamp above, "
                f"sampled {SAMPLE_ORDER}; boards whose listing API pages or truncates report their enumerated/total bound "
                "in sources.json and board denominators are unaffected by per-posting detail retrievals. The candidate "
                "cap was allocated fairly across retrieved employers and provisional title/variant buckets; cap "
                "exclusions are recorded per posting. Posting text and quotes are attributed to the exact retrieved "
                "response that contains them (sources.json retrieval_kind/parent_source_id); only short excerpts are "
                "published."
            ),
            "selection": dict(self.selection_summary),
            "stats": stats,
            "run_ids": [self.run_id],
        }
        if self.config.occupation == "growth-manager":
            occupation_row["growth_variants"] = list(VARIANT_HINTS)
        posts = [
            {key: value for key, value in posting.items() if key != "excerpt"} for posting in self.postings
        ]
        (slice_dir / "occupations.json").write_text(json.dumps([occupation_row], indent=2) + "\n", encoding="utf-8")
        (slice_dir / "sources.json").write_text(json.dumps(self.sources, indent=2) + "\n", encoding="utf-8")
        (slice_dir / "postings.json").write_text(json.dumps(posts, indent=2) + "\n", encoding="utf-8")
        (slice_dir / "claims.json").write_text(json.dumps(self.claims, indent=2) + "\n", encoding="utf-8")
        (slice_dir / "requirements.json").write_text(json.dumps(self.requirements, indent=2) + "\n", encoding="utf-8")
        if self.exclusions:
            (slice_dir / "exclusions.json").write_text(json.dumps(self.exclusions, indent=2) + "\n", encoding="utf-8")
        if self.mappings:
            (slice_dir / "mappings.json").write_text(json.dumps(self.mappings, indent=2) + "\n", encoding="utf-8")
        if self.disagreements:
            (slice_dir / "disagreements.json").write_text(json.dumps(self.disagreements, indent=2) + "\n", encoding="utf-8")
        return {
            "slice_dir": str(slice_dir),
            "stats": stats,
            "occupation_row": occupation_row,
        }

    # -- receipt ---------------------------------------------------------

    def _lineage_row(self, publish_result: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "occupation_slug": self.config.occupation,
            "occupations": [self.config.occupation],
            "provider": self.runner.calls[-1].provider if self.runner.calls else "",
            "model": self.runner.calls[-1].model if self.runner.calls else "",
            "thinking": self.runner.calls[-1].thinking if self.runner.calls else "",
            "model_fallback": self.runner.calls[-1].fallback if self.runner.calls else None,
            "started_at": self.started_at,
            "finished_at": _now_iso(),
            "question": self.config.question,
            "retrievals": self.retrievals,
            "model_calls": len(self.runner.calls),
            "boards_attempted": len([s for s in self.sources if s.get("source_type") == "job-board"]),
            "boards_used": len({self._origin_board_id(str(posting["source_id"])) for posting in self.postings}),
            "detail_sources": len(self._detail_parent),
            "foundations_included": len(
                [s for s in self.sources if s.get("source_type") == "foundation" and s.get("inclusion")]
            ),
            "postings_seen": len(self._posting_text),
            "postings_admitted": len(self.postings),
            "excluded": len(self.exclusions),
            "stages": {
                "discovery": {
                    "foundations_targeted": len(self.discovery.get("foundations") or []),
                    "boards_targeted": len(self.discovery.get("boards") or []),
                    "search_terms": self.discovery.get("search_terms") or [],
                },
                "retrieval": {"retrievals": self.retrievals},
                "candidate_selection": dict(self.selection_summary),
                "discovery_feedback": {
                    "triggered": bool(self.discovery_feedback),
                    "boards_proposed": len((self.discovery_feedback or {}).get("boards") or []),
                },
                "admission": {
                    "admitted": len(self.postings),
                    "excluded": len(self.exclusions),
                    "work_level_rejections": dict(sorted(self.work_level_rejections.items())),
                },
                "reconciliation": {
                    "foundation_claims": len([c for c in self.claims if c["claim_type"] == "foundation"]),
                    "demand_claims": len([c for c in self.claims if c["claim_type"] == "advertised_demand"]),
                    "learning_priorities": len(self.requirements),
                },
                "challenge": {"disagreements": len(self.disagreements)},
            },
            "publish": publish_result or {"status": "not_published"},
            "skip_rules_applied": sorted({str(row.get("kind")) for row in self.disagreements}),
        }

    def _write_lineage(self, publish_result: dict[str, Any] | None) -> dict[str, Any]:
        row = self._lineage_row(publish_result)
        (self.run_dir / "slice" / "lineage.json").write_text(json.dumps([row], indent=2) + "\n", encoding="utf-8")
        return row

    def _write_receipt(self, status: str, publish_result: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
        artifacts: dict[str, dict[str, str]] = {}
        for relative in (
            "slice/occupations.json",
            "slice/sources.json",
            "slice/postings.json",
            "slice/claims.json",
            "slice/requirements.json",
        ):
            path = self.run_dir / relative
            if path.is_file():
                artifacts[relative] = {"path": str(path), "sha256": sha256_file(path)}
        session_records = [
            {"path": call.session_record, "sha256": call.session_sha256}
            for call in self.runner.calls
            if call.session_record
        ]
        receipt = {
            "receipt_schema": "market-run-receipt/1",
            "run_id": self.run_id,
            "occupation": self.config.occupation,
            "provider": self.runner.calls[-1].provider if self.runner.calls else "",
            "model": self.runner.calls[-1].model if self.runner.calls else "",
            "model_fallback": self.runner.calls[-1].fallback if self.runner.calls else None,
            "thinking": self.runner.calls[-1].thinking if self.runner.calls else "",
            "started_at": self.started_at,
            "finished_at": _now_iso(),
            "execution_context": "local-runtime",
            "sampling": {
                "geography": self.spec["geography"],
                "seniority": self.spec["seniority"],
                "denominators": {
                    "retrievals": self.retrievals,
                    "postings_seen": len(self._posting_text),
                    "prefilter_candidates": len(self.candidates),
                    "detail_fetches": int(self.selection_summary.get("detail_fetches") or 0),
                    "detail_sources_retained": len(self._detail_parent),
                    "postings_admitted": len(self.postings),
                    "employers_admitted": len({posting["employer"] for posting in self.postings}),
                    "boards_attempted": len([s for s in self.sources if s.get("source_type") == "job-board"]),
                    "boards_used": len({self._origin_board_id(str(posting["source_id"])) for posting in self.postings}),
                    "model_calls": len(self.runner.calls),
                },
            },
            "artifacts": artifacts,
            "session_records": session_records,
            "fixtures_used": False,
            "discovery": [self.discovery] if self.discovery else [],
            "discovery_feedback": [self.discovery_feedback] if self.discovery_feedback else [],
            "candidate_selection": dict(self.selection_summary),
            "retrieval": [source for source in self.sources],
            "extraction": [
                {
                    "posting_id": posting["id"],
                    "dedup_key": posting["dedup_key"],
                    "employer": posting["employer"],
                    "variant": posting.get("variant"),
                    "source_id": posting["source_id"],
                    "work_level": str(posting.get("work_level") or ""),
                    "work_level_reason": str(posting.get("work_level_reason") or ""),
                    "people_management_quote": str(posting.get("people_management_quote") or ""),
                    "skills": posting.get("skills") or [],
                    "quote_verified": bool(posting.get("excerpt")),
                }
                for posting in self.postings
            ],
            "admission": {
                "admitted": len(self.postings),
                "work_level_rejections": dict(sorted(self.work_level_rejections.items())),
            },
            "reconciliation": {
                "claims": [claim["id"] for claim in self.claims],
                "learning_priorities": [requirement["id"] for requirement in self.requirements],
            },
            "challenge": {
                "disagreements": self.disagreements,
                "claims_after_challenge": len(self.claims),
            },
            "model_calls": self.runner.receipt_calls(),
            "cost_usd_total": round(self.budget.cost_usd, 6),
            "publish": publish_result or {"status": "skipped"},
            "status": status,
            "error": error,
            "resources": {
                "limits": {key: RESEARCH_LIMITS[key] for key in (
                    "max_retrievals", "max_model_calls", "max_response_bytes", "serialized_calls"
                )},
                "used": {"retrievals": self.retrievals, "model_calls": len(self.runner.calls)},
            },
        }
        receipts_dir = Path(self.config.evidence_root) / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = receipts_dir / f"{self.run_id}.json"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        (self.run_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        self._ledger(kind="run_summary", status=status, receipt=str(receipt_path), error=error)
        return receipt

    # -- driver ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        status = "blocked"
        publish_result: dict[str, Any] | None = None
        error: str | None = None
        try:
            self.stage_discovery()
            self.stage_retrieval()
            self.stage_candidate_selection()
            self.stage_admission()
            self.stage_synthesis()
            self.stage_challenge()
            self._build_slice()
            blocked_reasons = self._publish_preconditions()
            if blocked_reasons:
                status = "validated_not_published"
                error = "; ".join(blocked_reasons)
                self._write_lineage({"status": "blocked", "reasons": blocked_reasons})
            elif not self.config.publish:
                status = "validated_not_published"
                self._write_lineage({"status": "skipped"})
            else:
                self._write_lineage({"status": "validated"})
                candidate = merge_slice(Path(self.config.release_root), self.run_dir / "slice")
                publish_result = publish_release(
                    Path(self.config.release_root),
                    candidate,
                )
                status = "published"
                self._write_lineage({"status": "published", "release_id": publish_result["release_id"]})
        except (ResearchError, PublishError) as exc:
            error = str(exc)
            status = "blocked"
            try:
                self._write_lineage({"status": "blocked", "error": error})
            except Exception:  # noqa: BLE001 - preserve original failure
                pass
        receipt = self._write_receipt(status, publish_result, error)
        return {
            "run_id": self.run_id,
            "occupation": self.config.occupation,
            "status": status,
            "release_id": (publish_result or {}).get("release_id"),
            "run_dir": str(self.run_dir),
            "receipt": str(Path(self.config.evidence_root) / "receipts" / f"{self.run_id}.json"),
            "stats": {
                "retrievals": self.retrievals,
                "model_calls": len(self.runner.calls),
                "admitted_postings": len(self.postings),
                "employers": len({posting["employer"] for posting in self.postings}),
                "claims": len(self.claims),
                "learning_priorities": len(self.requirements),
                "excluded": len(self.exclusions),
                "disagreements": len(self.disagreements),
            },
            "publish": publish_result,
            "error": error,
            "cost_usd_total": round(self.budget.cost_usd, 6),
        }

    def _raw_linkage_problems(self) -> list[str]:
        """Byte-level re-verification against the compiler's raw retrieved text.

        The release validator re-checks quotes against the published extract
        files; this pass re-checks them against the raw bytes/text the run
        actually retrieved, so a quote can never be published under a source
        response that does not contain it (e.g. a compact metadata listing).
        """

        problems: list[str] = []
        for claim in self.claims:
            quote = str(claim.get("quote") or "")
            if not quote:
                continue
            if not any(quote in self._raw_text.get(str(sid), "") for sid in (claim.get("source_ids") or [])):
                problems.append(
                    f"claim {claim['id']}: quote is not byte-verbatim in the raw text of any cited source"
                )
        for posting in self.postings:
            excerpt = str(posting.get("excerpt") or "")
            if excerpt and excerpt not in self._raw_text.get(str(posting.get("source_id") or ""), ""):
                problems.append(
                    f"posting {posting['dedup_key']}: verified excerpt is not in the raw text of its origin source"
                )
        return problems

    def _publish_preconditions(self) -> list[str]:
        reasons: list[str] = []
        if not any(source.get("source_type") == "foundation" and source.get("inclusion") for source in self.sources):
            reasons.append("no official foundation source retrieved for this role")
        foundation_claims = [claim for claim in self.claims if claim["claim_type"] == "foundation"]
        if not foundation_claims:
            reasons.append("no foundation claims survived verification")
        if len(self.postings) < self.config.min_postings:
            reasons.append(
                f"{len(self.postings)} admitted postings is below the required minimum {self.config.min_postings}"
            )
        if self.config.occupation == "growth-manager":
            variants = {str(posting.get("variant")) for posting in self.postings}
            missing = [variant for variant in VARIANT_HINTS if variant not in variants]
            if missing:
                reasons.append(f"growth variants without admitted postings: {missing}")
        if not self.requirements:
            reasons.append("no learning priorities survived verification")
        if not self.claims:
            reasons.append("no claims survived verification")
        reasons.extend(self._raw_linkage_problems())
        for source_id, parent_id in sorted(self._detail_parent.items()):
            if not any(str(source.get("id")) == parent_id for source in self.sources):
                reasons.append(f"detail source {source_id}: parent board source {parent_id} is not recorded")
        return reasons


def title_matches_hints(title: str, hints: tuple[str, ...] | list[str]) -> bool:
    """True when a hint appears in the title as a whole word/phrase.

    Word-boundary matching keeps short hints honest: bare ``hr`` matches "HR
    Consultant" but not "T|hr|eat Investigator", and ``sales`` does not match
    "Salesforce Administrator".
    """

    lowered = str(title or "").lower()
    for hint in hints:
        normalized = str(hint or "").strip().lower()
        if normalized and re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered):
            return True
    return False


def _tag_terms(text: str) -> list[str]:
    from .core import tokenize

    return sorted(set(tokenize(text)))[:12]


def resolve_priority_links(
    *,
    occupation: str,
    claims: list[dict[str, Any]],
    priority_id: str,
    basis: str,
    link_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate agent-selected evidence links for one learning priority.

    The choice of linked claims is the agent's; this resolver only enforces
    identity, role, basis and variant discipline. Unknown, other-role or
    wrong-basis ids are dropped individually; growth roles reject a priority
    whose links cross variants; a priority with no surviving link is dropped
    entirely. Every rejection is returned as an inspectable disagreement.
    """

    wanted_basis = "foundation" if str(basis) == "foundation" else "advertised_demand"
    by_id = {str(claim.get("id")): claim for claim in claims}
    disagreements: list[dict[str, Any]] = []

    def reject(kind: str, issue: str, severity: str = "warning") -> None:
        disagreements.append(
            {
                "kind": kind,
                "occupation_slug": occupation,
                "priority_id": priority_id,
                "issue": issue,
                "resolution": "link dropped; only agent-selected recorded claims survive",
                "challenger": "deterministic_evidence_linker",
                "severity": severity,
            }
        )

    if not isinstance(link_row, dict):
        reject(
            "unlinked_learning_priority",
            "linking pass returned no decision for this priority",
            "blocker",
        )
        return {"claim_ids": [], "rationale": "", "variant": None, "disagreements": disagreements}

    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_ids = link_row.get("claim_ids")
    if not isinstance(raw_ids, list):
        raw_ids = []
    for raw in raw_ids[:32]:  # defensive bound: links are prompt-bounded to a handful
        claim_id = str(raw or "")
        if not claim_id or claim_id in seen:
            continue
        seen.add(claim_id)
        claim = by_id.get(claim_id)
        if claim is None:
            reject("invalid_priority_link", f"claim_id {claim_id!r} is not a recorded claim for this run")
            continue
        if str(claim.get("occupation_slug") or "") != occupation:
            reject("invalid_priority_link", f"claim {claim_id!r} belongs to another occupation")
            continue
        claim_type = str(claim.get("claim_type") or "")
        if claim_type != wanted_basis:
            reject(
                "invalid_priority_link",
                f"{wanted_basis} priority cannot cite {claim_type or 'untyped'} claim {claim_id!r}; "
                "foundation and advertised demand stay distinct",
            )
            continue
        accepted.append(claim)

    variant: str | None = None
    if occupation == "growth-manager":
        variants = {str(claim.get("variant")) for claim in accepted if claim.get("variant")}
        if len(variants) > 1:
            reject(
                "cross_variant_priority_link",
                f"links cross growth variants {sorted(variants)}; a priority must not conflate variants",
                "blocker",
            )
            return {"claim_ids": [], "rationale": "", "variant": None, "disagreements": disagreements}
        variant = next(iter(variants), None)

    claim_ids = [str(claim["id"]) for claim in accepted][:LINK_LIMIT]
    rationale = normalize_ws(str(link_row.get("rationale") or ""))
    if not claim_ids:
        reject(
            "unlinked_learning_priority",
            "no agent-selected recorded claim supports this priority",
            "blocker",
        )
    return {"claim_ids": claim_ids, "rationale": rationale, "variant": variant, "disagreements": disagreements}


def probe_runtime(*, evidence_root: Path, overlay: Path, run_id: str | None = None) -> dict[str, Any]:
    """One bounded model call proving the pinned subscription model resolves.

    Fails closed (exit 2 from the CLI) when the session record reports a
    different provider/model, ``resolvedModelIsFallback: true``, a different
    thinking level, or an unparseable reply.
    """

    from .agent import extract_json_object

    evidence_root = Path(evidence_root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    probe_id = run_id or f"probe_{stamp}_{secrets.token_hex(2)}"
    run_dir = evidence_root / "runs" / probe_id
    run_dir.mkdir(parents=True, exist_ok=True)
    runner = OmpAgentRunner(run_dir=run_dir, overlay=Path(overlay))
    prompt = (
        'Connectivity probe for the market research pipeline. Do not use tools. '
        'Reply with exactly the JSON object {"probe":"ok"} and no other text.'
    )
    receipt: dict[str, Any] = {
        "receipt_schema": "market-probe-receipt/1",
        "probe_id": probe_id,
        "started_at": _now_iso(),
        "execution_context": "local-runtime",
        "fixtures_used": False,
        "prompt_sha256": sha256_text(prompt),
        "model_pin": MODEL_PIN,
    }
    try:
        result = runner.call("probe", prompt)
        payload = extract_json_object(result.text)
        ok = bool(result.ok and payload and payload.get("probe") == "ok")
        receipt.update(
            {
                "ok": ok,
                "provider": result.provider,
                "model": result.model,
                "thinking": result.thinking,
                "model_fallback": result.fallback,
                "exit_code": result.exit_code,
                "attempts": result.attempts,
                "error": result.error,
                "problems": result.problems,
                "session_records": [
                    {"path": call.session_record, "sha256": call.session_sha256}
                    for call in runner.calls
                    if call.session_record
                ],
                "reply_sha256": sha256_text(result.text),
                "cost_usd": round(runner.budget.cost_usd, 6),
            }
        )
    except ResearchError as exc:
        receipt.update({"ok": False, "error": str(exc), "problems": [str(exc)]})
    receipt["finished_at"] = _now_iso()
    receipts_dir = evidence_root / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / f"{probe_id}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    receipt["receipt"] = str(receipt_path)
    return receipt


def run_research(config: ResearchConfig) -> dict[str, Any]:
    return ResearchRun(config).run()


__all__ = ["OCCUPATION_CONFIG", "ResearchConfig", "ResearchRun", "admission_prompt_text", "probe_runtime", "resolve_priority_links", "run_research"]
