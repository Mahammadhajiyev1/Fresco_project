"""Stitch sets found in overlapping chunks (and across pages) into one record per set."""
from __future__ import annotations

from .postprocess import finalize_confidence
from .schemas import BBox, Component, HardwareSet, PageLocation


def _line_key(line_id: str) -> tuple[int, int]:
    try:
        p, n = line_id.split("-")
        return int(p), int(n)
    except ValueError:
        return (10**9, 10**9)


def _component_key(c: Component) -> tuple:
    return (c.description.lower(), c.catalog_number, c.qty, c.mfr, c.finish)


def _merge_locations(a: list[PageLocation], b: list[PageLocation]) -> list[PageLocation]:
    by_page: dict[int, PageLocation] = {}
    for loc in a + b:
        cur = by_page.get(loc.page)
        if cur is None:
            by_page[loc.page] = loc.model_copy(deep=True)
        else:
            cur.bbox = BBox.union(cur.bbox, loc.bbox)
            cur.line_ids = list(dict.fromkeys(cur.line_ids + loc.line_ids))
    return [by_page[p] for p in sorted(by_page)]


def _merge_into(base: HardwareSet, other: HardwareSet) -> None:
    seen_lines = {i for c in base.components for i in c.source_line_ids}
    seen_keys = {_component_key(c) for c in base.components}
    for c in other.components:
        overlaps = bool(c.source_line_ids) and any(i in seen_lines for i in c.source_line_ids)
        if overlaps or (not c.source_line_ids and _component_key(c) in seen_keys):
            continue
        base.components.append(c)
        seen_lines.update(c.source_line_ids)
        seen_keys.add(_component_key(c))
    base.locations = _merge_locations(base.locations, other.locations)
    base.description = base.description or other.description
    if other.status == "not_used":
        base.status = "not_used"
    base.warnings = list(dict.fromkeys(base.warnings + other.warnings))


def merge_sets(sets: list[HardwareSet]) -> list[HardwareSet]:
    merged: dict[str, HardwareSet] = {}
    for s in sets:
        key = s.set_number.strip().upper()
        if key in merged:
            _merge_into(merged[key], s)
        else:
            merged[key] = s.model_copy(deep=True)
    out = list(merged.values())
    for s in out:
        s.components.sort(key=lambda c: min((_line_key(i) for i in c.source_line_ids), default=(10**9, 10**9)))
        finalize_confidence(s)
    out.sort(key=lambda s: min(((l.page, l.bbox.y0) for l in s.locations), default=(10**9, 0)))
    return out
