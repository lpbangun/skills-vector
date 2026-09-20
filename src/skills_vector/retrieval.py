"""Ordinary-code retrieval: fetch, hash body, record provenance, never invent evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .ids import content_hash, stable_id
from .occupational import (
    PARSER_VERSION,
    EvidenceWeight,
    Passage,
    SourceKind,
    SourceRecord,
)


class SilentFixtureFallback(RuntimeError):
    """Production collection refused to substitute synthetic evidence."""


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    url: str
    body: str
    content_hash: str
    retrieved_at: datetime
    status: str
    failure_note: str = ""


class Retriever:
    def __init__(
        self,
        fixture_root: Path,
        allow_fixtures: bool,
        *,
        timeout_seconds: int = 20,
        opener=None,
    ) -> None:
        self.fixture_root = fixture_root
        self.allow_fixtures = allow_fixtures
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def retrieve(self, url: str) -> RetrievedDocument:
        if url.startswith("fixture://"):
            if not self.allow_fixtures:
                raise SilentFixtureFallback(
                    "production research refuses unlabeled synthetic fallback; "
                    "pass allow_fixtures only for the offline fixture path"
                )
            return self._read_fixture(url)
        try:
            request = Request(url, headers={"User-Agent": "skills-vector-research/0.2"})
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
            body = raw.decode("utf-8", errors="replace")
            return RetrievedDocument(url, body, content_hash(raw), datetime.now(UTC), "ok")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            return RetrievedDocument(
                url,
                "",
                content_hash(url),
                datetime.now(UTC),
                "failed",
                failure_note=str(exc),
            )

    def _read_fixture(self, url: str) -> RetrievedDocument:
        name = url.removeprefix("fixture://sources/")
        path = self.fixture_root / name
        body = path.read_text(encoding="utf-8")
        data = json.loads(body)
        if data.get("kind") != "offline_fixture":
            raise SilentFixtureFallback(f"fixture file is not labeled offline_fixture: {path}")
        return RetrievedDocument(url, body, content_hash(body), datetime.now(UTC), "ok")


def source_from_document(
    document: RetrievedDocument,
    *,
    kind: SourceKind,
    weight: EvidenceWeight,
    title: str,
    publisher: str,
    published_on=None,
    rights: str,
) -> SourceRecord:
    fixture = document.url.startswith("fixture://") or kind is SourceKind.OFFLINE_FIXTURE
    return SourceRecord(
        source_id=stable_id("src", document.content_hash, document.url),
        kind=kind,
        weight=weight,
        title=title if "[offline fixture]" in title or not fixture else f"{title} [offline fixture]",
        publisher=publisher,
        url=document.url,
        retrieved_at=document.retrieved_at,
        content_hash=document.content_hash,
        parser_version=PARSER_VERSION,
        published_on=published_on,
        rights=rights,
        fixture=fixture,
        retrieval_ok=document.status == "ok",
        failure_note=document.failure_note,
    )


def passages_from_document(source: SourceRecord, document: RetrievedDocument, *, max_chars: int = 1200) -> tuple[Passage, ...]:
    if not document.body or document.status != "ok":
        return ()
    try:
        payload = json.loads(document.body)
    except json.JSONDecodeError:
        text = document.body.strip()
        chunks = _chunk(text, max_chars)
        return tuple(
            Passage(stable_id("psg", source.source_id, str(index)), source.source_id, f"char:{index}", chunk)
            for index, chunk in enumerate(chunks)
        )
    excerpts = payload.get("excerpts") or payload.get("passages") or []
    if not excerpts and payload.get("text"):
        excerpts = [{"locator": "body", "text": payload["text"]}]
    built: list[Passage] = []
    for index, excerpt in enumerate(excerpts):
        text = excerpt["text"] if isinstance(excerpt, dict) else str(excerpt)
        locator = excerpt.get("locator", f"excerpt:{index}") if isinstance(excerpt, dict) else f"excerpt:{index}"
        built.append(Passage(stable_id("psg", source.source_id, locator, text), source.source_id, locator, text[:max_chars]))
    return tuple(built)


def _chunk(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text] if text else []
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
