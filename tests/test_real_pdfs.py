"""Regression tests on the real sample PDFs in data/ (skipped when the files are absent). Local only."""
from pathlib import Path

import pymupdf
import pytest

from hwsets.headers import detect_set_headers
from hwsets.ingest import load_pages, page_from_pymupdf
from hwsets.mock import MockExtractor
from hwsets.pages import find_candidate_pages
from hwsets.pipeline import run_pipeline

DATA = Path(__file__).parents[1] / "data"
# (file, manually verified hardware-set page range, expected headers, expected NOT USED)
DOCS = {
    "valor": ("087100-DOOR-HARDWARE_Rev_2.pdf", (7, 18), 37, 0),
    "ryan": ("087100 - Door Hardware-6.pdf", (24, 46), 38, 0),
    "lyons": ("Project Manual (1).pdf", (285, 294), 29, 4),
}
pytestmark = pytest.mark.skipif(not all((DATA / d[0]).exists() for d in DOCS.values()),
                                reason="real sample PDFs not in data/")


@pytest.fixture(scope="module", params=list(DOCS))
def doc(request):
    f, rng, n_headers, n_nu = DOCS[request.param]
    pages = load_pages(DATA / f)
    return DATA / f, rng, n_headers, n_nu, pages, detect_set_headers(pages)


def test_header_counts(doc):
    _, rng, n_headers, n_nu, _, hs = doc
    assert len(hs) == n_headers and len({h.set_number for h in hs}) == n_headers
    assert sum(h.not_used for h in hs) == n_nu
    assert all(rng[0] <= h.page <= rng[1] for h in hs)             # no header outside the verified range


def test_header_ids_cover_structural_variants():
    valor = {h.set_number for h in detect_set_headers(load_pages(DATA / DOCS["valor"][0]))}
    assert {"21A", "21B"} <= valor
    ryan = {h.set_number for h in detect_set_headers(load_pages(DATA / DOCS["ryan"][0]))}
    assert {"EX-1.0", "2.5", "28.0"} <= ryan


def test_candidate_recall_is_complete_and_precise(doc):
    _, rng, _, _, pages, hs = doc
    cand = set(find_candidate_pages(pages, headers=hs))
    known = set(range(rng[0], rng[1] + 1))
    assert known <= cand                                            # 100% recall, incl. continuation page
    assert len(cand - known) <= 2                                   # and (almost) nothing extra


def test_ryan_ex2_footer_does_not_stretch_location():
    pages = load_pages(DATA / DOCS["ryan"][0])
    footers = {l.id: l for p in pages for l in p.lines if l.furniture}
    assert any("DOOR HARDWARE" in l.text and l.page == 24 for l in footers.values())
    res = run_pipeline(DATA / DOCS["ryan"][0], extractor=MockExtractor(), workers=1)
    ex2 = next(s for s in res.sets if s.set_number == "EX-2.0")
    page24 = next(p for p in pages if p.number == 24)
    footer_top = min(l.bbox.y0 for l in footers.values() if l.page == 24 and l.bbox.y0 > page24.height / 2)
    p24 = next(loc for loc in ex2.locations if loc.page == 24)
    assert p24.bbox.y1 < footer_top
    assert not set(footers) & {i for loc in ex2.locations for i in loc.line_ids}


def test_ids_and_bboxes_match_unfiltered_parse(doc):
    path, rng, _, _, pages, _ = doc
    raw = pymupdf.open(path)
    for p in (q for q in pages if q.number in (rng[0], rng[0] + 1, rng[1])):
        plain = page_from_pymupdf(raw[p.number - 1])
        assert [(l.id, l.text, l.bbox) for l in p.lines] == [(l.id, l.text, l.bbox) for l in plain.lines]


def test_no_hardware_content_is_mistaken_for_page_furniture():
    """Lyons has no running header/footer in its hardware pages: nothing there may be hidden (regression: the
    'For use on Door #(s):' label under each group header used to be flagged on 9 of 10 pages)."""
    lyons = [p for p in load_pages(DATA / DOCS["lyons"][0]) if 285 <= p.number <= 294]
    assert not any(l.furniture for p in lyons for l in p.lines)
    labels = [l for p in lyons for l in p.lines if l.clean == "For use on Door #(s):"]
    assert len(labels) >= 20
    for name in ("valor", "ryan"):                          # these DO have real furniture: only that is hidden
        lo, hi = DOCS[name][1]
        texts = {l.clean.split(" | ")[0].lower()[:12] for p in load_pages(DATA / DOCS[name][0])
                 if lo <= p.number <= hi for l in p.lines if l.furniture}
        assert not any(t.startswith(("set:", "hardware gro", "qty", "each to have")) for t in texts)
