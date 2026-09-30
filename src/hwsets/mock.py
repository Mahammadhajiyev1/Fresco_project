"""DEV-ONLY stand-in for the Claude extractor (HWSETS_MOCK=1). NOT real extraction.

Splits pages at deterministic set-header lines and turns pipe-separated rows into components with only
qty + first text cell. It never classifies mfr/finish/catalog. Its purpose is to let the API and UI be
exercised end-to-end (locations, bboxes, editing) on real PDFs without an API key.
"""
from __future__ import annotations

from .headers import parse_header
from .schemas import LLMComponent, LLMExtraction, LLMLayout, LLMSet, Page

MOCK_WARNING = "MOCK MODE: sets come from a deterministic header regex, not from Claude. Not real extraction."


class MockExtractor:
    def extract_chunk(self, pages: list[Page], context=None, resolver=None) -> LLMExtraction:
        sets: list[LLMSet] = []
        for page in pages:
            for line in (l for l in page.lines if not l.furniture):
                parsed = parse_header(line.clean)
                if parsed:
                    number, desc, not_used = parsed
                    sets.append(LLMSet(
                        set_number=number, description=desc,
                        status="not_used" if not_used else "active",
                        header_line_ids=[line.id], components=[],
                        continues_from_previous_page=False, continues_on_next_page=False))
                elif sets and " | " in line.clean:
                    cells = line.clean.split(" | ")
                    qty = int(cells[0]) if cells[0].isdigit() else None
                    cells = cells[1:] if qty is not None else cells
                    sets[-1].components.append(LLMComponent(
                        qty=qty, description=(cells[0] if cells else line.clean), catalog_number=None,
                        mfr=None, finish=None, notes=None, source_line_ids=[line.id], confidence=0.2))
        layout = LLMLayout(format="none", columns=[], notes="MOCK - no layout inference")
        return LLMExtraction(layout=layout, sets=sets)
