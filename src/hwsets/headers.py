"""Deterministic detection of hardware-set header lines, and of which set is open at a chunk start (no LLM).

A header is a line that contains a set/group keyword plus an identifier, optionally behind outline
numbering, e.g.
    "Hardware Group No. 21A: (Doors ...)"   "Set: EX-1.0"   "Set #AL 01"   "HW 04A Interior Single"
    "PART 79 - HARDWARE GROUP NO. C201EZ"   "3.2 Hardware Set 5"
Prose that merely mentions sets ("Hardware sets are indicated on Drawings", "Group 1 stainless-steel
bolts") is rejected: bare "set"/"group" need an explicit marker (No./#/:), the identifier must contain a
digit, only outline numbering may precede the keyword, and what follows the identifier must not read like a
lowercase sentence continuation.
"""
from __future__ import annotations

import re

from .schemas import ChunkContext, Line, Page, SetHeader

# Identifier (upper-case only, scoped flag, so words such as "the 5" never qualify). Two shapes:
#   * 1-4 letters, one space, digits    -> "AL 01"
#   * one token of letters/digits joined by - . /, at least one digit, up to 16 chars
#       -> "01" "21A" "EX-1.0" "2.5" "C201EZ" "XCY746" "ABCD-1234-XY" "005.1"
_ID = (
    r"(?-i:[A-Z]{1,4}\s\d{1,4}[A-Z]{0,2}"
    r"|(?=[A-Z0-9./\-]*\d)[A-Z0-9]+(?:[-./][A-Z0-9]+)*)"
)
_MARK = r"\s*(?:no\.?|number|#)?\s*[:#]?\s*"
_KEYWORD = (
    r"(?:hardware\s+(?:group|set)(?:\s*/\s*(?:group|set))?"
    r"|door\s+hardware\s+(?:group|set)"
    r"|hw"
    r"|(?:set|group|heading)(?=\s*(?:no\.?|number|#|:)))"
)
# Outline numbering that may precede the keyword: "PART 79 -", "3.2", "3.2.1 |", "A.", "1)".
_LEAD = (
    r"(?:(?:part\s+\d+|\d+(?:\.\d+)*|[A-Za-z])\s*[-–—:.)|]+\s*"
    r"|(?:part\s+\d+|\d+(?:\.\d+)*)\s+)?"
)
_HEADER = re.compile(rf"^{_LEAD}{_KEYWORD}{_MARK}(?P<id>{_ID})(?![A-Za-z0-9])(?P<rest>.*)$", re.I)
# What may follow the identifier: nothing, a separator, or an upper-case/digit/paren word.
_REST_OK = re.compile(r"^(?:$|\s*[-–—:(|,;/]|\s+[A-Z0-9(])")
_NOT_USED = re.compile(r"\b(?:not\s+used|n\s?/\s?a|unused|deleted|void)\b", re.I)
_NOT_USED_LINE = re.compile(r"^\W*(?:not\s+used|n\s?/\s?a|unused|deleted|void)\W*$", re.I)
_MAX_ID = 16
_MAX_LEN = 160


def parse_header(text: str) -> tuple[str, str | None, bool] | None:
    """Return (set_number, description, not_used) if ``text`` is a set-header line, else None."""
    t = re.sub(r"\s+", " ", text.replace("\ufffd", " ")).strip()
    if not t or len(t) > _MAX_LEN:
        return None
    m = _HEADER.match(t)
    if not m or len(m.group("id")) > _MAX_ID:
        return None
    rest = m.group("rest")
    if not _REST_OK.match(rest):
        return None
    desc = re.sub(r"^[\s\-–—:|,;/]+", "", rest).strip() or None
    return re.sub(r"\s+", " ", m.group("id")).strip(), desc, bool(_NOT_USED.search(t))


def is_header_line(line: Line) -> bool:
    return parse_header(line.clean) is not None


