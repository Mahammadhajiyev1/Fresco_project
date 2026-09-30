"""Deterministic post-processing of Claude output: grounding, bboxes, mfr/finish cross-checks."""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean

from .codes import check_mfr_finish, value_conflict
from .schemas import (
    BBox, Component, HardwareSet, Line, LLMComponent, LLMExtraction, PageLocation,
)

LineIndex = dict[str, Line]


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _present(value: str, text: str) -> bool:
    """Is ``value`` really printed in ``text``? Contiguous match, or - for a cell that wraps over several printed
    lines, where other columns sit between the pieces - every token of the value, in order."""
    if not _alnum(value):                       # a placeholder such as '---' has no letters or digits: it must appear literally
        return bool(value.strip()) and value.strip() in text
    if _alnum(value) in _alnum(text):
        return True
    hay, pos = text.lower(), 0
    for tok in value.lower().split():
        pos = hay.find(tok, pos)
        if pos < 0:
            return False
        pos += len(tok)
    return True


def locate(line_ids: list[str], lines: LineIndex) -> list[PageLocation]:
    """Union bbox per page for the given line ids (unknown ids and page furniture ignored)."""
    by_page: dict[int, list[Line]] = defaultdict(list)
    for lid in dict.fromkeys(line_ids):
        if lid in lines and not lines[lid].furniture:
            by_page[lines[lid].page].append(lines[lid])
    out = []
    for page, ls in sorted(by_page.items()):
        box: BBox = ls[0].bbox
        for l in ls[1:]:
            box = box.union(l.bbox)
        out.append(PageLocation(page=page, bbox=box, line_ids=[l.id for l in ls]))
    return out


def _ground_component(c: LLMComponent, lines: LineIndex) -> Component:
    comp = Component(
        qty=c.qty, description=c.description.strip(), catalog_number=c.catalog_number,
        mfr=c.mfr, finish=c.finish, notes=c.notes,
        confidence=max(0.0, min(1.0, c.confidence)), source_line_ids=list(c.source_line_ids),
    )
    src = [lines[i] for i in c.source_line_ids if i in lines]
    penalty = 0.0
    if not src:
        comp.warnings.append("no valid source lines")
        penalty += 0.3
    else:
        text = " ".join(l.clean for l in src)
        if c.catalog_number and not _present(c.catalog_number, text):
            comp.warnings.append(f"catalog number {c.catalog_number!r} not found in source lines")
            penalty += 0.25
        if c.qty is not None and not re.search(rf"(?<!\d){c.qty}(?!\d)", text):
            comp.warnings.append(f"quantity {c.qty} not present in source lines; set to null")
            comp.qty = None
            penalty += 0.2
        for field in ("mfr", "finish"):
            v = getattr(comp, field)
            if v and not _present(v, text):
                comp.warnings.append(f"{field} {v!r} not found in source lines")
                penalty += 0.2
    comp.confidence = max(0.05, comp.confidence - penalty)
    return comp


def fix_mfr_finish(sets: list[HardwareSet]) -> None:
    """Cross-check mfr/finish assignment across one chunk's components (in place).

    Column-level: if the whole mfr column reads as finishes and vice versa, swap everywhere.
    Row-level: swap a row only when its mfr is an unambiguous finish AND its finish an unambiguous mfr.
    Otherwise flag and lower confidence - ambiguous codes are never overridden from a single value.
    """
    comps = [c for s in sets for c in s.components]
    verdict, msgs = check_mfr_finish([c.mfr for c in comps], [c.finish for c in comps])
    if verdict == "swapped":
        for c in comps:
            c.mfr, c.finish = c.finish, c.mfr
            c.warnings.append("mfr/finish swapped (column-level check)")
            c.confidence = min(c.confidence, 0.8)
        return
    for c in comps:
        bad_mfr = value_conflict(c.mfr, "mfr")
        bad_fin = value_conflict(c.finish, "finish")
        if bad_mfr and bad_fin:
            c.mfr, c.finish = c.finish, c.mfr
            c.warnings.append("mfr/finish swapped (row-level check)")
            c.confidence = min(c.confidence, 0.7)
        elif bad_mfr or bad_fin:
            which = "mfr" if bad_mfr else "finish"
            c.warnings.append(f"{which} value {getattr(c, which)!r} looks like the other column")
            c.confidence = min(c.confidence, 0.5)


def ground_extraction(ext: LLMExtraction, lines: LineIndex) -> list[HardwareSet]:
    out: list[HardwareSet] = []
    for s in ext.sets:
        comps = [_ground_component(c, lines) for c in s.components]
        ids = list(s.header_line_ids) + [i for c in comps for i in c.source_line_ids]
        hs = HardwareSet(
            set_number=s.set_number.strip(), description=(s.description or None),
            status=s.status, components=comps, locations=locate(ids, lines),
        )
        if not hs.locations:
            hs.warnings.append("no locatable source lines")
        out.append(hs)
    fix_mfr_finish(out)
    for hs in out:
        finalize_confidence(hs)
    return out


def finalize_confidence(hs: HardwareSet) -> None:
    if hs.components:
        hs.confidence = round(mean(c.confidence for c in hs.components), 3)
    else:
        hs.confidence = 1.0 if hs.status == "not_used" else 0.4
        if hs.status == "active" and "active set with no components" not in hs.warnings:
            hs.warnings.append("active set with no components")
    if not hs.locations:
        hs.confidence = min(hs.confidence, 0.3)
