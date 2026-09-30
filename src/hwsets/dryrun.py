"""Dry run: show EXACTLY what would be sent to the semantic extractor, without any external API call.

Nothing here imports or constructs the Anthropic client, and no API key is read.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .headers import ContextResolver, detect_set_headers
from .ingest import load_pages
from .pages import chunk_pages, context_pages, find_candidate_pages
from .prompts import SYSTEM_PROMPT, build_user_message, continuation_block
from .schemas import BBox, ChunkContext, Page, SetHeader

BANNER = "DRY RUN — NO EXTERNAL API CALLS"


class DryRunLine(BaseModel):
    id: str
    page: int
    original_text: str            # source text as extracted from the PDF
    model_text: str               # what the model would see (private-use glyphs removed)
    bbox: BBox


class DryRunChunk(BaseModel):
    index: int
    pages: list[int]
    context: ChunkContext         # which set is already open when the chunk starts
    context_in_message: bool = False   # True when a CONTINUATION CONTEXT block was prepended to the message
    headers_in_chunk: list[SetHeader]
    user_message: str             # the exact user message the extractor would send
    lines: list[DryRunLine]       # every line shown to the model, with source id + bbox
    hidden_furniture_line_ids: list[str] = Field(default_factory=list)


class DryRunReport(BaseModel):
    banner: str = BANNER
    api_calls_made: int = 0
    document: str
    total_pages: int
    candidate_pages: list[int]
    all_headers: list[SetHeader]
    system_prompt_chars: int
    chunks: list[DryRunChunk]

    def to_text(self) -> str:
        out = [
            self.banner, "=" * len(self.banner),
            f"document: {self.document}   total pages: {self.total_pages}",
            f"candidate pages ({len(self.candidate_pages)}): {self.candidate_pages}",
            f"set headers detected: {len(self.all_headers)}   chunks: {len(self.chunks)}   "
            f"system prompt: {self.system_prompt_chars} chars (unchanged, not shown)",
        ]
        for c in self.chunks:
            ctx = c.context
            out += ["", f"--- CHUNK {c.index}: pages {c.pages[0]}-{c.pages[-1]} " + "-" * 40]
            if ctx.is_continuation:
                out.append(
                    f"[context] chunk starts INSIDE set {ctx.open_set_id!r} (header {ctx.header_line_id} on page "
                    f"{ctx.header_page})"
                    + (" [NOT USED]" if ctx.open_set_not_used else "")
                    + (f"; carried-over lines end before {ctx.continues_until_line_id}" if ctx.continues_until_line_id else "")
                    + ("; continuation block IS in the message" if c.context_in_message else "")
                )
            else:
                out.append("[context] chunk starts on a fresh set (or no set is open)")
            out.append("[headers] " + ", ".join(f"{h.set_number}@{h.line_id}" + ("(NOT USED)" if h.not_used else "")
                                                for h in c.headers_in_chunk))
            out += ["[user message sent to the model]", c.user_message]
        out += ["", self.banner]
        return "\n".join(out)


def build_dry_run(
    pdf: str | Path | bytes,
    *,
    name: str | None = None,
    pages_hint: list[int] | None = None,
    chunk_size: int = 4,
    overlap: int = 1,
) -> DryRunReport:
    pages = load_pages(pdf)
    headers = detect_set_headers(pages)
    candidates = pages_hint or find_candidate_pages(pages, headers=headers)
    by_num = {p.number: p for p in pages}
    scan: list[Page] = [by_num[n] for n in candidates if n in by_num]
    resolve = ContextResolver(pages, headers, context_pages(pages, headers, candidates))  # --pages can't hide a set

    chunks: list[DryRunChunk] = []
    for i, chunk in enumerate(chunk_pages(scan, size=chunk_size, overlap=overlap), start=1):
        nums = [p.number for p in chunk]
        ctx = resolve(chunk)
        chunks.append(DryRunChunk(
            index=i, pages=nums, context=ctx,
            context_in_message=continuation_block(ctx) is not None,
            headers_in_chunk=[h for h in headers if h.page in set(nums)],
            user_message=build_user_message(chunk, ctx),
            lines=[
                DryRunLine(id=l.id, page=l.page, original_text=l.text, model_text=l.clean, bbox=l.bbox)
                for p in chunk for l in p.lines if not l.furniture and l.clean.strip()
            ],
            hidden_furniture_line_ids=[l.id for p in chunk for l in p.lines if l.furniture],
        ))
    doc = name or (Path(pdf).name if not isinstance(pdf, bytes) else "upload.pdf")
    return DryRunReport(
        document=doc, total_pages=len(pages), candidate_pages=sorted(candidates), all_headers=headers,
        system_prompt_chars=len(SYSTEM_PROMPT), chunks=chunks,
    )