def detect_set_headers(pages: list[Page]) -> list[SetHeader]:
    """All set-header lines, in document order, with source page / line id / bbox.

    Furniture lines are ignored. A short "NOT USED"/"N/A" line right under a header also marks it.
    ``text`` is the ORIGINAL line text; detection runs on the model-facing (glyph-free) text.
    """
    out: list[SetHeader] = []
    for page in pages:
        body = [l for l in page.lines if not l.furniture and l.clean.strip()]
        for i, line in enumerate(body):
            parsed = parse_header(line.clean)
            if parsed is None:
                continue
            number, desc, not_used = parsed
            if not not_used and i + 1 < len(body) and _NOT_USED_LINE.match(body[i + 1].clean):
                not_used = True
            out.append(SetHeader(
                set_number=number, description=desc, not_used=not_used,
                text=line.text, page=page.number, line_id=line.id, bbox=line.bbox,
            ))
    return out


def _line_order(line_id: str) -> tuple[int, int]:
    p, n = line_id.split("-")
    return int(p), int(n)


END_OF_SECTION = re.compile(r"\bend\s+of\s+section\b", re.I)


def chunk_context(
    chunk: list[Page],
    headers: list[SetHeader],
    candidate_pages: set[int] | None = None,
    all_pages: dict[int, Page] | None = None,
) -> ChunkContext:
    """Which set is already open when ``chunk`` starts?

    Deterministic, from header order only. The chunk continues a set when its first content line is not
    itself a header and an earlier header exists that has not been closed since: every page up to the chunk
    must be a candidate page (a set is never carried across an unrelated gap) and, when ``all_pages`` is given,
    no "END OF SECTION" line may lie between that header and the chunk. A NOT USED header stays "open" but
    is flagged.
    """
    first_page = chunk[0].number
    body = [l for p in chunk for l in p.lines if not l.furniture and l.clean.strip()]
    if not body:
        return ChunkContext()
    in_chunk = [h for h in headers if chunk[0].number <= h.page <= chunk[-1].number]
    first_header = min(in_chunk, key=lambda h: _line_order(h.line_id), default=None)
    ctx = ChunkContext(continues_until_line_id=first_header.line_id if first_header else None)
    if first_header and first_header.line_id == body[0].id:
        return ctx                                    # chunk opens with a new set: nothing carried over
    earlier = [h for h in headers if h.page < first_page]
    if not earlier:
        return ctx
    prev = max(earlier, key=lambda h: _line_order(h.line_id))
    if candidate_pages is not None and not all(p in candidate_pages for p in range(prev.page, first_page)):
        return ctx                                    # gap of non-candidate pages: not the same set
    if all_pages is not None:
        prev_order = _line_order(prev.line_id)
        for n in range(prev.page, first_page):
            page = all_pages.get(n)
            if page and any(
                END_OF_SECTION.search(l.clean) and _line_order(l.id) > prev_order
                for l in page.lines if not l.furniture
            ):
                return ctx                            # the previous set ended before this chunk starts
    return ctx.model_copy(update=dict(
        is_continuation=True, open_set_id=prev.set_number, open_set_header_text=prev.text,
        open_set_not_used=prev.not_used, header_page=prev.page, header_line_id=prev.line_id,
    ))


class ContextResolver:
    """Recompute the open-set context for ANY chunk (including sub-chunks made by a split/retry).

    Built once per document from the deterministic header list, so a derived chunk never inherits a stale copy
    of its parent's context: its own first line decides whether it starts fresh or inside a set.
    """

    def __init__(self, pages: list[Page], headers: list[SetHeader], candidate_pages: set[int] | None = None):
        self.headers = headers
        self.candidate_pages = candidate_pages
        self.pages = {p.number: p for p in pages}

    def __call__(self, chunk: list[Page]) -> ChunkContext:
        return chunk_context(chunk, self.headers, self.candidate_pages, self.pages)
