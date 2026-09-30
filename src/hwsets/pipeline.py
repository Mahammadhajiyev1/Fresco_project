"""End-to-end orchestration: PDF -> ExtractionResult."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from .extractor import ExtractionError, Extractor
from .headers import ContextResolver, detect_set_headers
from .ingest import load_pages
from .merge import merge_sets
from .pages import chunk_pages, context_pages, find_candidate_pages, parse_page_spec  # noqa: F401  (re-exported for the API)
from .postprocess import ground_extraction
from .schemas import ExtractionResult, HardwareSet, LLMExtraction, Page

log = logging.getLogger(__name__)


class ChunkExtractor(Protocol):
    """Anything that turns a chunk of pages into an LLMExtraction (the Claude Extractor, the dev mock, test fakes)."""

    def extract_chunk(self, pages: list[Page], context=..., resolver=...) -> LLMExtraction: ...


def _number_gap_warnings(sets: list[HardwareSet]) -> list[str]:
    nums = sorted({int(m.group()) for s in sets if (m := re.match(r"\d+", s.set_number))})
    if len(nums) < 3:
        return []
    missing = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
    return [f"set numbers missing from sequence: {missing}"] if 0 < len(missing) <= 10 else []


def run_pipeline(
    pdf: str | Path | bytes,
    *,
    name: str | None = None,
    extractor: ChunkExtractor | None = None,
    pages_hint: list[int] | None = None,
    chunk_size: int = 4,
    workers: int = 4,
) -> ExtractionResult:
    """Extract every hardware set from a specbook PDF.

    ``pages_hint`` (1-based) bypasses candidate-page detection.
    """
    pages = load_pages(pdf)
    doc_name = name or (Path(pdf).name if not isinstance(pdf, bytes) else "upload.pdf")
    warnings: list[str] = []
    if not any(p.has_text for p in pages):
        return ExtractionResult(
            document=doc_name, total_pages=len(pages), pages_scanned=[], sets=[],
            warnings=["no extractable text layer (scanned PDF?); OCR is not supported"],
        )

    headers = detect_set_headers(pages)
    wanted = pages_hint or find_candidate_pages(pages, headers=headers)
    if not wanted:
        warnings.append("no pages looked like hardware sets")
    by_num = {p.number: p for p in pages}
    scan: list[Page] = [by_num[n] for n in wanted if n in by_num]
    lines = {l.id: l for p in scan for l in p.lines}

    extractor = extractor or Extractor()
    chunks = chunk_pages(scan, size=chunk_size, overlap=1)
    resolve = ContextResolver(pages, headers, context_pages(pages, headers, wanted))  # same rule as the dry run

    def run(chunk: list[Page]) -> tuple[list[Page], LLMExtraction | None, str | None]:
        try:
            return chunk, extractor.extract_chunk(chunk, resolve(chunk), resolver=resolve), None
        except ExtractionError as e:
            return chunk, None, str(e)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunks) or 1))) as pool:
        results = list(pool.map(run, chunks))

    all_sets: list[HardwareSet] = []
    layouts = []
    for chunk, ext, err in results:
        span = f"{chunk[0].number}-{chunk[-1].number}"
        if ext is None:
            warnings.append(f"pages {span}: extraction failed ({err})")
            continue
        layouts.append(ext.layout)
        all_sets.extend(ground_extraction(ext, lines))

    sets = merge_sets(all_sets)
    warnings.extend(_number_gap_warnings(sets))
    return ExtractionResult(
        document=doc_name, total_pages=len(pages), pages_scanned=[p.number for p in scan],
        layouts=layouts, sets=sets, warnings=warnings,
    )
