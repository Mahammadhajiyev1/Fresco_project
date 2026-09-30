"""Context survives split/retry: every derived sub-chunk recomputes which set is open. No model is called."""
import re

from hwsets.extractor import Extractor
from hwsets.headers import ContextResolver, END_OF_SECTION, detect_set_headers
from hwsets.pipeline import run_pipeline
from hwsets.schemas import BBox, Line, LLMExtraction, LLMLayout, Page

MARK = "CONTINUATION CONTEXT"
EMPTY = LLMExtraction(layout=LLMLayout(format="none", columns=[], notes=""), sets=[])


def page(n, *texts):
    return Page(number=n, width=600, height=800, lines=[
        Line(id=f"{n}-{i}", page=n, text=t, bbox=BBox(x0=50, y0=100 + 12 * i, x1=300, y1=110 + 12 * i))
        for i, t in enumerate(texts, 1)])


class TruncatingClient:
    """Pretends every call covering more than one page is truncated (max_tokens), forcing a split."""

    def __init__(self):
        self.calls = []                                   # (first_page, last_page, user_message)
        self.messages = self

    def parse(self, **kw):
        msg = kw["messages"][0]["content"]
        a, b = map(int, re.search(r"Pages (\d+)-(\d+)", msg).groups())
        self.calls.append((a, b, msg))

        class R:
            stop_reason = "max_tokens" if b > a else "end_turn"
            parsed_output = None if b > a else EMPTY
        return R()

    def message_for(self, first, last):
        return next(m for a, b, m in self.calls if (a, b) == (first, last))


def run(pages, chunk_nums, candidates=None, resolver=True):
    hs = detect_set_headers(pages)
    res = ContextResolver(pages, hs, candidates)
    chunk = [p for p in pages if p.number in chunk_nums]
    client = TruncatingClient()
    Extractor(client=client, model="m").extract_chunk(chunk, res(chunk), res if resolver else None)
    return client


def test_split_in_the_middle_of_a_normal_set():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "2 | Closer | LCN", "1 | Stop | RO")]
    c = run(pages, {1, 2})
    assert [(a, b) for a, b, _ in c.calls] == [(1, 2), (1, 1), (2, 2)]      # whole chunk, then both halves
    assert MARK not in c.message_for(1, 1)                                 # first half starts on the header
    second = c.message_for(2, 2)
    assert second.startswith(MARK) and "hardware set '1.0'" in second and "[1-1] on page 1" in second


def test_split_begins_exactly_on_a_new_set_header():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "Set: 2.0", "1 | Stop | RO")]
    c = run(pages, {1, 2})
    assert MARK not in c.message_for(2, 2)                                 # fresh start: nothing carried over


def test_split_inside_a_not_used_set_keeps_its_status():
    pages = [page(1, "Set: 3.0 - NOT USED"), page(2, "1 | Hinge | MK")]
    second = run(pages, {1, 2}).message_for(2, 2)
    assert second.startswith(MARK) and "hardware set '3.0'" in second
    assert "The open set is marked NOT USED/N/A. Preserve the set (status not_used) even if it contains no " \
           "components; do not invent components for it." in second


def test_multiple_splits_each_get_their_own_correct_context():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "2 | Closer | LCN"), page(3, "1 | Stop | RO", "1 | Lock | SCH"),
             page(4, "1 | Kick | RO", "Set: 2.0", "1 | Hinge | MK")]
    c = run(pages, {1, 2, 3, 4})
    assert [(a, b) for a, b, _ in c.calls] == [(1, 4), (1, 2), (1, 1), (2, 2), (3, 4), (3, 3), (4, 4)]
    assert MARK not in c.message_for(1, 1)
    assert "hardware set '1.0'" in c.message_for(2, 2)
    assert c.message_for(3, 4).startswith(MARK)                            # the intermediate half already had it
    assert "hardware set '1.0'" in c.message_for(3, 3) and "hardware set '1.0'" in c.message_for(4, 4)
    assert "before the next set header at [4-2]" in c.message_for(4, 4)    # carried lines end at the new header
    assert "All lines in this chunk belong" in c.message_for(3, 3)         # no new header on page 3


