"""flat-headings/1: conservative clause-boundary headings in one-line descriptions.

No decoding, whitespace normalization or source rewriting: offsets bind the
original saved description. Marked-up and multiline inputs use rules/2 instead.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import re

from .role_quotes import ranges

METHOD = "flat-headings/1"
# Reject inline markup too: fail closed rather than reinterpret any HTML as flat text.
MARKUP = re.compile(r"</?[A-Za-z][^<>]*>")
LINE_BREAK = re.compile(r"[\r\n\v\f\x85\u2028\u2029]")
# Literal case variants, not case-insensitive prose matching or employer rules.
HEADINGS = {
    "responsibilities": (
        "Responsibilities", "Key Responsibilities", "Key responsibilities", "Core Responsibilities",
        "Your Responsibilities", "Roles and Responsibilities", "Role &amp; Responsibilities",
        "Primary Job Duties &amp; Responsibilities", "Principal Duties and Responsibilities",
        "ESSENTIAL DUTIES AND RESPONSIBILITIES", "Responsible for:",
        "What you'll do", "What You'll Do", "What you’ll do", "What You’ll Do",
        "What Will You Do?", "Your Impact", "About the role", "About the Role", "About this role",
        "How you will make an impact:", "How You'll Spend Your Time", "What does the day-to-day look like?",
    ),
    "requirements": (
        "Required qualifications", "Required Qualifications", "Basic Qualifications", "Minimum Qualifications",
        "What you'll bring", "What You'll Bring", "What you’ll bring", "What You’ll Bring",
        "Qualifications", "Key Qualifications", "About You", "What is a Must Have?", "Job Specifications",
    ),
    "preferred": (
        "Preferred qualifications", "Preferred Qualifications", "Other Qualifications", "Preferred Skills/Experience",
        "What Will Our Ideal Candidate Have?", "Preferred Capabilities, Skills and Experiences:",
        "Qualities that will help you thrive in this role are:", "What Skills &amp; Qualifications",
    ),
    "company": ("About Our Company", "About Us", "About the Team", "How We Work", "Our Promise"),
    "benefits": ("Benefits", "Compensation", "Compensation &amp; Benefits", "Compensation and Benefits",
                 "Pay", "Pay Range", "Base Pay Salary", "Salary Range", "What Is in It for You?"),
    "legal": ("Equal Opportunity", "Equal Employment Opportunity", "Accommodations", "Pay Transparency",
              "Pay Transparency Statement",
              "The minimum and maximum full-time annual salaries for this role are listed below, by location.",
              "The expected base pay range", "The annual base salary range"),
}
LOOKUP = {heading: section for section, headings in HEADINGS.items() for heading in headings}
PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(h) for h in sorted(LOOKUP, key=len, reverse=True)) + r")(?!\w)")
TITLE_LINKS = {"a", "an", "and", "as", "at", "for", "in", "of", "or", "the", "to", "with"}


def eligible(description: str) -> bool:
    return bool(description.strip()) and not MARKUP.search(description) and not LINE_BREAK.search(description)


def clause_start(text: str, position: int) -> bool:
    before = text[:position].rstrip()
    if not before or re.search(r'[.!?][\"”’)]*$', before):
        return True
    # A flattened title can itself follow prose (e.g. '... policy Workday Pay').
    # Only the immediately adjacent title-cased run qualifies, never arbitrary
    # lower-case prose. Sentence-prefix matching alone misses that structure.
    words = re.findall(r"[\w’'-]+", re.split(r"[.!?]", before)[-1])
    run = []
    for word in reversed(words):
        if not (word[0].isupper() or word[0].isdigit() or word in TITLE_LINKS):
            break
        run.append(word)
    return bool(run) and (run[0][0].isupper() or run[0][0].isdigit()) and len(run) <= 16 and len(" ".join(run)) <= 120


def capitalized_after(text: str, position: int) -> bool:
    following = text[position:].lstrip(" :;–—-•●·*\"“'(")
    return bool(following) and following[0].isupper()


def extract(description: str, *, employer: str | None = None) -> dict:
    from .posting_sections import EXCLUDE

    if not eligible(description):
        raise ValueError("flat-headings/1 requires a one-line description without block tags")
    matches = [(hit.start(), hit.end(), hit[0], LOOKUP[hit[0]]) for hit in PATTERN.finditer(description)]
    if employer:
        # Literal reviewed roster employer only, never a broad 'About ...' wildcard.
        for hit in re.finditer(r"(?<!\w)" + re.escape("About " + employer) + r"(?!\w)", description):
            matches.append((hit.start(), hit.end(), hit[0], "company"))
    boundaries = []
    for start, end, heading, section in sorted(matches):
        if clause_start(description, start) and capitalized_after(description, end):
            if boundaries and start < boundaries[-1][1]:
                continue
            boundaries.append((start, end, heading, section))
    sha = hashlib.sha256(description.encode()).hexdigest()
    expectations, sections, detected = [], defaultdict(list), []
    for index, (start, end, heading, section) in enumerate(boundaries):
        stop = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(description)
        content_start = end
        while content_start < stop and description[content_start] in " :;–—-•●·*":
            content_start += 1
        detected.append({"heading": heading, "section": section, "start": start,
                         "content_start": content_start, "end": stop})
        if section not in ("responsibilities", "requirements", "preferred"):
            continue
        for a, b in ranges(description[content_start:stop]):
            quote = description[content_start + a:content_start + b]
            if EXCLUDE.search(quote):
                continue
            entry = {"quote": quote, "start": content_start + a, "end": content_start + b,
                     "section": section, "heading": heading, "source_sha256": sha,
                     "dimension": "task" if section == "responsibilities" else "capability",
                     "basis": "employer_preference" if section == "preferred" else
                              "advertised_demand" if section == "responsibilities" else "employer_requirement"}
            expectations.append(entry)
            sections[section].append(quote)
    return {"description": description, "source_sha256": sha, "expectations": expectations,
            "sections": dict(sections), "has_responsibilities": bool(sections.get("responsibilities")),
            "extraction_method": METHOD, "section_boundaries": detected}
