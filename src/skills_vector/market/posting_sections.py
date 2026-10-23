"""rules/2 structural section detection; no employer-specific or posting-id rules."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from .posting_volume import _Blocks, BOILERPLATE, normalize_phrase

HEADINGS = {
    "preferred": r"(?:preferred qualifications|nice to have|nice to haves|nonessential skills|optional garnishes)|^(?:preferred|bonus|things we love)",
    "requirements": r"(?:minimum qualifications|\(requirements\))|^(?:requirements|qualifications|what you(?:.ll| will)? (?:bring|need)|what we(?: look|.re looking) for|who you are|about you|skills and experience|you have|we.re looking for someone|the secret ingredients)",
    "responsibilities": r"\(responsibilities\)|^(?:responsibilities|your responsibilities|what you(?:.ll| will)? (?:do|achieve|work on|be doing|build|deliver)|(?:the )?impact you(?:.ll| will)? have|your impact|the role|about the role|duties|job description|a day in the life|in particular,? you will be|in this role(?:,? you(?:.ll| will))?|what you.ll cook up)",
    "benefits": r"^(?:benefits|perks|what we(?:.ll| will)? offer|compensation|salary|pay range|local pay range|total targeted cash|our total rewards|why (?:join|work)|people are the secret)",
    "company": r"^(?:about\b|who we are|our company|our values|more about|AI at\b|our approach|how .*uses AI|why this role|we.re transforming|.* is a flex first team)",
    "legal": r"^(?:equal (?:employment |)opportunity|legal|privacy|additional information|accommodations|diversity|our commitment|compliance|note to applicants|location|travel)",
}
EXCLUDE = re.compile(BOILERPLATE.pattern + r"|\b(?:will not sponsor|work visas|job application and interview process|how .* uses AI in.*hiring|pay range transparency|in-office culture|encourage you to apply|not everyone|relocation support|role requires regular travel)\b", re.I)


class StructuralBlocks(_Blocks):
    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        values = dict(attrs)
        if not self.in_list and not "".join(self.parts).strip() and (
            tag in ("strong", "b") or "font-weight: 700" in values.get("style", "") or "font-weight: bold" in values.get("style", "")
        ):
            self.kind = "heading"


def extract(description: str, *, is_html: bool = True) -> dict:
    if is_html:
        parser = StructuralBlocks()
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
        name = " ".join(value.strip().rstrip(":").casefold().split())
        matched = next((key for key, pattern in HEADINGS.items() if re.search(pattern, name)), None)
        short_header = (kind != "bullet" or value.rstrip().endswith(":")) and len(name) <= 130
        if matched and short_header:
            section, heading = matched, value
            continue
        if kind == "heading":
            section, heading = "unknown", value
            continue
        if len(name) <= 90 and value.rstrip().endswith(":"):
            heading = value
            continue
        # Explicit current-duty prose without a heading is evidence; bare lists are not guessed.
        current_duty = kind == "paragraph" and bool(re.match(r"^(?:in this role,? )?you (?:will|will be responsible for)\b", name))
        actual = "responsibilities" if current_duty and section == "unknown" else section
        if actual not in ("responsibilities", "requirements", "preferred") or EXCLUDE.search(value):
            continue
        if actual == "responsibilities" and kind == "paragraph":
            explicit = re.search(r"\byou(?:.ll| will)\b", name) or re.match(r"^(?:independently )?(?:own|manage|build|lead|deliver|develop|coordinate|prepare|conduct|analy[sz]e|support|maintain|drive|work|identify|create|source|negotiate|partner|provide|execute|use|track|sell|ensure|exceed)\b", name)
            if not explicit:
                continue
        if not normalize_phrase(value):
            continue
        # Some board HTML glues a trailing heading to an introductory paragraph.
        value = re.sub(r"(?<=\.)Responsibilities\s*$", "", value, flags=re.I).rstrip()
        entry = {"quote": value, "start": start, "end": start + len(value), "section": actual, "heading": heading,
                 "source_sha256": sha, "dimension": "task" if actual == "responsibilities" else "capability",
                 "basis": "employer_preference" if actual == "preferred" else "advertised_demand" if actual == "responsibilities" else "employer_requirement"}
        expectations.append(entry); sections[actual].append(value)
    return {"description": text, "source_sha256": sha, "expectations": expectations,
            "sections": dict(sections), "has_responsibilities": bool(sections.get("responsibilities"))}
