from conftest import FakeExtractor

from hwsets.pipeline import run_pipeline


def test_multipage_set_not_used_and_locations(table_pdf, table_pages):
    res = run_pipeline(table_pdf, extractor=FakeExtractor(table_pages), chunk_size=4)
    by = {s.set_number: s for s in res.sets}
    assert set(by) == {"1", "2"}

    s1 = by["1"]
    assert [c.description for c in s1.components] == ["Hinges", "Closer", "Kick plate"]
    assert [l.page for l in s1.locations] == [2, 3]           # spans both pages
    assert all(l.bbox.y1 > l.bbox.y0 for l in s1.locations)

    kick = s1.components[2]
    assert kick.qty is None                                    # missing quantity stays null
    assert (kick.mfr, kick.finish) == ("PE", "630")            # ambiguous PE stays mfr in mfr column

    s2 = by["2"]
    assert s2.status == "not_used" and s2.components == []
    assert s2.confidence == 1.0


def test_overlapping_chunks_do_not_duplicate(table_pdf, table_pages):
    fake = FakeExtractor(table_pages)
    res = run_pipeline(table_pdf, extractor=fake, pages_hint=[2, 3], chunk_size=1 + 1, workers=1)
    assert len(res.sets) == 2
    assert len(next(s for s in res.sets if s.set_number == "1").components) == 3


def test_swapped_mfr_finish_is_repaired(table_pdf, table_pages):
    res = run_pipeline(table_pdf, extractor=FakeExtractor(table_pages, mfr_finish_swapped=True),
                       pages_hint=[2, 3], workers=1)
    hinge = res.sets[0].components[0]
    assert (hinge.mfr, hinge.finish) == ("MK", "US26D")
    assert any("swapped" in w for w in hinge.warnings)


def test_quantity_not_in_source_is_nulled(table_pdf, table_pages):
    class Liar(FakeExtractor):
        def extract_chunk(self, pages, context=None, resolver=None):
            ext = super().extract_chunk(pages, context, resolver)
            for s in ext.sets:
                for c in s.components:
                    if c.description == "Closer":
                        c.qty = 7                              # hallucinated
            return ext

    res = run_pipeline(table_pdf, extractor=Liar(table_pages), pages_hint=[2, 3], workers=1)
    closer = res.sets[0].components[1]
    assert closer.qty is None and closer.confidence < 0.95


def test_scanned_pdf_reports_no_text():
    import pymupdf
    doc = pymupdf.open(); doc.new_page()
    res = run_pipeline(doc.tobytes(), extractor=object())
    assert res.sets == [] and "no extractable text" in res.warnings[0]


# ---- grounding check for values that wrap over several printed lines ---------------------------------------------
def test_present_accepts_wrapped_cells_but_rejects_invented_or_reordered_values():
    from hwsets.postprocess import _present
    wrapped = "3 | EA | HINGE | 5BB1 4.5 X 4.5 (NRP AT | 639 | IVE OUTSWING LOCKING DOORS)"   # other columns sit between the halves
    assert _present("5BB1 4.5 X 4.5 (NRP AT OUTSWING LOCKING DOORS)", wrapped)
    assert _present("C607", "2 | EA | SFIC CORE | C607 | 606 | FAL")                        # ordinary contiguous case
    assert not _present("5BB1 4.5 X 4.5 (NRP AT ANYWHERE)", wrapped)                        # a token that is not printed
    assert not _present("DOORS) LOCKING OUTSWING", wrapped)                                 # right tokens, wrong order
    assert not _present("", wrapped)


def test_wrapped_catalog_no_longer_gets_a_false_grounding_warning():
    from hwsets.postprocess import _ground_component
    from hwsets.schemas import BBox, Line, LLMComponent
    mk = lambda i, t: Line(id=i, page=1, text=t, bbox=BBox(x0=0, y0=0, x1=1, y1=1))
    lines = {"1-1": mk("1-1", "3 | EA | HINGE | 5BB1 4.5 X 4.5 (NRP AT | 639 | IVE"), "1-2": mk("1-2", "OUTSWING LOCKING DOORS)")}
    def comp(catalog):
        return LLMComponent(qty=3, description="HINGE", catalog_number=catalog, mfr="IVE", finish="639", notes=None,
                            source_line_ids=["1-1", "1-2"], confidence=0.9)
    ok = _ground_component(comp("5BB1 4.5 X 4.5 (NRP AT OUTSWING LOCKING DOORS)"), lines)
    assert ok.warnings == [] and ok.confidence == 0.9
    bad = _ground_component(comp("5BB1 9.9 X 9.9 (INVENTED)"), lines)
    assert any("not found" in w for w in bad.warnings) and bad.confidence < 0.9


def test_present_handles_placeholder_values_without_letters_or_digits():
    from hwsets.postprocess import _present
    assert _present("---", "1 | EA | TRANSFER WIRE | ---")             # printed as a placeholder: grounded
    assert not _present("---", "1 | EA | TRANSFER WIRE | 622")         # not printed: still flagged
    assert not _present("  ", "anything")
