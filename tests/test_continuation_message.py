"""The continuation block that is prepended to the model message (deterministic, no API)."""
import re

from hwsets.dryrun import build_dry_run
from hwsets.extractor import Extractor
from hwsets.headers import chunk_context, detect_set_headers
from hwsets.pipeline import run_pipeline
from hwsets.prompts import build_user_message, continuation_block
from hwsets.schemas import BBox, ChunkContext, Line, LLMExtraction, LLMLayout, Page

MARK = "CONTINUATION CONTEXT"


def page(n, *texts):
    return Page(number=n, width=600, height=800, lines=[
        Line(id=f"{n}-{i}", page=n, text=t, bbox=BBox(x0=50, y0=100 + 12 * i, x1=300, y1=110 + 12 * i))
        for i, t in enumerate(texts, 1)])


def message(pages, chunk_numbers, candidates=None):
    hs = detect_set_headers(pages)
    chunk = [p for p in pages if p.number in chunk_numbers]
    ctx = chunk_context(chunk, hs, candidates)
    return build_user_message(chunk, ctx), build_user_message(chunk), ctx


def test_chunk_begins_inside_an_open_set():
    pages = [page(1, "Hardware Group No. 03: (Doors U5)", "1 | Hinge | MK"), page(2, "2 | Closer | LCN")]
    msg, plain, ctx = message(pages, {2})
    assert msg.startswith(MARK)
    assert "hardware set '03'" in msg and "[1-1]" in msg and "page 1" in msg
    assert "All lines in this chunk belong to set '03' until another set header appears." in msg
    assert msg.endswith(plain)                                   # everything after the block is unchanged
    assert msg == continuation_block(ctx) + "\n\n" + plain


def test_chunk_begins_on_a_new_set_header_has_no_block():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"), page(2, "Set: 2.0", "1 | Stop | RO")]
    msg, plain, ctx = message(pages, {2})
    assert msg == plain and MARK not in msg and not ctx.is_continuation


def test_new_set_appears_after_carried_continuation_lines():
    pages = [page(1, "Set: 1.0", "1 | Hinge | MK"),
             page(2, "2 | Closer | LCN", "Set: 2.0", "1 | Stop | RO")]
    msg, plain, _ = message(pages, {2})
    assert "before the next set header at [2-2]" in msg and "belong to set '1.0'" in msg
    assert "[2-2] Set: 2.0" in msg                               # the next header stays visible in the source text
    body = msg[msg.index("=== PAGE 2 ==="):]
    assert body.index("[2-1] 2 | Closer") < body.index("[2-2] Set: 2.0") < body.index("[2-3]")  # carried lines first
    assert msg.endswith(plain)


def test_not_used_continuation_is_explicit_and_creates_nothing():
    pages = [page(1, "Set: 3.0 - NOT USED"), page(2, "1 | Hinge | MK")]
    msg, _, ctx = message(pages, {2})
    assert ctx.open_set_not_used
    assert "The open set is marked NOT USED/N/A. Preserve the set (status not_used) even if it contains no " \
           "components; do not invent components for it." in msg


def test_used_set_has_no_not_used_sentence():
    pages = [page(1, "Set: 3.0"), page(2, "1 | Hinge | MK")]
    assert "NOT USED" not in message(pages, {2})[0]


def test_no_previous_set_means_no_block():
    pages = [page(1, "prose only", "Set: 1.0"), page(2, "1 | Hinge | MK")]
    msg, plain, ctx = message(pages, {1})
    assert msg == plain and not ctx.is_continuation


def test_block_is_short_and_source_authoritative():
    pages = [page(1, "Hardware Group No. 21A: (Doors 101, 102 - a very long description of the set header)"),
             page(2, "1 | Hinge | MK")]
    msg, plain, _ = message(pages, {2})
    block = msg[: -len(plain)].strip()
    assert block.count("\n") <= 4 and len(block) < 700           # concise
    assert "very long description" not in block and "Doors 101" not in block   # header text is not dumped
    assert "remain authoritative" in block
    assert "Do not add quantities, components, manufacturers, finishes, catalog numbers or descriptions" in block
    assert not re.search(r"\b(qty|hinge|MK)\b", block, re.I)     # invents no component data


def test_no_block_for_missing_or_incomplete_context():
    assert continuation_block(None) is None
    assert continuation_block(ChunkContext()) is None
    assert continuation_block(ChunkContext(is_continuation=True, open_set_id=None)) is None


def test_restricted_pages_run_still_finds_the_true_preceding_set(furniture_pdf):
    r = build_dry_run(furniture_pdf, pages_hint=[2, 3])          # page 1 (where set 1 opens) is not selected
    c = r.chunks[0]
    assert (c.context.open_set_id, c.context.header_page) == ("1", 1)
    assert c.user_message.startswith(MARK) and "hardware set '1'" in c.user_message and "[1-" in c.user_message
    assert "HARDWARE SET #1" not in c.user_message               # its header is honestly not in the message


def test_full_run_has_block_only_where_needed(furniture_pdf):
    c = build_dry_run(furniture_pdf).chunks[0]                   # single chunk starting on page 1
    assert MARK not in c.user_message and c.context_in_message is False


# ---- the extractor and pipeline really use it (fake client, no network) --------------------------------------
class _Resp:
    stop_reason = "end_turn"
    parsed_output = LLMExtraction(layout=LLMLayout(format="none", columns=[], notes=""), sets=[])


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def parse(self, **kw):
        self.calls.append(kw)
        return _Resp()


def test_extractor_sends_the_block_in_the_user_message():
    pages = [page(1, "Set: 1.0"), page(2, "1 | Hinge | MK")]
    ctx = chunk_context([pages[1]], detect_set_headers(pages))
    client = _FakeClient()
    Extractor(client=client, model="m").extract_chunk([pages[1]], ctx)
    sent = client.calls[0]["messages"][0]["content"]
    assert sent == build_user_message([pages[1]], ctx) and sent.startswith(MARK)


def test_pipeline_passes_context_to_the_extractor(furniture_pdf):
    seen = []

    class Recorder:
        def extract_chunk(self, pages, context=None, resolver=None):
            seen.append((pages[0].number, context))
            return LLMExtraction(layout=LLMLayout(format="none", columns=[], notes=""), sets=[])

    run_pipeline(furniture_pdf, extractor=Recorder(), pages_hint=[2, 3], workers=1)
    (first, ctx), = seen
    assert first == 2 and ctx.is_continuation and ctx.open_set_id == "1"


def test_blank_or_missing_model_env_falls_back_to_the_default(monkeypatch):
    from hwsets.extractor import DEFAULT_MODEL
    monkeypatch.setenv("HWSETS_MODEL", "")
    assert Extractor(client=_FakeClient()).model == DEFAULT_MODEL           # never an empty model name
    monkeypatch.delenv("HWSETS_MODEL")
    assert Extractor(client=_FakeClient()).model == DEFAULT_MODEL
    monkeypatch.setenv("HWSETS_MODEL", "some-model")
    assert Extractor(client=_FakeClient()).model == "some-model"
    assert Extractor(client=_FakeClient(), model="explicit").model == "explicit"
