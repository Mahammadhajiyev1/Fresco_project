"""Pydantic models.

Two families:
* ``LLM*``  - what Claude returns. References source text by line id; never coordinates.
* the rest - the final, grounded output (bounding boxes computed from PDF geometry).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Geometry / ingest
# --------------------------------------------------------------------------- #
class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    def union(self, other: "BBox") -> "BBox":
        return BBox(
            x0=min(self.x0, other.x0), y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1), y1=max(self.y1, other.y1),
        )


class Word(BaseModel):
    text: str
    bbox: BBox


class Line(BaseModel):
    id: str            # "<page>-<n>", e.g. "12-7"
    page: int          # 1-based
    text: str          # ORIGINAL words joined; large horizontal gaps rendered as " | "
    model_text: str | None = None   # normalised copy for the model (private-use glyphs removed); None = same
    bbox: BBox
    words: list[Word] = Field(default_factory=list, exclude=True)
    furniture: bool = False   # repeated running header/footer; kept for traceability, hidden from the model

    @property
    def clean(self) -> str:
        """Model-facing text (falls back to the original when no normalised copy was made)."""
        return self.text if self.model_text is None else self.model_text


class Page(BaseModel):
    number: int        # 1-based
    width: float
    height: float
    lines: list[Line]

    @property
    def has_text(self) -> bool:
        return sum(len(l.text) for l in self.lines) > 40


class SetHeader(BaseModel):
    """A deterministically detected set-header line (not an extraction result)."""
    set_number: str
    description: str | None = None   # text after the identifier, cleaned
    not_used: bool = False
    text: str                        # full source line text
    page: int
    line_id: str
    bbox: BBox


class ChunkContext(BaseModel):
    """Which set is already open when a chunk begins (derived from deterministic header order)."""
    is_continuation: bool = False
    open_set_id: str | None = None
    open_set_header_text: str | None = None
    open_set_not_used: bool = False
    header_page: int | None = None
    header_line_id: str | None = None
    continues_until_line_id: str | None = None   # first header inside the chunk; the carried-over lines precede it


# --------------------------------------------------------------------------- #
# LLM-facing schema (kept free of numeric constraints for structured outputs)
# --------------------------------------------------------------------------- #
ColumnRole = Literal["qty", "description", "catalog_number", "mfr", "finish", "notes", "other"]


class LLMColumn(BaseModel):
    header: str = Field(description="Column header text as printed, or '' if none")
    role: ColumnRole
    sample_values: list[str] = Field(
        description="Up to 4 example cell values seen in this column; evidence for the role"
    )


class LLMLayout(BaseModel):
    format: Literal["table", "list", "mixed", "none"] = Field(
        description="Layout of the hardware-set content in these pages"
    )
    columns: list[LLMColumn] = Field(
        description="Component columns in left-to-right order (empty for free-text lists)"
    )
    notes: str = Field(description="One sentence on how mfr and finish are distinguished here")


class LLMComponent(BaseModel):
    qty: int | None = Field(description="Quantity exactly as printed; null when absent")
    description: str
    catalog_number: str | None
    mfr: str | None = Field(description="Manufacturer code/name from the manufacturer column")
    finish: str | None = Field(description="Finish code/name from the finish column")
    notes: str | None
    source_line_ids: list[str] = Field(description="Ids of the source lines this component came from")
    confidence: float = Field(description="0-1 self-assessed confidence in mfr/finish/qty mapping")


class LLMSet(BaseModel):
    set_number: str = Field(description='Identifier as printed, without the word "SET" or "#", e.g. "1", "3A", "15"')
    description: str | None
    status: Literal["active", "not_used"]
    header_line_ids: list[str] = Field(description="Line ids of the set header (number + title)")
    components: list[LLMComponent]
    continues_from_previous_page: bool = Field(
        description="True if this set began on an earlier page than the first page shown"
    )
    continues_on_next_page: bool = Field(
        description="True if the set appears to continue beyond the last page shown"
    )


class LLMExtraction(BaseModel):
    layout: LLMLayout
    sets: list[LLMSet]


# --------------------------------------------------------------------------- #
# Final output
# --------------------------------------------------------------------------- #
class PageLocation(BaseModel):
    page: int
    bbox: BBox
    line_ids: list[str]


class Component(BaseModel):
    qty: int | None = None
    description: str
    catalog_number: str | None = None
    mfr: str | None = None
    finish: str | None = None
    notes: str | None = None
    confidence: float = 1.0
    source_line_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HardwareSet(BaseModel):
    set_number: str
    description: str | None = None
    status: Literal["active", "not_used"] = "active"
    locations: list[PageLocation] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    confidence: float = 1.0
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    document: str
    total_pages: int
    pages_scanned: list[int]
    layouts: list[LLMLayout] = Field(default_factory=list)
    sets: list[HardwareSet]
    warnings: list[str] = Field(default_factory=list)
