"""Dry-run / continuation / glyph checks on the real sample PDFs in data/ (skipped when absent). No API."""
import re
from pathlib import Path

import pytest

from hwsets.dryrun import build_dry_run
from hwsets.headers import chunk_context, detect_set_headers, parse_header
from hwsets.ingest import load_pages
from hwsets.pages import find_candidate_pages

DATA = Path(__file__).parents[1] / "data"
VALOR = DATA / "087100-DOOR-HARDWARE_Rev_2.pdf"
RYAN = DATA / "087100 - Door Hardware-6.pdf"
LYONS = DATA / "Project Manual (1).pdf"
pytestmark = pytest.mark.skipif(not all(p.exists() for p in (VALOR, RYAN, LYONS)),
                                reason="real sample PDFs not in data/")
PUA = re.compile("[\ue000-\uf8ff]")


@pytest.fixture(scope="module")
def valor():
    pages = load_pages(VALOR)
    return pages, detect_set_headers(pages)


def _expected_open_set(pages, page_no):
    """Independent oracle: scan lines in order; the set open at the top of ``page_no``, or None if the page
    opens with a header."""
    last = None
    for p in pages:
        body = [l for l in p.lines if not l.furniture and l.clean.strip()]
        if p.number == page_no:
            return None if parse_header(body[0].clean) else last
        for l in body:
            if parse_header(l.clean):
                last = parse_header(l.clean)[0]
    return None


def test_exact_candidate_counts_unchanged():
    for path, n in ((VALOR, 12), (RYAN, 23), (LYONS, 10)):
        pages = load_pages(path)
        assert len(find_candidate_pages(pages)) == n


def test_valor_mid_set_page_transitions_get_correct_context(valor):
    pages, headers = valor
    cands = set(find_candidate_pages(pages, headers=headers))
    mid_set = 0
    for p in (q for q in pages if 8 <= q.number <= 18):
        ctx = chunk_context([p], headers, cands)
        want = _expected_open_set(pages, p.number)
        assert ctx.open_set_id == want, p.number
        assert ctx.is_continuation == (want is not None)
        if ctx.is_continuation:
            mid_set += 1
            h = next(h for h in headers if h.line_id == ctx.header_line_id)       # traceable to a real header
            assert h.set_number == want and h.page == ctx.header_page < p.number
    assert mid_set >= 9                                                          # most Valor page breaks are mid-set


def test_valor_page_8_continues_group_03_from_page_7(valor):
    pages, headers = valor
    ctx = chunk_context([p for p in pages if p.number == 8], headers, {7, 8})
    assert (ctx.is_continuation, ctx.open_set_id, ctx.header_page) == (True, "03", 7)
    assert ctx.continues_until_line_id == "8-12"                                  # Group 04 starts on 8-12


def test_ryan_ex2_continues_onto_page_25_then_ends():
    r = build_dry_run(RYAN, pages_hint=[25, 26])
    ctx = r.chunks[0].context
    assert (ctx.is_continuation, ctx.open_set_id, ctx.header_page) == (True, "EX-2.0", 24)
    assert ctx.continues_until_line_id == "26-3"                                  # EX-3.0 header
    fresh = build_dry_run(RYAN, pages_hint=[26]).chunks[0].context
    assert not fresh.is_continuation and fresh.open_set_id is None


def test_lyons_model_text_has_no_icon_glyphs_but_source_keeps_them():
    pages = load_pages(LYONS, page_numbers=list(range(285, 295)))
    originals = [l for p in pages for l in p.lines if PUA.search(l.text)]
    assert len(originals) > 50                                                    # icons really are in the source
    assert all(not PUA.search(l.clean) for p in pages for l in p.lines)
    r = build_dry_run(LYONS, pages_hint=list(range(285, 295)))
    for c in r.chunks:
        assert not PUA.search(c.user_message)
        assert all(not PUA.search(l.model_text) for l in c.lines)
    src = {l.id: l for p in pages for l in p.lines}
    assert any(PUA.search(src[l.id].text) for c in r.chunks for l in c.lines)     # originals untouched


@pytest.mark.parametrize("path,pages", [(LYONS, list(range(285, 295))), (RYAN, list(range(24, 28))),
                                        (VALOR, list(range(8, 11)))])
def test_dry_run_is_clean_traceable_and_has_headers(path, pages):
    r = build_dry_run(path, pages_hint=pages)
    assert r.api_calls_made == 0 and r.banner.startswith("DRY RUN")
    source = {l.id: l for p in load_pages(path) for l in p.lines}
    furniture = {i for i, l in source.items() if l.furniture}
    for c in r.chunks:
        assert not furniture & {l.id for l in c.lines}                            # no furniture reaches the model
        assert set(c.hidden_furniture_line_ids) <= furniture
        for l in c.lines:
            src = source[l.id]
            assert (l.original_text, l.bbox, l.page) == (src.text, src.bbox, src.page)
        for h in c.headers_in_chunk:                                              # headers present in the message
            assert f"[{h.line_id}]" in c.user_message
            assert source[h.line_id].bbox == h.bbox


def test_lyons_not_used_groups_survive_into_the_message():
    r = build_dry_run(LYONS, pages_hint=list(range(285, 295)))
    text = "\n".join(c.user_message for c in r.chunks)
    for n in ("05", "16", "21", "22"):
        assert re.search(rf"Hardware Group No\. {n} - Not Used", text)
