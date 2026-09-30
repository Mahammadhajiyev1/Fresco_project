from hwsets.ingest import format_lines, load_pages, page_from_pymupdf
from hwsets.mock import MockExtractor
from hwsets.pipeline import run_pipeline
from hwsets.postprocess import locate


def test_repeated_margin_lines_flagged_body_preserved(furniture_pdf):
    pages = load_pages(furniture_pdf)
    flagged = {l.text.split(" ")[0] for p in pages for l in p.lines if l.furniture}
    assert flagged == {"ACME", "Page"}                       # digits normalised: "Page 1 of 5" == "Page 2 of 5"
    for p in pages:
        assert sum(l.furniture for l in p.lines) == 2        # exactly the header and the footer
    body = [l.text for p in pages for l in p.lines if not l.furniture]
    assert any("HARDWARE SET #1" in t for t in body) and any("Kick plate" in t for t in body)


def test_set_headers_never_furniture_even_if_repeated(furniture_pdf):
    pages = load_pages(furniture_pdf)
    assert all(not l.furniture for p in pages for l in p.lines if "HARDWARE SET" in l.text)


def test_ids_and_coordinates_unchanged_by_filtering(furniture_pdf):
    import pymupdf
    raw = pymupdf.open(stream=furniture_pdf, filetype="pdf")
    for filtered in load_pages(furniture_pdf):
        plain = page_from_pymupdf(raw[filtered.number - 1])
        assert [(l.id, l.text, l.bbox) for l in filtered.lines] == [(l.id, l.text, l.bbox) for l in plain.lines]


def test_furniture_hidden_from_prompt_text_but_not_dropped(furniture_pdf):
    pages = load_pages(furniture_pdf)
    text = format_lines(pages)
    assert "ACME TOWER" not in text and "Page 1 of 5" not in text and "HARDWARE SET #1" in text
    assert any(l.furniture for p in pages for l in p.lines)   # still present for traceability


def test_locate_ignores_cited_furniture(furniture_pdf):
    p1 = load_pages(furniture_pdf, page_numbers=[1])[0]
    idx = {l.id: l for l in p1.lines}
    header = next(l for l in p1.lines if "SET #1" in l.text)
    footer = next(l for l in p1.lines if l.text.startswith("Page"))
    loc = locate([header.id, footer.id], idx)[0]
    assert loc.bbox.y1 < footer.bbox.y0 and footer.id not in loc.line_ids


def test_set_location_does_not_stretch_over_footer(furniture_pdf):
    res = run_pipeline(furniture_pdf, extractor=MockExtractor(), pages_hint=[1, 2, 3], workers=1)
    s1 = next(s for s in res.sets if s.set_number == "1")
    footer_y0 = min(l.bbox.y0 for p in load_pages(furniture_pdf) for l in p.lines if l.text.startswith("Page"))
    assert s1.locations and all(loc.bbox.y1 < footer_y0 for loc in s1.locations)


def test_no_false_furniture_on_short_documents(table_pdf):
    assert not any(l.furniture for p in load_pages(table_pdf) for l in p.lines)   # only 3 pages, no repeats


def _group_pdf() -> bytes:
    """Every page opens with a group header, then 'For use on Door #(s):' at the SAME height (below content),
    plus a running header, a footer and a bare page number - the layout that fooled the first filter."""
    import pymupdf
    doc = pymupdf.open()
    for n in range(1, 7):
        pg = doc.new_page()
        pg.insert_text((50, 30), "ACME TOWER - ISSUED FOR BID", fontsize=8)          # running header
        pg.insert_text((50, 60), f"Hardware Group No. {n:02d}", fontsize=10)
        pg.insert_text((50, 75), "For use on Door #(s):", fontsize=9)                 # repeats, but is content
        pg.insert_text((50, 90), f"D{n}01", fontsize=9)                               # repeats after digit-normalising
        pg.insert_text((50, 110), "1    Hinge    TA2714    MK    US26D", fontsize=9)
        pg.insert_text((50, 790), "DOOR HARDWARE", fontsize=8)                        # footer
        pg.insert_text((50, 810), str(n), fontsize=8)                                 # bare page number
    return doc.tobytes()


def test_repeated_content_below_a_header_is_not_furniture():
    pages = load_pages(_group_pdf())
    flagged = {l.text for p in pages for l in p.lines if l.furniture}
    assert flagged == {"ACME TOWER - ISSUED FOR BID", "DOOR HARDWARE", "1", "2", "3", "4", "5", "6"}
    kept = [l for p in pages for l in p.lines if not l.furniture]
    assert sum(l.text == "For use on Door #(s):" for l in kept) == 6            # every label survives
    assert sum(l.text.startswith("D") and l.text.endswith("01") for l in kept) == 6
    assert "For use on Door #(s):" in format_lines(pages[:1])


def test_furniture_must_be_an_unbroken_run_at_the_page_edge():
    from hwsets.furniture import _edge_runs
    from hwsets.schemas import BBox, Line, Page

    def page(*rows):                                  # rows: (id, y) on an 800pt page: top band < 120, bottom > 720
        return Page(number=1, width=600, height=800, lines=[
            Line(id=i, page=1, text=i, bbox=BBox(x0=0, y0=y, x1=9, y1=y + 5)) for i, y in rows])
    p = page(("a", 10), ("b", 30), ("body", 400), ("c", 740), ("d", 760), ("e", 780))
    assert _edge_runs(p, {"a", "b", "c", "d", "e"}) == {"a", "b", "c", "d", "e"}     # top run + bottom run
    assert _edge_runs(p, {"a", "c"}) == {"a"}                                        # 'c' is cut off from the bottom edge
    assert _edge_runs(p, {"b", "d"}) == set()                                        # nothing touches an edge
    # a repeated line in the TOP band can never be reached by the bottom-edge walk, whatever lies between
    q = page(("hdr", 10), ("content", 60), ("row", 100), ("footer", 780))
    assert _edge_runs(q, {"content", "row", "footer"}) == {"footer"}
