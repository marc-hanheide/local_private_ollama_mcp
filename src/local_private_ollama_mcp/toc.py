"""Table of contents with character ranges, without using the LLM.

Strategies, in order:
1. markdown  - ATX headings (`# Title`) in the (converted) markdown, ignoring code fences
2. pdf_outline - the PDF's bookmark outline, located in the converted text
3. heuristic - numbered headings ("2.1 Methods") and common unnumbered section titles
4. none      - no structure found

Each section covers [start, end) in characters of the document text that all tools
work on, ending where the next heading of the same or a higher level begins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

TocMethod = Literal["markdown", "pdf_outline", "heuristic", "none"]

ATX_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
NUMBERED_RE = re.compile(r"^((?:\d{1,2}\.){0,3}\d{1,2})\.?[ \t]+([A-Z][^\n]{1,100})$")
KNOWN_HEADINGS = {
    "abstract", "summary", "executive summary", "introduction", "background", "related work",
    "literature review", "state of the art", "methods", "methodology", "approach", "materials and methods",
    "experiments", "evaluation", "results", "discussion", "conclusion", "conclusions",
    "future work", "acknowledgements", "acknowledgments", "references", "bibliography",
    "works cited", "literature cited", "appendix", "appendices", "objectives", "aims and objectives",
    "work plan", "workplan", "impact", "risks", "risk management", "budget", "justification of resources",
    "management", "project management", "data management", "ethics", "case for support",
}


@dataclass
class Heading:
    level: int
    title: str
    start: int


@dataclass
class TocSection:
    index: int
    level: int
    title: str
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


def _line_starts(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for line in text.splitlines(keepends=True):
        out.append((pos, line.rstrip("\r\n")))
        pos += len(line)
    return out


def markdown_headings(text: str) -> list[Heading]:
    headings, in_fence = [], False
    for pos, line in _line_starts(text):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = ATX_RE.match(line)
        if m and m.group(2).strip():
            headings.append(Heading(len(m.group(1)), m.group(2).strip(), pos))
    return headings


def outline_headings(text: str, outline: list[tuple[int, str]]) -> list[Heading]:
    """Locate each bookmark title in order; titles that can't be found are skipped."""
    headings, pos = [], 0
    for level, title in outline:
        words = [re.escape(w) for w in title.split()]
        if not words:
            continue
        m = re.compile(r"\s+".join(words), re.IGNORECASE).search(text, pos)
        if m is None:
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        headings.append(Heading(max(1, level), title, line_start))
        pos = m.end()
    return headings


REFERENCE_LIST_HEADINGS = {
    "references", "bibliography", "works cited", "literature cited", "reference list", "list of references",
}
AFTER_REFERENCES_HEADINGS = {"appendix", "appendices", "supplementary material", "supplementary information"}
# Signs that a numbered line is a reference entry, affiliation or sentence, not a heading.
YEAR_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
CITATION_RE = re.compile(r"\[\d+(?:[,–-]\s*\d+)*\]")


def _looks_like_heading_title(title: str) -> bool:
    if title.endswith((".", ",", ";", ":", "-")) or len(title.split()) > 12:
        return False  # sentences, list items and wrapped lines
    if any(q in title for q in "“”\"«»"):
        return False  # quoted titles: reference entries
    if title.count(",") >= 2 or "et al" in title.lower() or YEAR_RE.search(title):
        return False  # author lists, affiliations, reference entries
    if CITATION_RE.search(title):
        return False  # body text with citation markers
    if any(len(w) > 25 for w in title.split()):
        return False  # PDF text with lost spaces ("Thepresentstudyassumes...")
    return True


def _running_headers(lines: list[tuple[int, str]]) -> set[str]:
    """Lines repeated on many pages, like "12 R. Soni and C. Fox", ignoring page numbers."""
    counts: dict[str, int] = {}
    for _, raw in lines:
        key = re.sub(r"^\d+\s+|\s+\d+$", "", raw.strip()).lower()
        if key and len(key) <= 80:
            counts[key] = counts.get(key, 0) + 1
    return {k for k, n in counts.items() if n >= 3}


def heuristic_headings(text: str) -> list[Heading]:
    lines = _line_starts(text)
    headers = _running_headers(lines)
    headings: list[Heading] = []
    in_references = False
    for pos, raw in lines:
        line = raw.strip()
        if not line or len(line) > 110:
            continue
        bare = line.rstrip(":").strip()
        bare_l = re.sub(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+", "", bare).lower()
        if in_references and bare_l not in AFTER_REFERENCES_HEADINGS:
            continue  # numbered entries in a reference list are not headings
        if re.sub(r"^\d+\s+|\s+\d+$", "", line).lower() in headers:
            continue
        m = NUMBERED_RE.match(line)
        if m:
            title = m.group(2).strip()
            if not _looks_like_heading_title(title):
                continue
            level = m.group(1).count(".") + 1
            headings.append(Heading(level, f"{m.group(1)} {title}", pos))
        elif bare.lower() in KNOWN_HEADINGS:
            headings.append(Heading(1, bare, pos))
        else:
            continue
        in_references = bare_l in REFERENCE_LIST_HEADINGS
    return headings


def _to_sections(headings: list[Heading], text_len: int) -> list[TocSection]:
    headings = sorted(headings, key=lambda h: h.start)
    # Drop duplicates at the same position (e.g. two bookmarks matched to one line).
    dedup: list[Heading] = []
    for h in headings:
        if not dedup or h.start != dedup[-1].start:
            dedup.append(h)
    sections = []
    for i, h in enumerate(dedup):
        end = text_len
        for nxt in dedup[i + 1:]:
            if nxt.level <= h.level:
                end = nxt.start
                break
        sections.append(TocSection(i, h.level, h.title, h.start, end))
    return sections


def build_toc(text: str, outline: list[tuple[int, str]] | None = None) -> tuple[TocMethod, list[TocSection]]:
    if hs := markdown_headings(text):
        return "markdown", _to_sections(hs, len(text))
    if outline and (hs := outline_headings(text, outline)):
        return "pdf_outline", _to_sections(hs, len(text))
    if hs := heuristic_headings(text):
        return "heuristic", _to_sections(hs, len(text))
    return "none", []
