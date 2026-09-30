"""Claude structured-output extraction for a chunk of pages."""
from __future__ import annotations

import logging
import os

import anthropic
import pydantic

from . import client_headers, require_api_key
from .prompts import SYSTEM_PROMPT, build_user_message
from .schemas import ChunkContext, LLMExtraction, Page

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000


class ExtractionError(RuntimeError):
    pass


class Extractor:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None):
        if client is None:
            require_api_key()            # ConfigurationError (clear message) instead of an SDK traceback
            headers = client_headers()
            client = anthropic.Anthropic(default_headers=headers) if headers else anthropic.Anthropic()
        self.client = client
        self.model: str = model or os.getenv("HWSETS_MODEL") or DEFAULT_MODEL

    def extract_chunk(
        self, pages: list[Page], context: ChunkContext | None = None, resolver=None
    ) -> LLMExtraction:
        """Extract sets from consecutive pages. Splits the chunk in two if the output is truncated.

        ``resolver(pages) -> ChunkContext`` (a ``ContextResolver``) recomputes which set is open at the start of
        each derived half. Without one, the first half can safely reuse the parent's context (same first line)
        but the second half gets none - never a stale copy.
        """
        try:
            return self._call(pages, context)
        except ExtractionError:
            if len(pages) < 2:
                raise
            mid = len(pages) // 2
            first, second = pages[:mid], pages[mid:]
            log.warning("chunk %s-%s failed; retrying as two halves", pages[0].number, pages[-1].number)
            a = self.extract_chunk(first, resolver(first) if resolver else context, resolver)
            b = self.extract_chunk(second, resolver(second) if resolver else None, resolver)
            return LLMExtraction(layout=a.layout, sets=a.sets + b.sets)

    def _call(self, pages: list[Page], context: ChunkContext | None) -> LLMExtraction:
        user = build_user_message(pages, context)
        try:
            resp = self.client.messages.parse(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=LLMExtraction,
            )
        except pydantic.ValidationError as e:  # truncated / malformed JSON
            raise ExtractionError(f"invalid structured output: {e.error_count()} errors") from e
        if resp.stop_reason == "refusal":
            raise ExtractionError("model refused the request")
        if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            raise ExtractionError(f"no complete structured output (stop_reason={resp.stop_reason})")
        return resp.parsed_output