def test_previous_set_ends_before_the_derived_chunk_starts():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK", "END OF SECTION 087100"), page(2, "1 | Stop | RO"), page(3, "1 | Kick | RO")]
    c = run(pages, {2, 3})
    assert MARK not in c.message_for(2, 2) and MARK not in c.message_for(3, 3)   # set 1.0 was closed on page 1


def test_end_of_section_before_the_header_line_does_not_close_it():
    pages = [page(1, "END OF SECTION 087000", "Set: 1.0", "1 | Hinge | MK"), page(2, "1 | Stop | RO")]
    ctx = ContextResolver(pages, detect_set_headers(pages))([pages[1]])
    assert ctx.is_continuation and ctx.open_set_id == "1.0"                # END came BEFORE the header: still open


def test_previous_set_is_not_carried_across_a_non_candidate_gap():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "unrelated prose"), page(3, "1 | Stop | RO", "1 | X | MK")]
    hs = detect_set_headers(pages)
    assert not ContextResolver(pages, hs, {1, 3})([pages[2]]).is_continuation
    assert ContextResolver(pages, hs, {1, 2, 3})([pages[2]]).is_continuation


def test_derived_chunks_never_receive_a_stale_copy_of_the_parent_context():
    """Parent chunk starts inside set 1.0 (context present). The second half starts on a NEW set: no block."""
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "1 | Closer | LCN"), page(3, "Set: 2.0", "1 | Stop | RO")]
    res = ContextResolver(pages, detect_set_headers(pages))
    parent = [pages[1], pages[2]]
    assert res(parent).is_continuation
    client = TruncatingClient()
    Extractor(client=client, model="m").extract_chunk(parent, res(parent), res)
    assert client.message_for(2, 2).startswith(MARK)                       # first half: same start, same context
    assert MARK not in client.message_for(3, 3)                            # second half: recomputed, starts fresh


def test_without_a_resolver_the_second_half_gets_no_context_rather_than_a_stale_one():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "1 | Closer | LCN"), page(3, "1 | Stop | RO")]
    res = ContextResolver(pages, detect_set_headers(pages))
    parent = [pages[1], pages[2]]
    client = TruncatingClient()
    Extractor(client=client, model="m").extract_chunk(parent, res(parent))   # no resolver supplied
    assert client.message_for(2, 2).startswith(MARK)                       # same first line: parent's context is valid
    assert MARK not in client.message_for(3, 3)                            # cannot be recomputed: omitted, not guessed


def test_context_is_never_manufactured_content(furniture_pdf):
    c = run([page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "2 | Closer | LCN")], {1, 2})
    block = c.message_for(2, 2).split("Pages 2-2")[0]
    assert not re.search(r"Hinge|Closer|LCN|MK", block)


def test_pipeline_hands_the_extractor_a_resolver(furniture_pdf):
    seen = {}

    class Recorder:
        def extract_chunk(self, pages, context=None, resolver=None):
            seen["resolver"], seen["context"] = resolver, context
            return EMPTY

    run_pipeline(furniture_pdf, extractor=Recorder(), pages_hint=[2, 3], workers=1)
    assert seen["context"].open_set_id == "1"
    from hwsets.ingest import load_pages
    pages = load_pages(furniture_pdf)
    assert seen["resolver"]([pages[2]]).open_set_id is None                 # page 3 opens with a header: fresh
    assert seen["resolver"]([pages[1]]).open_set_id == "1"


def test_end_of_section_regex_is_a_real_word_boundary_regex():
    assert END_OF_SECTION.search("END OF SECTION 087100") and END_OF_SECTION.search("  end  of section")
    assert not END_OF_SECTION.search("the weekend of sectional doors")
    assert "\x08" not in END_OF_SECTION.pattern                           # guards against a stray backspace
