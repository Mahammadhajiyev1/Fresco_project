"""PDF -> pages of visual lines with bounding boxes (PyMuPDF)."""
from __future__ import annotations

from pathlib import Path
from statistics import median

import pymupdf

from .furniture import mark_furniture
from .schemas import BBox, Line, Page, Word
from .textnorm import normalize_for_model

GAP_MARK = " | "
# Horizontal gap (in multiples of the line's median word height) treated as a column break.
GAP_FACTOR = 1.2


def _cluster_rows(words: list[Word]) -> list[list[Word]]:
    """Group words into visual rows by vertical overlap of their y-centres."""
    words = sorted(words, key=lambda w: ((w.bbox.y0 + w.bbox.y1) / 2, w.bbox.x0))
    rows: list[list[Word]] = []
    for w in words:
        cy = (w.bbox.y0 + w.bbox.y1) / 2
        if rows:
            last = rows[-1]
            ref = last[0].bbox
            tol = 0.5 * (ref.y1 - ref.y0)
            if abs(cy - (ref.y0 + ref.y1) / 2) <= max(tol, 2.0):
                last.append(w)
                continue
        rows.append([w])
    return rows


def _row_text(row: list[Word]) -> str:
    row = sorted(row, key=lambda w: w.bbox.x0)
    h = median(w.bbox.y1 - w.bbox.y0 for w in row) or 8.0
    parts = [row[0].text]
    for prev, cur in zip(row, row[1:]):
        parts.append(GAP_MARK if cur.bbox.x0 - prev.bbox.x1 > GAP_FACTOR * h else " ")
        parts.append(cur.text)
    return "".join(parts)


def page_from_pymupdf(pg: pymupdf.Page) -> Page:
    number = pg.number + 1
    words = [
        Word(text=w[4], bbox=BBox(x0=w[0], y0=w[1], x1=w[2], y1=w[3]))
        for w in pg.get_text("words")
        if w[4].strip()
    ]
    lines: list[Line] = []
    for i, row in enumerate(_cluster_rows(words), start=1):
        row = sorted(row, key=lambda w: w.bbox.x0)
        box = row[0].bbox
        for w in row[1:]:
            box = box.union(w.bbox)
        text = _row_text(row)
        norm = normalize_for_model(text)
        lines.append(Line(
            id=f"{number}-{i}", page=number, text=text, model_text=None if norm == text else norm,
            bbox=box, words=row,
        ))
    return Page(number=number, width=pg.rect.width, height=pg.rect.height, lines=lines)


def load_pages(path: str | Path | bytes, *, page_numbers: list[int] | None = None) -> list[Page]:
    """Read pages (1-based ``page_numbers`` to restrict). Accepts a path or raw PDF bytes.

    Repeated running headers/footers are detected over the WHOLE document (so a restricted read gets the
    same flags) and marked with ``Line.furniture``; line ids and coordinates are unaffected.
    """
    doc = pymupdf.open(stream=path, filetype="pdf") if isinstance(path, bytes) else pymupdf.open(path)
    try:
        pages = [page_from_pymupdf(doc[i]) for i in range(doc.page_count)]
    finally:
        doc.close()
    mark_furniture(pages)
    if page_numbers:
        wanted = set(page_numbers)
        pages = [p for p in pages if p.number in wanted]
    return pages


def render_page_png(path: str | Path | bytes, page_number: int, zoom: float = 1.5) -> bytes:
    doc = pymupdf.open(stream=path, filetype="pdf") if isinstance(path, bytes) else pymupdf.open(path)
    try:
        if not 1 <= page_number <= doc.page_count:
            raise IndexError(f"page {page_number} out of range (1-{doc.page_count})")
        return doc[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    finally:
        doc.close()


def format_lines(pages: list[Page]) -> str:
    """Model-facing text: one '[id] text' per line (normalised, furniture hidden), with page separators."""
    out: list[str] = []
    for p in pages:
        out.append(f"=== PAGE {p.number} ===")
        out.extend(f"[{l.id}] {l.clean}" for l in p.lines if not l.furniture and l.clean.strip())
    return "\n".join(out)
