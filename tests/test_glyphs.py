import pytest

from hwsets.ingest import format_lines
from hwsets.schemas import BBox, Line, Page
from hwsets.textnorm import normalize_for_model


@pytest.mark.parametrize("raw,clean", [
    ("SNB \uf09d | 612 | VON", "SNB | 612 | VON"),                          # glyph in its own cell
    ("1 | EA | CORE | C607 | \uf09d | 606 | FAL", "1 | EA | CORE | C607 | 606 | FAL"),
    ("\uf09d \uf07e691", "691"),                                             # glyphs glued to a value
    ("\uf09dLink to catalog cut sheet", "Link to catalog cut sheet"),
    ("\uf09d", ""),
    ("plain text | 630 | LCN", "plain text | 630 | LCN"),                     # untouched
])
def test_private_use_glyphs_removed(raw, clean):
    assert normalize_for_model(raw) == clean


def test_ordinary_unicode_is_preserved():
    s = "Café – 100’ ° ½ • \ufffd 中文 → —"
    assert normalize_for_model(s) == s


def _page():
    def ln(i, t, m):
        return Line(id=f"1-{i}", page=1, text=t, model_text=m, bbox=BBox(x0=1, y0=i, x1=9, y1=i + 1))
    return Page(number=1, width=600, height=800, lines=[
        ln(1, "1 | EA | CORE | C607 \uf09d | 606 | FAL", "1 | EA | CORE | C607 | 606 | FAL"),
        ln(2, "\uf09d", ""),                                               # a line that was only an icon
        ln(3, "plain line", None)])


def test_model_text_is_clean_and_original_is_kept():
    page = _page()
    out = format_lines([page])
    assert "\uf09d" not in out and "[1-1] 1 | EA | CORE | C607 | 606 | FAL" in out
    assert "[1-2]" not in out and "[1-3] plain line" in out                # icon-only line dropped from the prompt
    assert "\uf09d" in page.lines[0].text and page.lines[0].bbox.y0 == 1   # source text + coordinates intact
    assert page.lines[2].clean == "plain line"
