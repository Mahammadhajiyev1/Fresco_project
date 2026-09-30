"""Manufacturer / finish code lexicons and context-aware classification.

A single token is never trusted on its own: ambiguous tokens ("PE", "NO", ...) are
judged by what the rest of their column looks like (see ``column_profile``).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Literal

Kind = Literal["mfr", "finish", "ambiguous", "unknown"]

# Common door-hardware manufacturer codes / short names (upper-case).
MFR_CODES = {
    "IVES", "IV", "HAGER", "HAG", "STANLEY", "STA", "MCKINNEY", "MK", "MCK", "LCN", "SCH", "SCHLAGE",
    "VON", "VD", "VON DUPRIN", "YALE", "YA", "SARGENT", "SAR", "SA", "CORBIN", "CR", "RUSSWIN", "RUS",
    "ROCKWOOD", "RO", "ROC", "GLYNN", "GJ", "GLYNN-JOHNSON", "ZERO", "ZER", "NGP", "REESE", "RE", "HES",
    "ADAMS RITE", "AR", "DORMA", "DOR", "BEST", "OCC", "TRIMCO", "TRI", "BOMMER", "BOM", "ABH",
    "PEMKO", "PEM", "NORTON", "NOR", "ARCHITECTURAL", "DET", "DETEX", "SDC", "SECURITRON", "SEC",
    "ALLEGION", "BALDWIN", "BAL", "DEL", "DELTANA", "DON-JO", "DJ", "GEZE", "JACKSON", "KAB", "KN",
    "LOCKNETICS", "LOC", "MARKAR", "NATIONAL GUARD", "NG", "SELECT", "SLI", "SOSS", "TELKEE", "TK",
    "WILLIAMS", "WIL", "HAGAR", "FALCON", "FAL", "ASSA", "ABLOY", "CAL", "CALIFORNIA", "MMC", "PRECISION",
    "PRE", "APEX", "CON-TECH", "CT", "ROSS", "HAGER COMPANIES", "MCK", "MIL", "MILLER", "WESTERN",
}

# Finish codes that are not purely numeric / US-prefixed.
FINISH_WORDS = {
    "BSP", "BLK", "BLACK", "ANOD", "AL", "ALUM", "SP", "SS", "DKB", "DB", "DKBRZ", "PC", "PAINT", "PTD",
    "PAINTED", "SATIN", "POLISHED", "BRASS", "BRZ", "BRONZE", "CHR", "CHROME", "GALV", "PRIME", "PRIMED",
    "PRIM", "CLR", "CLEAR", "MILL", "MF", "STL", "STAINLESS", "NIC", "NICKEL", "BRIGHT", "SATCHR", "USP",
}
_FINISH_PATTERNS = [
    re.compile(r"^US\d{1,2}[A-Z]{0,2}$"),        # US26D, US32D, US10B
    re.compile(r"^\d{3}[A-Z]?$"),                # 626, 630, 652, 689, 643e
    re.compile(r"^BHMA\s*\d{3}$"),
]

# Tokens that are legitimately both a manufacturer and a finish (or a plain word). They never
# vote in column profiling and are always resolved from their column.
AMBIGUOUS = {"PE", "NO", "AR", "AL", "SP", "DB", "PC", "SS", "NG", "CT", "RE", "RO", "SA", "CR"}


def _norm(tok: str) -> str:
    return re.sub(r"[^\w\- ]", "", tok.strip().upper())


def classify_token(tok: str | None) -> Kind:
    if not tok:
        return "unknown"
    t = _norm(tok)
    if not t:
        return "unknown"
    if t in AMBIGUOUS:
        return "ambiguous"
    if any(p.match(t) for p in _FINISH_PATTERNS) or t in FINISH_WORDS:
        return "finish"
    if t in MFR_CODES:
        return "mfr"
    return "unknown"


class ColumnProfile:
    """Vote counts of a column's values: how mfr-like vs finish-like it is."""

    def __init__(self, values: list[str | None]):
        c = Counter(classify_token(v) for v in values if v)
        self.mfr, self.finish, self.ambiguous, self.unknown = (
            c["mfr"], c["finish"], c["ambiguous"], c["unknown"],
        )

    @property
    def role(self) -> Literal["mfr", "finish", "unclear"]:
        decisive = self.mfr + self.finish
        if decisive < 2:
            return "unclear"
        if self.mfr >= 0.7 * decisive:
            return "mfr"
        if self.finish >= 0.7 * decisive:
            return "finish"
        return "unclear"


def column_profile(values: list[str | None]) -> ColumnProfile:
    return ColumnProfile(values)


def check_mfr_finish(
    mfr_values: list[str | None], finish_values: list[str | None]
) -> tuple[str, list[str]]:
    """Cross-check the two columns of a document.

    Returns ``(verdict, messages)`` where verdict is:
      ``ok``      columns look right
      ``swapped`` mfr column reads as finish and vice versa -> caller should swap
      ``unclear`` not enough evidence
    """
    m, f = column_profile(mfr_values), column_profile(finish_values)
    msgs: list[str] = []
    if m.role == "finish" and f.role == "mfr":
        return "swapped", [f"mfr column looks like finishes ({m.finish}/{m.mfr + m.finish}) and finish column looks like manufacturers"]
    if m.role == "mfr" and f.role == "finish":
        return "ok", msgs
    if m.role == "finish":
        msgs.append("mfr column looks like finish codes")
    if f.role == "mfr":
        msgs.append("finish column looks like manufacturer codes")
    return ("unclear" if msgs or "unclear" in (m.role, f.role) else "ok"), msgs


def value_conflict(value: str | None, expected: Literal["mfr", "finish"]) -> bool:
    """True if an *unambiguous* value clearly belongs to the other column."""
    k = classify_token(value)
    return k in ("mfr", "finish") and k != expected
