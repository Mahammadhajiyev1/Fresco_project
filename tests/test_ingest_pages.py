from hwsets.ingest import format_lines, load_pages
from hwsets.pages import chunk_pages, find_candidate_pages, score_page
from hwsets.schemas import BBox, Line, Page


def test_lines_have_bboxes_and_column_gaps(table_pages):
    p2 = table_pages[1]
    row = next(l for l in p2.lines if "TA2714" in l.text)
    assert row.id.startswith("2-")
    assert " | " in row.text                      # column gaps exposed
    assert row.text.split(" | ")[-2:] == ["MK", "US26D"]
    assert 0 < row.bbox.x0 < row.bbox.x1 < p2.width
    assert row.bbox.y0 < row.bbox.y1 < p2.height


def test_format_lines_contains_ids(table_pages):
    text = format_lines(table_pages[1:2])
    assert "=== PAGE 2 ===" in text and "[2-1]" in text


def test_candidate_pages_use_header_evidence(table_pages):
    assert score_page(table_pages[0]) < score_page(table_pages[1])
    assert find_candidate_pages(table_pages) == [2, 3]          # prose page 1 excluded


def test_continuation_page_without_header_is_kept(furniture_pdf):
    pages = load_pages(furniture_pdf)
    assert find_candidate_pages(pages) == [1, 2, 3]             # p2 has rows but no header; prose p4-5 dropped


def _mkpage(n, *texts):
    return Page(number=n, width=600, height=800, lines=[
        Line(id=f"{n}-{i}", page=n, text=t, bbox=BBox(x0=50, y0=100 + 12 * i, x1=200, y1=110 + 12 * i))
        for i, t in enumerate(texts, 1)])


def test_gap_between_header_pages_is_kept_even_without_keywords():
    pages = [_mkpage(1, "Set: 1.0"), _mkpage(2, "zzz qqq"), _mkpage(3, "Set: 2.0"), _mkpage(4, "zzz qqq")]
    assert find_candidate_pages(pages) == [1, 2, 3]             # gap page 2 kept; trailing junk page 4 dropped


def test_end_of_section_stops_continuation():
    pages = [_mkpage(1, "Set: 1.0", "1 Hinge | MK", "END OF SECTION"),
             _mkpage(2, "Hinges closers gasketing finish catalog")]
    assert find_candidate_pages(pages) == [1]


def test_headerless_document_falls_back_to_keyword_scoring():
    text = "Hinges closers lockset exit device kick plate threshold gasketing cylinder mfr finish catalog"
    pages = [_mkpage(1, "prose"), _mkpage(2, "prose"), _mkpage(3, text), _mkpage(4, "prose"), _mkpage(5, "prose")]
    assert find_candidate_pages(pages) == [2, 3, 4]              # keyword hit +/- 1 page margin


def _pages(nums):
    return [Page(number=n, width=1, height=1, lines=[]) for n in nums]


def test_chunking_overlaps_and_splits_on_gaps():
    chunks = chunk_pages(_pages(range(1, 8)), size=4, overlap=1)
    assert [[p.number for p in c] for c in chunks] == [[1, 2, 3, 4], [4, 5, 6, 7]]
    gap = chunk_pages(_pages([1, 2, 10, 11]), size=4, overlap=1)
    assert [[p.number for p in c] for c in gap] == [[1, 2], [10, 11]]
