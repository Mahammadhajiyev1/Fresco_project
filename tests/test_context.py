"""Deterministic continuation context: which set is open when a chunk starts."""
from hwsets.headers import chunk_context, detect_set_headers
from hwsets.ingest import load_pages
from hwsets.schemas import BBox, Line, Page


def page(n, *texts):
    return Page(number=n, width=600, height=800, lines=[
        Line(id=f"{n}-{i}", page=n, text=t, bbox=BBox(x0=50, y0=100 + 12 * i, x1=300, y1=110 + 12 * i))
        for i, t in enumerate(texts, 1)])


def ctx(pages, chunk_numbers, candidates=None):
    hs = detect_set_headers(pages)
    chunk = [p for p in pages if p.number in chunk_numbers]
    return chunk_context(chunk, hs, candidates)


def test_set_begins_on_page_n_and_continues_on_n_plus_1():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "2 | Closer | LCN", "1 | Stop | RO")]
    c = ctx(pages, {2})
    assert c.is_continuation and c.open_set_id == "1.0"
    assert (c.header_page, c.header_line_id, c.open_set_header_text) == (1, "1-1", "Set: 1.0")
    assert c.continues_until_line_id is None                  # no new header inside the chunk


def test_new_set_starts_partway_through_page_n_plus_1():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"),
             page(2, "2 | Closer | LCN", "Set: 2.0", "1 | Stop | RO")]
    c = ctx(pages, {2})
    assert c.is_continuation and c.open_set_id == "1.0"       # the first lines still belong to set 1.0
    assert c.continues_until_line_id == "2-2"                 # ...up to the header of 2.0


def test_chunk_opening_with_a_header_carries_nothing_over():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "Set: 2.0", "1 | Stop | RO")]
    c = ctx(pages, {2})
    assert not c.is_continuation and c.open_set_id is None
    assert c.continues_until_line_id == "2-1"


def test_no_previous_open_set():
    pages = [page(1, "prose before any set", "Set: 1.0"), page(2, "1 | Hinge | MK")]
    c = ctx(pages, {1})
    assert not c.is_continuation and c.open_set_id is None and c.header_line_id is None


def test_not_used_set_is_flagged_not_hidden():
    pages = [page(1, "Set: 3.0 - NOT USED"), page(2, "1 | Hinge | MK")]
    c = ctx(pages, {2})
    assert c.is_continuation and c.open_set_id == "3.0" and c.open_set_not_used is True


def test_multi_page_chain_uses_the_latest_header():
    pages = [page(1, "Set: 1.0"), page(2, "Set: 2.0", "1 | X | MK"), page(3, "1 | Y | MK"), page(4, "1 | Z | MK")]
    assert ctx(pages, {4}).open_set_id == "2.0"


def test_gap_of_non_candidate_pages_breaks_the_chain():
    pages = [page(1, "Set: 1.0"), page(2, "unrelated prose"), page(3, "1 | Y | MK")]
    assert not ctx(pages, {3}, candidates={1, 3}).is_continuation
    assert ctx(pages, {3}, candidates={1, 2, 3}).is_continuation


def test_furniture_is_ignored_when_finding_the_first_line(furniture_pdf):
    pages = load_pages(furniture_pdf)                          # p2 = continuation rows; header/footer are furniture
    c = ctx(pages, {2}, candidates={1, 2, 3})
    assert c.is_continuation and c.open_set_id == "1" and c.header_page == 1
