"""Normalisation of text for MODEL-FACING use only. Source text is never modified."""
from __future__ import annotations

import re
import unicodedata

_PIPES = re.compile(r"(?:\s*\|\s*){2,}")


def strip_private_use(text: str) -> str:
    """Remove private-use code points (icon-font glyphs such as U+F09D) and tidy the seams.

    Only Unicode category ``Co`` is removed - ordinary Unicode (accents, dashes, symbols) is untouched.
    A cell that contained nothing but a glyph collapses instead of leaving an empty "|  |" column.
    """
    if not any(unicodedata.category(c) == "Co" for c in text):
        return text
    t = "".join(c for c in text if unicodedata.category(c) != "Co")
    t = _PIPES.sub(" | ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"^\s*\|\s*|\s*\|\s*$", "", t).strip()


def normalize_for_model(text: str) -> str:
    return strip_private_use(text)
