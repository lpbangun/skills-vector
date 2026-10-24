"""Allowlisted retrieval with hard bounds and recorded attempts.

By default only hosts in :mod:`skills_vector.market.hosts` are retrievable.
The structured-career lane supplies an explicit roster-site scope callback;
this never changes the default research allowlist. Redirects are
re-checked against the allowlist, response size is capped while streaming, and
transient network errors get at most two infrastructure retries (the cap is
fixed in :mod:`skills_vector.market.limits`; semantic failures are never
retried here).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from urllib.parse import urljoin

from .hosts import url_problem
from .limits import RESEARCH_LIMITS
from .release import sha256_bytes

USER_AGENT = "skills-vector-research/1.0 (+public occupational reference; contact: local operator)"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Transport(Protocol):
    def __call__(self, url: str) -> tuple[int, dict[str, str], bytes]: ...


class AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - stdlib signature
        target = urljoin(req.full_url, newurl)
        reason = url_problem(target)
        if reason:
            raise urllib.error.HTTPError(target, code, f"redirect refused: {reason}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, target)


def urllib_transport(url: str, *, timeout: float | None = None) -> tuple[int, dict[str, str], bytes]:
    timeout = float(timeout if timeout is not None else RESEARCH_LIMITS["retrieval_timeout_seconds"])
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    opener = urllib.request.build_opener(AllowlistRedirectHandler)
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - allowlisted https only
        headers = {key.lower(): value for key, value in response.headers.items()}
        return int(response.status), headers, response.read(int(RESEARCH_LIMITS["max_response_bytes"]) + 1)


@dataclass
class FetchResult:
    url: str
    status: int | None
    body: bytes = b""
    content_type: str = ""
    sha256: str = ""
    bytes_read: int = 0
    attempts: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    truncated: bool = False
    retrieved_at: str = field(default_factory=_now_iso)
    location: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None and not self.truncated

    def as_metadata(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "bytes_read": self.bytes_read,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "truncated": self.truncated,
            "retrieved_at": self.retrieved_at,
        }


def fetch_url(
    url: str,
    *,
    transport: Transport | None = None,
    max_bytes: int | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    url_check: Callable[[str], str | None] = url_problem,
    before_request: Callable[[], None] | None = None,
    redacted_errors: bool = False,
) -> FetchResult:
    """Fetch one allowlisted URL, bounded and with at most two infra retries."""

    max_bytes = int(max_bytes if max_bytes is not None else RESEARCH_LIMITS["max_response_bytes"])
    retries = int(retries if retries is not None else RESEARCH_LIMITS["max_retries"])
    reason = url_check(url)
    if reason:
        return FetchResult(url=url, status=None, error=f"refused by allowlist: {reason}")
    call: Callable[..., tuple[int, dict[str, str], bytes]] = transport or urllib_transport
    attempts = 0
    last_error: str | None = None
    started = time.monotonic()
    while attempts <= retries:
        attempts += 1
        try:
            if before_request is not None:
                before_request()
            if transport is not None:
                status, headers, body = call(url)
            else:
                status, headers, body = call(url, timeout=timeout)
            truncated = len(body) > max_bytes
            payload = body[:max_bytes]
            result = FetchResult(
                url=url,
                status=int(status),
                body=payload,
                content_type=str(headers.get("content-type", "")),
                sha256=sha256_bytes(payload),
                bytes_read=len(payload),
                attempts=attempts,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                truncated=truncated,
                location=headers.get("location") if 300 <= int(status) < 400 else None,
            )
            if result.status >= 500 and attempts <= retries:
                last_error = f"http {result.status}"
                time.sleep(min(2 ** (attempts - 1), 4))
                continue
            return result
        except urllib.error.HTTPError as exc:
            last_error = f"http {exc.code}"
            if exc.code == 429 or exc.code >= 500:
                if attempts <= retries:
                    time.sleep(min(2 ** (attempts - 1), 4))
                    continue
            return FetchResult(
                url=url,
                status=int(exc.code),
                attempts=attempts,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=last_error,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = "network error" if redacted_errors else f"network error: {exc}"
            if attempts <= retries:
                time.sleep(min(2 ** (attempts - 1), 4))
                continue
        except Exception as exc:  # noqa: BLE001 - transport boundary; record, do not crash the run
            last_error = "transport failure" if redacted_errors else f"transport failure: {exc}"
            break
    return FetchResult(
        url=url,
        status=None,
        attempts=attempts,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        error=last_error or "unknown transport failure",
    )


def scoped_transport(url_check: Callable[[str], str | None], user_agent: str, max_bytes: int, timeout: float) -> Transport:
    """Existing urllib layer, with an explicit roster scope and manual redirects."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    def call(url: str) -> tuple[int, dict[str, str], bytes]:
        if url_check(url):
            raise ValueError("out-of-scope structured request")
        request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
        opener = urllib.request.build_opener(NoRedirect)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            if int(headers.get("content-length", "0")) > max_bytes:
                raise ValueError("structured response too large")
            return int(response.status), headers, response.read(max_bytes + 1)
    return call
