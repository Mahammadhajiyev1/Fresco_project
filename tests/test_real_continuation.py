"""Continuation block on the real sample PDFs (dry run only; skipped when data/ is absent)."""
import re
from pathlib import Path

import pytest

from hwsets.dryrun import build_dry_run
from hwsets.ingest import load_pages
from hwsets.textnorm import strip_private_use

DATA = Path(__file__).parents[1] / "data"
VALOR = DATA / "087100-DOOR-HARDWARE_Rev_2.pdf"
RYAN = DATA / "087100 - Door Hardware-6.pdf"
LYONS = DATA / "Project Manual (1).pdf"
pytestmark = pytest.mark.skipif(not all(p.exists() for p in (VALOR, RYAN, LYONS)),
                                reason="real sample PDFs not in data/")
MARK = "CONTINUATION CONTEXT"
PUA = re.compile("[\ue000-\uf8ff]")


def test_valor_8_10_begins_inside_group_03():
    c = build_dry_run(VALOR, pages_hint=[8, 9, 10]).chunks[0]
    m = c.user_message
    assert m.startswith(MARK) and c.context_in_message
    assert "hardware set '03'" in m and "[7-43]" in m and "page 7" in m
    assert "before the next set header at [8-12]" in m
    # the carried lines (8-5 .. 8-11) come first, then Group 04 appears as an ordinary visible header
    body = m[m.index("=== PAGE 8 ==="):]
    assert body.index("[8-5]") < body.index("[8-11]") < body.index("[8-12] Hardware Group No. 04")
    for n in ("04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14"):
        assert f"Hardware Group No. {n}" in m                                   # later headers stay visible
    assert [h.set_number for h in c.headers_in_chunk][:3] == ["04", "05", "06"]
    assert "Hardware Group No. 03" not in m                                     # its header line is honestly absent


def test_ryan_page_25_continues_ex_2_0_and_ex_3_0_ends_it():
    c = build_dry_run(RYAN, pages_hint=[25, 26]).chunks[0]
    m = c.user_message
    assert m.startswith(MARK) and "hardware set 'EX-2.0'" in m and "[24-29]" in m and "page 24" in m
    assert "before the next set header at [26-3]" in m
    body = m[m.index("=== PAGE 25 ==="):]
    assert body.index("[25-3]") < body.index("[26-3] Set: EX-3.0")             # carried line, then the new header
    assert not re.search(r"arkaspecs|Copyright 2025|Bulletin 06|08 71 00 - 2\d", m)   # page furniture absent
    fresh = build_dry_run(RYAN, pages_hint=[26]).chunks[0]
    assert MARK not in fresh.user_message and fresh.context_in_message is False   # EX-3.0 terminates the carry


def test_lyons_has_no_unneeded_continuation_and_text_is_only_glyph_normalised():
    r = build_dry_run(LYONS, pages_hint=list(range(285, 295)))
    assert all(MARK not in c.user_message and not c.context_in_message for c in r.chunks)
    src = {l.id: l for p in load_pages(LYONS) for l in p.lines}
    for c in r.chunks:
        assert not PUA.search(c.user_message)
        for l in c.lines:
            assert l.model_text == strip_private_use(src[l.id].text)             # the ONLY change is the glyphs
            assert f"[{l.id}] {l.model_text}" in c.user_message
            assert re.sub(r"\s+", "", PUA.sub("", l.original_text).replace("|", "")) == \
                   re.sub(r"\s+", "", l.model_text.replace("|", ""))            # nothing else lost or added


def test_block_never_carries_source_content_on_real_documents():
    for path, pages in ((VALOR, [8, 9]), (RYAN, [25, 26])):
        m = build_dry_run(path, pages_hint=pages).chunks[0].user_message
        block = m[: m.index("Pages ")]
        assert len(block) < 700 and block.count("\n") <= 5
        assert not re.search(r"\b\d+\s*\|\s*EA\b|Pemko|Sargent|Norton|BLK|Door U", block)


def test_valor_every_split_point_recomputes_the_right_open_set():
    """A derived chunk starting at any Valor page gets the same context an independent scan expects."""
    from test_real_dryrun import _expected_open_set

    from hwsets.headers import ContextResolver, detect_set_headers
    from hwsets.pages import find_candidate_pages

    pages = load_pages(VALOR)
    hs = detect_set_headers(pages)
    res = ContextResolver(pages, hs, set(find_candidate_pages(pages, headers=hs)))
    checked = 0
    for k in range(8, 18):
        derived = [p for p in pages if p.number in (k, k + 1)]           # e.g. the second half of a split chunk
        assert res(derived).open_set_id == _expected_open_set(pages, k), k
        assert res(derived[:1]).open_set_id == res(derived).open_set_id  # the start alone decides the context
        checked += 1
    assert checked == 10
