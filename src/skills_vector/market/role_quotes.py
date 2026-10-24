"""Source-bound sentence-size projection spans; raw snapshots are never rewritten."""
from __future__ import annotations

import hashlib
import re

METHOD = "sentence-spans/1"
# Avoid splitting common internal abbreviations; every returned range is contiguous.
ABBREVIATIONS = {"e.g.", "i.e.", "u.s.", "u.k.", "mr.", "ms.", "dr.", "sr.", "jr.", "vs."}


def ranges(text: str) -> list[tuple[int, int]]:
    boundaries = []
    for hit in re.finditer(r'[.!?](?:["”’)]*)\s+(?=\S)', text):
        last = text[:hit.start() + 1].rsplit(None, 1)[-1].lower().lstrip("([{\"\'“")
        if last in ABBREVIATIONS:
            continue
        boundaries.append(hit.start() + len(hit[0].rstrip()))
    starts, spans = 0, []
    for stop in [*boundaries, len(text)]:
        a, b = starts, stop
        while a < b and text[a].isspace(): a += 1
        while b > a and text[b - 1].isspace(): b -= 1
        if a < b: spans.append((a, b))
        starts = stop
    return spans


def omit_contact_sentences(expectations: list[dict], description: str) -> tuple[list[dict], list[dict]]:
    """Omit unsafe sentences, never whole descriptions or surrounding clean duties."""
    from .structured_career import CONTACT

    output, omitted = [], []
    for expected in expectations:
        key = "source_wording" if "source_wording" in expected else "quote"
        original = expected[key]
        if description[expected["start"]:expected["end"]] != original:
            raise ValueError("contact filter input is not a source-bound span")
        spans = ranges(original)
        if not any(CONTACT.search(original[a:b]) for a, b in spans):
            output.append(expected)
            continue
        for a, b in spans:
            quote = original[a:b]
            start = expected["start"] + a
            if CONTACT.search(quote):
                omitted.append({"start": start, "end": start + len(quote),
                                "source_sha256": expected["source_sha256"],
                                "quote_sha256": hashlib.sha256(quote.encode()).hexdigest()})
            else:
                output.append({**expected, key: quote, "start": start, "end": start + len(quote)})
    return output, omitted


def project(posting: dict, description: str) -> list[dict]:
    output, excluded = [], []
    expectations, contact_omissions = omit_contact_sentences(posting["expectations"], description)
    posting["contact_spans_omitted"] = posting.get("contact_spans_omitted", 0) + len(contact_omissions)
    posting["contact_omissions"] = posting.get("contact_omissions", []) + contact_omissions
    for expected in expectations:
        original = expected["source_wording"]
        if description[expected["start"]:expected["end"]] != original:
            raise ValueError("sentence projection input is not a source-bound span")
        for a, b in ranges(original):
            quote = original[a:b]
            start = expected["start"] + a
            if description[start:start + len(quote)] != quote:
                raise ValueError("sentence quote is not an exact source substring")
            if notice(quote):
                excluded.append({"quote": quote, "start": start, "end": start + len(quote), "source_sha256": expected["source_sha256"], "reason": "standalone pay/location/benefit notice, not an expectation"})
                continue
            identity = posting["source_id"] + "|" + str(start) + "|" + quote
            output.append({**expected, "source_wording": quote, "start": start, "end": start + len(quote),
                "expectation_id": "exp.sentence." + hashlib.sha256(identity.encode()).hexdigest()[:24],
                "parent_expectation_id": expected["expectation_id"], "parent_quote_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "projection_extraction": METHOD})
    posting["projection_exclusions"] = excluded
    return output


def notice(quote: str) -> bool:
    """Narrow publication quarantine, not a semantic skill classifier."""
    text = quote.strip()
    return bool(re.fullmatch(r"(?:WA|CA|NY|TX|US|USA|[$£€][\d,]+(?:[—–-][$£€]?[\d,]+)?\s*(?:USD|CAD)?)", text, re.I)
                or re.search(r"^(?:For US based candidates,? the base pay|Please (?:review|read).*?(?:benefits|remote work policy|flex first)|Additionally,? this role is eligible for a new hire equity|This role is remote and the base pay)", text, re.I)
                or re.search(r"\bmarket-competitive compensation and benefits\b", text, re.I))
