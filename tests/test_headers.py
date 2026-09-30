import pytest

from hwsets.headers import detect_set_headers, parse_header
from hwsets.ingest import load_pages


@pytest.mark.parametrize("text,number,not_used", [
    ("Hardware Group No. 01", "01", False),
    ("Hardware Group No. 21A: (Doors 101, 102)", "21A", False),
    ("HARDWARE GROUP NO. 004", "004", False),
    ("Hardware Group No. 05 - Not Used", "05", True),
    ("Hardware Group No. 22 – N/A", "22", True),
    ("Hardware Group/Set #02", "02", False),
    ("Set: EX-1.0", "EX-1.0", False),
    ("Set: 2.5", "2.5", False),
    ("Set: 12.0", "12.0", False),
    ("Set #AL 01", "AL 01", False),
    ("SET #1 - ENTRANCE DOORS", "1", False),
    ("Hardware Set 3B", "3B", False),
    ("HW 04A Interior Single - Storeroom Lockset", "04A", False),
    ("HW 04 | Interior Single - Surface Closer", "04", False),
    ("HARDWARE SET NO. 7 (DELETED)", "7", True),
    # prefixed forms (outline numbering / part titles in front of the keyword)
    ("PART 79 - HARDWARE GROUP NO. C201EZ", "C201EZ", False),
    ("PART 80 - Hardware Group No. 12A", "12A", False),
    ("3.2 Hardware Set 5", "5", False),
    ("3.2.1 | HARDWARE GROUP NO. 07 - ENTRY", "07", False),
    ("A. HARDWARE GROUP NO. 7 - ENTRY", "7", False),
    ("1) Hardware Set #4", "4", False),
    # longer / unusual identifiers
    ("HARDWARE GROUP NO. XCY746", "XCY746", False),
    ("Hardware Group No. ABCD-1234-XY", "ABCD-1234-XY", False),
    ("Hardware Group No. 005.1", "005.1", False),
    ("Hardware Group No. D101/D102", "D101/D102", False),
    ("Set: HW-12B.1", "HW-12B.1", False),
    ("PART 79 - HARDWARE GROUP NO. C201EZ - NOT USED", "C201EZ", True),
])
def test_headers_recognised(text, number, not_used):
    got = parse_header(text)
    assert got is not None, text
    assert got[0] == number and got[2] is not_used


@pytest.mark.parametrize("text", [
    "Group 1 stainless-steel bolts, ASTM F 593, and nuts",       # bare 'Group', no marker
    "Interior Hardware Sets",                                    # no identifier
    "Hardware sets are indicated on Drawings",
    "Hardware Group No. 5 shall be provided at all doors",       # lowercase prose continuation
    "Set units level, plumb, and true to line and location.",
    "Provide Hardware Set 3 at exterior doors",                  # not at line start
    "Set: the following items",                                  # no digit identifier
    "Group Number: 1.",                                          # list item, trailing period
    "B. | Silencers and gasketing, where listed in Hardware Sets, may be omitted",
    "",
    "Hardware Group No. 01 " + "x" * 200,                        # implausibly long
    # prefixed prose / section furniture
    "PART 1 - GENERAL",
    "PART 79 - PROVIDE EACH SGL DOOR(S) WITH THE FOLLOWING:",
    "1. | Door Hardware Sets are indicated on Drawings (Door Schedule).",
    "A. | Hardware Group No. 5 shall be provided",
    "Refer to Hardware Group No. 5 for details",
    "2.3 Hardware sets and groups are listed below",
    "3.1 HARDWARE SCHEDULE",
    "1.11 | SCHEDULED DOOR HARDWARE",
    "Group 1 or 2 (ASTM F 738M)",
    "Set: 2026-01-28 issued",
    "Hardware Group Numbers are assigned by the architect",
    "HW 2 hours minimum rating required",
    "Set: 1234567890123456789 too long",                         # identifier longer than any real one
])
def test_prose_rejected(text):
    assert parse_header(text) is None, text


def test_detect_on_pdf_has_traceability(furniture_pdf):
    pages = load_pages(furniture_pdf)
    hs = detect_set_headers(pages)
    assert [h.set_number for h in hs] == ["1", "2", "3"]
    by_id = {l.id: l for p in pages for l in p.lines}
    for h in hs:
        line = by_id[h.line_id]
        assert line.page == h.page and line.text == h.text and line.bbox == h.bbox
        assert h.bbox.x1 > h.bbox.x0 and h.bbox.y1 > h.bbox.y0
    assert [h.page for h in hs] == [1, 3, 3]


def test_not_used_inline_and_on_following_line(furniture_pdf):
    hs = {h.set_number: h for h in detect_set_headers(load_pages(furniture_pdf))}
    assert hs["1"].not_used is False
    assert hs["2"].not_used is True          # "... - NOT USED" on the header line
    assert hs["3"].not_used is True          # "N/A" on its own line right below
