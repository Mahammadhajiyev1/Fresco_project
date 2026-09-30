"""Candidate-page selection must not depend on recognised headers alone (synthetic pages)."""
from hwsets.pages import component_lines, find_candidate_pages
from hwsets.schemas import BBox, Line, Page

ROW = "3 | Hinge assembly | MK | US26D"


def page(n, *texts):
    return Page(number=n, width=600, height=800, lines=[
        Line(id=f"{n}-{i}", page=n, text=t, bbox=BBox(x0=50, y0=100 + 12 * i, x1=300, y1=110 + 12 * i))
        for i, t in enumerate(texts, 1)])


def rows(k=3):
    return [ROW] * k


def test_component_lines_counts_rows_not_prose():
    assert component_lines(page(1, *rows(3))) == 3
    assert component_lines(page(1, "a. | Provide hinges as indicated | by Architect", "1. | Comply with ANSI A156")) == 0
    assert component_lines(page(1, "222A-1 | 223 | 224 | 323 | 324 | 325")) == 0     # door-number list


def test_unrecognised_header_style_does_not_hide_its_pages():
    """Pages 1-2 use a recognised header, pages 3-4 an unknown one; both are tables."""
    pages = [page(1, "Hardware Group No. 1", *rows(3)), page(2, *rows(3)),
             page(3, "OPENING TYPE A-1 (weird header)", *rows(3)), page(4, *rows(3)),
             page(5, "Ordinary prose only.")]
    assert find_candidate_pages(pages) == [1, 2, 3, 4]


def test_table_pages_far_from_any_header_are_kept_as_islands():
    prose = "Some ordinary specification prose."
    pages = [page(1, "Set: 1.0", *rows(2)), page(2, prose), page(3, prose), page(4, prose), page(5, prose),
             page(6, "WEIRD STYLE 9", *rows(5)), page(7, prose)]
    assert find_candidate_pages(pages) == [1, 6]


def test_keyword_prose_with_pipes_but_no_codes_is_not_selected():
    noisy = [f"{i}. | Provide hinges, closers, locksets and exit devices | per manufacturer" for i in range(1, 15)]
    pages = [page(1, "Set: 1.0", *rows(2)), page(2, *noisy), page(3, *noisy)]
    assert find_candidate_pages(pages) == [1]                     # full prose pages are not "stub" continuations


def test_only_one_short_stub_page_follows_a_set():
    stub = "Gasketing, threshold, sweep, astragal By Door Manufacturer"
    pages = [page(1, "Set: 1.0", *rows(2)), page(2, stub), page(3, stub), page(4, stub)]
    assert find_candidate_pages(pages) == [1, 2]                  # one stub, never a chain of keyword pages


def test_region_grows_backwards_over_table_pages():
    pages = [page(1, "prose"), page(2, *rows(3)), page(3, "Set: 4", *rows(2))]
    assert find_candidate_pages(pages) == [2, 3]


def test_end_of_section_and_new_section_stop_growth():
    pages = [page(1, "Set: 1.0", *rows(2), "END OF SECTION 087100"),
             page(2, *rows(3)),                                   # belongs to the next section
             page(3, "SECTION 087200", *rows(5))]
    assert find_candidate_pages(pages) == [1, 3]                # 3 is an island on its own; 2 is cut off
    pages2 = [page(1, "Set: 1.0", *rows(2)), page(2, "SECTION 087200 - OTHER", *rows(1))]
    assert find_candidate_pages(pages2) == [1]


def test_headerless_document_without_tables_falls_back_to_keywords():
    text = "Hinges closers lockset exit device kick plate threshold gasketing cylinder mfr finish catalog"
    pages = [page(1, "prose"), page(2, "prose"), page(3, text), page(4, "prose"), page(5, "prose")]
    assert find_candidate_pages(pages) == [2, 3, 4]
