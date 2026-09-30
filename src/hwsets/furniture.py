"""Conservative detection of repeated page furniture (running headers / footers / page numbers).

A line is furniture only if ALL hold:
  * it sits in the top or bottom margin band of its page,
  * the same text (digits normalised, so "Page 3" == "Page 4") recurs at the same vertical position
    (within ``y_tol`` points) on at least ``min_pages`` different pages,
  * it is not a set header or a component column-header row,
  * it is structurally a running header/footer: the furniture lines of a page form an unbroken run at the very
    TOP (or very BOTTOM) of the page. A line that sits below a content line - e.g. "For use on Door #(s):" directly
    under a group header that happens to start every page - is content, however often it repeats.
Lines are never deleted or renumbered - they are only flagged (``Line.furniture``).
"""
from __future__ import annotations

import re
from collections import defaultdict

from .headers import is_header_line
from .schemas import Line, Page

_PUA = re.compile("[\ue000-\uf8ff]")
_COLUMN_HEADER = re.compile(r"\bqty\b.*\bdesc", re.I)


def _key(text: str) -> str:
    t = _PUA.sub("", text).lower()
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", t)).strip()


def _in_margin(line: Line, page: Page, top: float, bottom: float) -> bool:
    cy = (line.bbox.y0 + line.bbox.y1) / 2
    return cy < top * page.height or cy > bottom * page.height


def _edge_runs(page: Page, candidates: set[str], top: float = 0.15, bottom: float = 0.90) -> set[str]:
    """Candidate ids that form an unbroken run at the top or bottom of the page. The run from the top may only
    use lines in the top margin band, the run from the bottom only lines in the bottom band (lines with no
    visible text are transparent). Anything else is content and must not be hidden."""
    visible = [l for l in page.lines if l.clean.strip()]

    def cy(l: Line) -> float:
        return (l.bbox.y0 + l.bbox.y1) / 2

    keep: set[str] = set()
    for seq, in_band in (
        (visible, lambda l: cy(l) < top * page.height),
        (visible[::-1], lambda l: cy(l) > bottom * page.height),
    ):
        for line in seq:
            if line.id not in candidates or not in_band(line):
                break
            keep.add(line.id)
    return keep


def mark_furniture(
    pages: list[Page], *, min_pages: int = 3, top: float = 0.15, bottom: float = 0.90, y_tol: float = 3.0
) -> int:
    """Flag repeated running headers/footers in place. Returns the number of lines flagged."""
    groups: dict[str, list[tuple[float, int, Line]]] = defaultdict(list)
    for page in pages:
        for line in page.lines:
            if not _in_margin(line, page, top, bottom):
                continue
            k = _key(line.text)
            if not k or is_header_line(line) or _COLUMN_HEADER.search(line.text):
                continue
            groups[k].append((line.bbox.y0, page.number, line))

    candidates: set[str] = set()
    for entries in groups.values():
        if len({pn for _, pn, _ in entries}) < min_pages:
            continue
        entries.sort(key=lambda e: e[0])
        cluster = [entries[0]]
        for e in entries[1:] + [None]:
            if e is not None and e[0] - cluster[-1][0] <= y_tol:
                cluster.append(e)
                continue
            if len({pn for _, pn, _ in cluster}) >= min_pages:
                candidates.update(line.id for _, _, line in cluster)
            cluster = [e] if e is not None else []

    flagged = 0
    for page in pages:
        page_candidates = {l.id for l in page.lines if l.id in candidates}
        if not page_candidates:
            continue
        edge = _edge_runs(page, page_candidates, top, bottom)
        for line in page.lines:
            if line.id in edge and not line.furniture:
                line.furniture = True
                flagged += 1
    return flagged
