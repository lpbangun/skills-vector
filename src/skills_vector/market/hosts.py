"""Source-host allowlist (SSRF guard) for the market research pipeline.

The research pipeline retrieves only public occupational foundations and public
employer job-board APIs. Every URL is checked here before retrieval and before
a release records a source; redirects off the allowlist are refused.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

TOKEN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"

JOB_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"

# host -> (kind, compiled path rule)
ALLOWED_HOSTS: dict[str, tuple[str, re.Pattern[str]]] = {
    "www.onetonline.org": ("foundation", re.compile(r"^/link/summary/[0-9]{2}-[0-9]{4}\.[0-9]{2}/?$")),
    "www.bls.gov": ("foundation", re.compile(r"^/ooh/[a-z0-9/_-]*\.htm$")),
    "www.onetcenter.org": ("foundation", re.compile(r"^/dl_files/[A-Za-z0-9._-]+\.(?:zip|json|xlsx)$")),
    "boards-api.greenhouse.io": ("job-board", re.compile(rf"^/v1/boards/{TOKEN}/jobs(?:/[0-9]{{1,20}})?/?$")),
    "api.lever.co": ("job-board", re.compile(rf"^/v0/postings/{TOKEN}/?$")),
    "api.ashbyhq.com": ("job-board", re.compile(rf"^/posting-api/job-board/{TOKEN}/?$")),
    "api.smartrecruiters.com": ("job-board", re.compile(rf"^/v1/companies/{TOKEN}/postings(?:/{JOB_ID})?/?$")),
    "apply.workable.com": ("job-board", re.compile(rf"^/api/v1/widget/accounts/{TOKEN}/?$")),
}

FOUNDATION_HOSTS = tuple(host for host, (kind, _) in ALLOWED_HOSTS.items() if kind == "foundation")
JOB_BOARD_HOSTS = tuple(host for host, (kind, _) in ALLOWED_HOSTS.items() if kind == "job-board")
ALLOWED_SOURCE_HOSTS = tuple(sorted(ALLOWED_HOSTS))


def host_kind(host: str) -> str | None:
    entry = ALLOWED_HOSTS.get(host.strip().lower())
    return entry[0] if entry else None


def url_problem(url: str) -> str | None:
    """Return a refusal reason, or ``None`` when the URL is retrievable."""

    if not isinstance(url, str) or not url.strip():
        return "empty url"
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        return f"scheme {parts.scheme!r} not allowed (https only)"
    if parts.username or parts.password:
        return "userinfo not allowed"
    if parts.port is not None:
        return "explicit port not allowed"
    host = (parts.hostname or "").lower()
    entry = ALLOWED_HOSTS.get(host)
    if entry is None:
        return f"host {host!r} not in source allowlist"
    if not entry[1].fullmatch(parts.path):
        return f"path {parts.path!r} not allowed for host {host!r}"
    if parts.query and not re.fullmatch(r"[A-Za-z0-9._~%&=+-]*", parts.query):
        return "query contains disallowed characters"
    return None


def url_is_allowed(url: str) -> bool:
    return url_problem(url) is None
