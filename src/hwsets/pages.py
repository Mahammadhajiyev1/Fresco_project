"""Find the pages that hold hardware sets and split them into overlapping chunks."""
from __future__ import annotations

import re

from .codes import classify_token
from .headers import END_OF_SECTION, detect_set_headers
from .schemas import Page, SetHeader

_SET_HEADER = re.compile(
    r"\b(hardware\s+(set|group)|hw[\s\-]*set|set\s*(no\.?|number|#)?\s*[:#]?\s*[A-Z]?\d+[A-Z]?|hw[\s\-]?\d+)\b",
    re.I,
)
_KEYWORDS = re.compile(
    r"\b(hinges?|closers?|lock\s?sets?|latch\s?sets?|exit\s+devices?|panic|kick\s?plates?|"
    r"thresholds?|door\s+stops?|weather\s?strip|gasketing|cylinders?|push\s?plates?|pulls?|"
    r"flush\s+bolts?|overhead\s+stops?|silencers?|catalog|finish|mfr|manufacturer)\b",
    re.I,
)
_END_OF_SECTION = END_OF_SECTION   # single definition lives in headers.py
_SECTION_START = re.compile(r"^SECTION\s+\d{2}\s?\d{2}\s?\d{2}\b")
_WORD = re.compile(r"[A-Za-z]{4,}")

MAX_GAP = 2        # non-kept pages between two kept pages that are always kept (multi-page sets)
LIKE_LINES = 2     # component-like lines that make a page "hardware-like" (extends a region)
ISLAND_LINES = 4   # component-like lines that make an isolated page a candidate on its own
STUB_LINES = 10    # a page with at most this many body lines can be a continuation "stub"


def parse_page_spec(spec: str | None) -> list[int] | None:
    """'40-52,60' -> [40, ..., 52, 60] (1-based); None/'' -> None."""
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        pages.extend(range(int(a), int(b or a) + 1))
    return pages


def _body(page: Page):
    return [l for l in page.lines if not l.furniture and l.clean.strip()]


def score_page(page: Page) -> float:
    """Keyword/code evidence that a page is about door hardware (furniture ignored)."""
    lines = _body(page)
    text = "\n".join(l.clean for l in lines)
    if not text.strip():
        return 0.0
    header_hits = len(_SET_HEADER.findall(text))
    kw_hits = len(_KEYWORDS.findall(text))
    code_hits = sum(
        1 for l in lines for tok in re.split(r"\s+|\|", l.clean)
        if tok and classify_token(tok) in ("mfr", "finish")
    )
    return header_hits * 3 + kw_hits + min(code_hits, 20) * 0.3


def component_lines(page: Page) -> int:
    """Layout evidence: lines shaped like a component row - at least three ' | ' cells, a descriptive word
    and a manufacturer/finish-looking cell. Prose lists ("a. | Provide ...") and door-number lists don't qualify."""
    n = 0
    for l in _body(page):
        cells = [c.strip() for c in l.clean.split(" | ")]
        if (len(cells) >= 3 and any(_WORD.search(c) for c in cells)
                and any(classify_token(c) in ("mfr", "finish") for c in cells)):
            n += 1
    return n


def _ends_section(page: Page) -> bool:
    return any(_END_OF_SECTION.search(l.clean) for l in _body(page))


def _starts_section(page: Page) -> bool:
    return any(_SECTION_START.match(l.clean.strip()) for l in _body(page)[:4])


def find_candidate_pages(
    pages: list[Page],
    *,
    headers: list[SetHeader] | None = None,
    continuation_score: float = 2.0,
    fallback_threshold: float = 6.0,
    fallback_margin: int = 1,
) -> list[int]:
    """Page numbers to send to the model - a hybrid of four kinds of evidence.

    strong        pages holding a detected set header
    layout        pages full of component-shaped rows: grown outward from kept pages (both directions) and also
                  kept on their own when clearly a table (``ISLAND_LINES``), so a header style we do not
                  recognise cannot hide its pages
    continuation  short gaps between kept pages, and following pages that still read as hardware content
    stopping      "END OF SECTION" and new section titles end a region
    With no header and no table-like page at all it falls back to keyword scoring with a page margin, so an
    unknown layout is over- rather than under-selected.
    """
    by_num = {p.number: p for p in pages}
    headers = detect_set_headers(pages) if headers is None else headers
    header_pages = {h.page for h in headers}
    keep = set(header_pages)
    keep.update(p.number for p in pages if component_lines(p) >= ISLAND_LINES)

    if not keep:
        hits = {p.number for p in pages if score_page(p) >= fallback_threshold}
        keep.update(m for n in hits for m in range(n - fallback_margin, n + fallback_margin + 1) if m in by_num)

    def grow(start: int, step: int) -> None:
        """Extend a region from page ``start`` while its neighbours still look like hardware content."""
        m = start + step
        while m in by_num and m not in keep:
            here, there = by_num[m], by_num[m - step]
            boundary = (
                (_ends_section(there) or _starts_section(here)) if step > 0
                else (_ends_section(here) or _starts_section(there))
            )
            if boundary:
                break
            looks_like = component_lines(here) >= LIKE_LINES
            # a set may spill a few lines onto the next page: accept ONE short hardware-flavoured stub page
            # directly after a seed page (never chained - a full prose page mentioning hinges is not a stub)
            follows_on = (
                step > 0 and m == start + 1 and len(_body(here)) <= STUB_LINES
                and score_page(here) >= continuation_score
            )
            if not (looks_like or follows_on):
                break
            keep.add(m)
            m += step

    for n in sorted(keep):
        grow(n, +1)
        grow(n, -1)

    ordered = sorted(keep)
    for a, b in zip(ordered, ordered[1:]):
        gap = range(a + 1, b)
        if 0 < len(gap) <= MAX_GAP and all(g in by_num for g in gap):
            if not any(_ends_section(by_num[g - 1]) or _starts_section(by_num[g]) for g in gap):
                keep.update(gap)
    return sorted(keep)


def context_pages(pages: list[Page], headers: list[SetHeader], selected) -> set[int]:
    """Pages a set may legitimately be carried across: the selected pages plus the automatically detected
    candidates, so a restricted ``--pages`` run still sees the true preceding open set."""
    return set(selected) | set(find_candidate_pages(pages, headers=headers))


def chunk_pages(pages: list[Page], size: int = 4, overlap: int = 1) -> list[list[Page]]:
    """Consecutive-page windows (overlapping) so multi-page sets appear whole in some chunk.

    Non-adjacent pages (gaps in page numbering) never share a chunk.
    """
    if size <= overlap:
        raise ValueError("size must exceed overlap")
    runs: list[list[Page]] = []
    for p in sorted(pages, key=lambda p: p.number):
        if runs and p.number == runs[-1][-1].number + 1:
            runs[-1].append(p)
        else:
            runs.append([p])
    chunks: list[list[Page]] = []
    for run in runs:
        i = 0
        while True:
            chunks.append(run[i:i + size])
            if i + size >= len(run):
                break
            i += size - overlap
    return chunks
