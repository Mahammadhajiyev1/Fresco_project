"""Synthetic specbook PDFs + a fake extractor so tests run offline."""
from __future__ import annotations

import pymupdf
import pytest

from hwsets.ingest import load_pages
from hwsets.schemas import LLMColumn, LLMComponent, LLMExtraction, LLMLayout, LLMSet, Page

# x positions of table columns
COLS = [50, 90, 300, 400, 470, 520]  # qty, desc, catalog, mfr, finish, notes


def _row(page: pymupdf.Page, y: float, cells: list[str]) -> None:
    for x, text in zip(COLS, cells):
        if text:
            page.insert_text((x, y), text, fontsize=9)


def make_table_pdf() -> bytes:
    """3 pages. Set 1 (table) spans pages 2-3. Set 2 is NOT USED. Page 1 is unrelated prose."""
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((50, 80), "SECTION 08 71 00 - GENERAL REQUIREMENTS. The contractor shall provide all "
                             "labor and materials in accordance with the drawings.", fontsize=9)

    p2 = doc.new_page()
    p2.insert_text((50, 60), "HARDWARE SET #1 - ENTRANCE DOORS", fontsize=11)
    _row(p2, 90, ["QTY", "DESCRIPTION", "CATALOG NO.", "MFR", "FINISH"])
    _row(p2, 110, ["3", "Hinges", "TA2714 4.5x4.5", "MK", "US26D"])
    _row(p2, 125, ["1", "Closer", "4040XP", "LCN", "689"])

    p3 = doc.new_page()
    _row(p3, 60, ["QTY", "DESCRIPTION", "CATALOG NO.", "MFR", "FINISH"])
    _row(p3, 80, ["", "Kick plate", "K1050 10x34", "PE", "630"])
    _row(p3, 110, ["", "HARDWARE SET #2 - NOT USED"])
    return doc.tobytes()


@pytest.fixture(scope="session")
def table_pdf() -> bytes:
    return make_table_pdf()


@pytest.fixture(scope="session")
def table_pages(table_pdf) -> list[Page]:
    return load_pages(table_pdf)


def find_id(pages: list[Page], needle: str) -> str:
    for p in pages:
        for l in p.lines:
            if needle in l.text:
                return l.id
    raise AssertionError(f"{needle!r} not on any page")


class FakeExtractor:
    """Stands in for Claude: returns the extraction a correct model would, keyed by page content."""

    def __init__(self, pages: list[Page], mfr_finish_swapped: bool = False):
        self.pages = pages
        self.swapped = mfr_finish_swapped
        self.calls: list[tuple[int, int]] = []

    def _c(self, needle, qty, desc, cat, mfr, fin, extra=None):
        ids = [find_id(self.pages, needle)]
        if self.swapped:
            mfr, fin = fin, mfr
        return LLMComponent(qty=qty, description=desc, catalog_number=cat, mfr=mfr, finish=fin,
                            notes=None, source_line_ids=ids + (extra or []), confidence=0.95)

    def extract_chunk(self, pages: list[Page], context=None, resolver=None) -> LLMExtraction:
        self.calls.append((pages[0].number, pages[-1].number))
        nums = {p.number for p in pages}
        sets: list[LLMSet] = []
        if nums & {2, 3}:
            comps = []
            if 2 in nums:
                comps += [self._c("TA2714", 3, "Hinges", "TA2714 4.5x4.5", "MK", "US26D"),
                          self._c("4040XP", 1, "Closer", "4040XP", "LCN", "689")]
            if 3 in nums:
                comps += [self._c("K1050", None, "Kick plate", "K1050 10x34", "PE", "630")]
            sets.append(LLMSet(
                set_number="1", description="ENTRANCE DOORS", status="active",
                header_line_ids=[find_id(self.pages, "SET #1")] if 2 in nums else [],
                components=comps, continues_from_previous_page=2 not in nums,
                continues_on_next_page=3 not in nums))
        if 3 in nums:
            sets.append(LLMSet(
                set_number="2", description="NOT USED", status="not_used",
                header_line_ids=[find_id(self.pages, "SET #2")], components=[],
                continues_from_previous_page=False, continues_on_next_page=False))
        layout = LLMLayout(format="table", notes="MFR and FINISH columns", columns=[
            LLMColumn(header="MFR", role="mfr", sample_values=["MK", "LCN", "PE"]),
            LLMColumn(header="FINISH", role="finish", sample_values=["US26D", "689", "630"])])
        return LLMExtraction(layout=layout, sets=sets)


def make_furniture_pdf() -> bytes:
    """5 pages with a running header + 'Page N of 5' footer on every page.

    p1: SET #1 header + rows   p2: continuation rows only (no header)
    p3: SET #2 - NOT USED, then SET #3 with an N/A line under it   p4-5: prose
    """
    doc = pymupdf.open()
    body = {
        1: [(100, "HARDWARE SET #1 - ENTRANCE DOORS"), (120, "3    Hinges    TA2714    MK    US26D"),
            (135, "1    Closer    4040XP    LCN    689")],
        2: [(100, "1    Kick plate    K1050    PE    630"), (115, "1    Threshold    655A    ZER    A")],
        3: [(100, "HARDWARE SET #2 - NOT USED"), (140, "HARDWARE SET #3"), (155, "N/A")],
        4: [(100, "This page is ordinary specification prose about installation.")],
        5: [(100, "More ordinary prose. Coordinate work with the general contractor.")],
    }
    for n in range(1, 6):
        pg = doc.new_page()
        pg.insert_text((50, 30), "ACME TOWER RENOVATION - ISSUED FOR BID", fontsize=8)
        for y, text in body[n]:
            pg.insert_text((50, y), text, fontsize=9)
        pg.insert_text((50, 810), f"Page {n} of 5", fontsize=8)
    return doc.tobytes()


@pytest.fixture(scope="session")
def furniture_pdf() -> bytes:
    return make_furniture_pdf()


@pytest.fixture(autouse=True)
def _offline_tests_never_see_an_api_key(request, monkeypatch):
    """Even when .env holds a real key, every non-live test runs WITHOUT it, so the offline suite can't reach the API.
    (Tests that need a key set a dummy one themselves.)"""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
