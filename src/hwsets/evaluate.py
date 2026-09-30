"""Offline evaluation of extracted JSON against a small, HAND-LABELLED ground truth. No model, no network.

    python -m hwsets.evaluate --expected data/ground_truth/book.json --actual outputs/book.json [--json report.json]
    python -m hwsets.evaluate --scaffold data/ground_truth/book.json --pdf data/book.pdf [--pages 40-52] [--limit 5] [--sets 05,06]

Ground truth is MANUAL. A field that is absent from the ground truth is UNLABELLED and is never scored;
an explicit ``null`` is a real expectation (e.g. "this row has no quantity"). See data/ground_truth/README.md.

Normalisation (values are compared normalised; the report always shows the ORIGINAL values):
  text      NFKC, private-use glyphs removed, curly quotes/dashes -> ASCII, case-folded, whitespace collapsed
  set no.   the text rule; "1" and "01" are DIFFERENT (reported as a near-match warning, never matched)
  mfr/finish the text rule, plus surrounding ".,;:" stripped
  qty       integers ("2" == 2); null only equals null
  ""        equals null

Component matching is strict, per matched set, using only LABELLED fields, in tiers:
  A  catalog_number and description both equal      B  catalog_number equal      C  description equal
Ties resolve in document order. There is no fuzzy matching; unmatched rows are reported as missing / extra.
No single overall score is computed on purpose - read the individual metrics.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .textnorm import strip_private_use

COMPONENT_FIELDS = ("qty", "description", "catalog_number", "mfr", "finish", "notes")
PLACEHOLDER = "<FILL IN>"
MANUAL_BANNER = "MANUAL GROUND TRUTH"
_QUOTES = str.maketrans({
    "–": "-", "—": "-", "−": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
})


# --------------------------------------------------------------------------- ground-truth schema
class ExpectedComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qty: int | None = None
    description: str | None = None
    catalog_number: str | None = None
    mfr: str | None = None
    finish: str | None = None
    notes: str | None = None

    def labelled(self) -> list[str]:
        """Fields the human labelled (present in the file - null counts)."""
        return [f for f in COMPONENT_FIELDS if f in self.model_fields_set]


class ExpectedSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set_number: str
    description: str | None = None
    status: Literal["active", "not_used"] | None = None
    pages: list[int] | None = None                       # pages the set occupies (checked against locations)
    components: list[ExpectedComponent] | None = None    # absent = components not labelled for this set
    components_complete: bool = True                     # False = the listed components are only some of the rows


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: str
    verified: bool = False                # a human must set this to true after checking every entry
    labelled_by: str | None = None
    sets_complete: bool = False           # True = every set in the document is listed, so extra sets are errors
    sets: list[ExpectedSet]


def _strip_comments(obj: Any) -> Any:
    """Drop keys starting with '_' (labeller notes / scaffold hints) at any depth."""
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def load_ground_truth(path: str | Path, *, allow_unverified: bool = False) -> GroundTruth:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    gt = GroundTruth.model_validate(_strip_comments(raw))
    if any(PLACEHOLDER in s.set_number for s in gt.sets):
        raise ValueError(f"{path}: contains unfilled {PLACEHOLDER} entries - finish labelling first")
    if not gt.verified and not allow_unverified:
        raise ValueError(
            f"{path}: 'verified' is not true. Ground truth must be checked by a human before it is used "
            "(pass --allow-unverified only to try the tool)."
        )
    return gt


# --------------------------------------------------------------------------- normalisation
def norm_text(v: Any) -> str | None:
    if v is None:
        return None
    s = unicodedata.normalize("NFKC", strip_private_use(str(v))).translate(_QUOTES).casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def norm_code(v: Any) -> str | None:
    s = norm_text(v)
    if s is None:
        return None
    return s.strip(".,;:") or None


def norm_qty(v: Any) -> int | str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)) and float(v) == int(v):
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
        return int(v)
    return str(v)


_NORM = {"qty": norm_qty, "description": norm_text, "catalog_number": norm_text,
         "mfr": norm_code, "finish": norm_code, "notes": norm_text}


def _n(field: str, value: Any):
    return _NORM[field](value)


# --------------------------------------------------------------------------- matching
def _tiers():
    return (("A", ("catalog_number", "description")), ("B", ("catalog_number",)), ("C", ("description",)))


def match_components(exp: list[ExpectedComponent], act: list[dict]) -> dict[str, Any]:
    """Strict tiered matching. Returns pairs [(exp_idx, act_idx, tier)], unmatched indices, out-of-order count."""
    used_a: set[int] = set()
    pairs: list[tuple[int, int, str]] = []
    matched_e: set[int] = set()
    out_of_order = 0
    for tier, fields in _tiers():
        last = -1
        for ei, e in enumerate(exp):
            if ei in matched_e:
                continue
            lab = e.labelled()
            if not all(f in lab and getattr(e, f) is not None for f in fields):
                continue
            ekey = tuple(_n(f, getattr(e, f)) for f in fields)
            cands = [ai for ai, a in enumerate(act)
                     if ai not in used_a and tuple(_n(f, a.get(f)) for f in fields) == ekey]
            if not cands:
                continue
            ai = next((c for c in cands if c > last), cands[0])
            if ai < last:
                out_of_order += 1
            last = max(last, ai)
            used_a.add(ai)
            matched_e.add(ei)
            pairs.append((ei, ai, tier))
    return {
        "pairs": sorted(pairs),
        "missing": [i for i in range(len(exp)) if i not in matched_e],
        "extra": [i for i in range(len(act)) if i not in used_a],
        "out_of_order": out_of_order,
    }


def _acc(labelled: int, correct: int) -> float | None:
    return round(correct / labelled, 4) if labelled else None


def _row(c: dict | ExpectedComponent) -> dict:
    d = c.model_dump() if isinstance(c, ExpectedComponent) else {f: c.get(f) for f in COMPONENT_FIELDS}
    return {f: d.get(f) for f in COMPONENT_FIELDS}


def _pages_of(s: dict) -> set[int]:
    return {int(loc["page"]) for loc in (s.get("locations") or []) if isinstance(loc, dict) and loc.get("page")}


# --------------------------------------------------------------------------- evaluation
def evaluate(gt: GroundTruth, actual: dict) -> dict:
    """Score ``actual`` (our ExtractionResult JSON, or the API's {"result": ...}) against ``gt``."""
    if "result" in actual and "sets" not in actual:
        actual = actual["result"]
    act_sets: list[dict] = list(actual.get("sets") or [])
    warnings: list[str] = []

    # ---- sets
    by_key: dict[str | None, dict] = {}
    for s in act_sets:
        k = norm_text(s.get("set_number"))
        if k in by_key:
            warnings.append(f"duplicate extracted set number {s.get('set_number')!r}: only the first is scored")
        else:
            by_key[k] = s
    matched: list[tuple[ExpectedSet, dict]] = []
    missing_sets: list[str] = []
    for es in gt.sets:
        a = by_key.get(norm_text(es.set_number))
        (matched.append((es, a)) if a is not None else missing_sets.append(es.set_number))
    matched_keys = {norm_text(es.set_number) for es, _ in matched}
    extra_sets = [s.get("set_number") for s in act_sets if norm_text(s.get("set_number")) not in matched_keys]

    near = []
    stripz = lambda v: re.sub(r"^(set|group|hw)?[\s#:.\-]*0*(?=\S)", "", norm_text(v) or "")
    for miss in missing_sets:
        for x in extra_sets:
            if stripz(miss) == stripz(x):
                near.append({"expected": miss, "extracted": x})
                warnings.append(f"set number {miss!r} vs extracted {x!r} differ only by leading zeros/prefix: NOT matched")

    set_field = {"description": [0, 0], "status": [0, 0], "pages": [0, 0]}
    set_mismatches: list[dict] = []
    for es, a in matched:
        for f in ("description", "status", "pages"):
            if f not in es.model_fields_set:
                continue                                  # unlabelled: never scored
            if f in ("status", "pages") and getattr(es, f) is None:
                continue                                  # null is only a meaningful expectation for description
            set_field[f][0] += 1
            if f == "description":
                ok = norm_text(es.description) == norm_text(a.get("description"))
            elif f == "status":
                ok = es.status == a.get("status")
            else:
                ok = set(es.pages or []) == _pages_of(a)
            set_field[f][1] += ok
            if not ok:
                got = sorted(_pages_of(a)) if f == "pages" else a.get(f)
                set_mismatches.append({"set": es.set_number, "field": f, "expected": getattr(es, f), "actual": got})

    # ---- components
    exp_total = matched_total = 0
    exp_in_matched = 0
    tiers = {"A": 0, "B": 0, "C": 0}
    out_of_order = 0
    field_stats = {f: {"labelled": 0, "correct": 0, "identity_pairs": 0, "identity_correct": 0} for f in COMPONENT_FIELDS}
    missing_comps: list[dict] = []
    extra_comps: list[dict] = []
    field_mismatches: list[dict] = []
    swaps: list[dict] = []
    possible_swaps: list[dict] = []
    sets_with_components = 0

    for es in gt.sets:
        if es.components is not None and es.set_number in missing_sets:
            exp_total += len(es.components)
            missing_comps += [{"set": es.set_number, "expected": _row(c), "reason": "set not extracted"}
                              for c in es.components]
    for es, a in matched:
        if es.components is None:
            continue
        sets_with_components += 1
        acts = list(a.get("components") or [])
        exp_total += len(es.components)
        exp_in_matched += len(es.components)
        m = match_components(es.components, acts)
        out_of_order += m["out_of_order"]
        matched_total += len(m["pairs"])
        for ei, ai, tier in m["pairs"]:
            tiers[tier] += 1
            e, c = es.components[ei], acts[ai]
            identity = {"A": ("catalog_number", "description"), "B": ("catalog_number",), "C": ("description",)}[tier]
            for f in e.labelled():
                ok = _n(f, getattr(e, f)) == _n(f, c.get(f))
                st = field_stats[f]
                st["labelled"] += 1
                st["correct"] += ok
                if f in identity:
                    st["identity_pairs"] += 1
                    st["identity_correct"] += ok
                if not ok:
                    field_mismatches.append({"set": es.set_number, "row": ei + 1, "field": f,
                                             "expected": getattr(e, f), "actual": c.get(f)})
            lab = e.labelled()
            if "mfr" in lab and "finish" in lab:
                em, ef, am, af = _n("mfr", e.mfr), _n("finish", e.finish), _n("mfr", c.get("mfr")), _n("finish", c.get("finish"))
                info = {"set": es.set_number, "row": ei + 1, "expected_mfr": e.mfr, "expected_finish": e.finish,
                        "actual_mfr": c.get("mfr"), "actual_finish": c.get("finish")}
                if em and ef and em != ef and am == ef and af == em:
                    swaps.append(info)
                elif (em and af == em and am != em) or (ef and am == ef and af != ef):
                    possible_swaps.append(info)
        for ei in m["missing"]:
            missing_comps.append({"set": es.set_number, "row": ei + 1, "expected": _row(es.components[ei]),
                                  "closest_unmatched_actual": _closest(es.components[ei], [acts[i] for i in m["extra"]])})
        if es.components_complete:
            extra_comps += [{"set": es.set_number, "actual": _row(acts[i])} for i in m["extra"]]

    if out_of_order:
        warnings.append(f"{out_of_order} matched component(s) appear in a different order than labelled")
    for s in swaps:
        warnings.append(f"mfr/finish SWAPPED in set {s['set']} row {s['row']}: expected mfr={s['expected_mfr']!r} "
                        f"finish={s['expected_finish']!r}, got mfr={s['actual_mfr']!r} finish={s['actual_finish']!r}")
    for s in possible_swaps:
        warnings.append(f"possible mfr/finish swap in set {s['set']} row {s['row']} (one value sits in the other column)")
    if missing_sets:
        warnings.append(f"missing sets: {missing_sets}")
    if extra_sets and gt.sets_complete:
        warnings.append(f"extra (unexpected) sets: {extra_sets}")
    if missing_comps:
        warnings.append(f"{len(missing_comps)} expected component(s) not found")
    if field_mismatches:
        warnings.append(f"{len(field_mismatches)} labelled field value(s) differ on matched components")

    fields = {}
    for f, st in field_stats.items():
        ex_lab = st["labelled"] - st["identity_pairs"]
        ex_ok = st["correct"] - st["identity_correct"]
        fields[f] = {
            "labelled": st["labelled"], "correct": st["correct"], "accuracy": _acc(st["labelled"], st["correct"]),
            "identity_pairs": st["identity_pairs"],
            "accuracy_excluding_identity": _acc(ex_lab, ex_ok),
        }
    return {
        "document": gt.document,
        "ground_truth": {"verified": gt.verified, "labelled_by": gt.labelled_by, "sets_complete": gt.sets_complete},
        "sets": {
            "expected": len(gt.sets), "extracted": len(act_sets), "matched": len(matched),
            "recall": _acc(len(gt.sets), len(matched)),
            "missing": missing_sets, "extra": extra_sets,
            "extra_are_errors": gt.sets_complete,
            "unlabelled_extracted": 0 if gt.sets_complete else len(extra_sets),
            "near_matches": near,
            "description": {"labelled": set_field["description"][0], "correct": set_field["description"][1],
                            "accuracy": _acc(*set_field["description"])},
            "status": {"labelled": set_field["status"][0], "correct": set_field["status"][1],
                       "accuracy": _acc(*set_field["status"])},
            "pages": {"labelled": set_field["pages"][0], "correct": set_field["pages"][1],
                      "accuracy": _acc(*set_field["pages"])},
            "mismatches": set_mismatches,
        },
        "components": {
            "sets_with_labelled_components": sets_with_components,
            "expected": exp_total, "matched": matched_total, "recall": _acc(exp_total, matched_total),
            "expected_in_matched_sets": exp_in_matched, "recall_in_matched_sets": _acc(exp_in_matched, matched_total),
            "matched_by_tier": tiers, "out_of_order": out_of_order,
            "missing": missing_comps, "extra_in_fully_labelled_sets": extra_comps,
        },
        "fields": fields,
        "mfr_finish": {"swaps": len(swaps), "possible_swaps": len(possible_swaps),
                       "swap_details": swaps, "possible_swap_details": possible_swaps},
        "field_mismatches": field_mismatches,
        "warnings": warnings,
        "note": "No overall score is computed on purpose: each metric above has its own denominator (see module docs).",
    }


def _closest(e: ExpectedComponent, candidates: list[dict]) -> dict | None:
    """Informational only: the unmatched extracted row sharing the most labelled values (needs >= 2). Never scored."""
    best, best_n = None, 1
    for c in candidates:
        n = sum(1 for f in e.labelled() if getattr(e, f) is not None and _n(f, getattr(e, f)) == _n(f, c.get(f)))
        if n > best_n:
            best, best_n = c, n
    return _row(best) if best else None


# --------------------------------------------------------------------------- text report
def _pct(x: float | None) -> str:
    return "n/a (nothing labelled)" if x is None else f"{x * 100:.1f}%"


def format_report(r: dict) -> str:
    s, c, f, mf = r["sets"], r["components"], r["fields"], r["mfr_finish"]
    gt = r["ground_truth"]
    out = [f"EVALUATION - {r['document']}", f"ground truth: {MANUAL_BANNER}  verified={gt['verified']}  "
           f"labelled_by={gt['labelled_by']}  sets_complete={gt['sets_complete']}", ""]
    out += ["SETS",
            f"  expected {s['expected']}   extracted {s['extracted']}   matched {s['matched']}   recall {_pct(s['recall'])}",
            f"  missing: {s['missing'] or 'none'}",
            f"  extra: {s['extra'] or 'none'}" + ("" if s["extra_are_errors"] else
                                                   "   (not errors: ground truth does not list every set)")]
    for k in ("description", "status", "pages"):
        if s[k]["labelled"]:
            out.append(f"  set {k}: {s[k]['correct']}/{s[k]['labelled']} = {_pct(s[k]['accuracy'])}")
    out += ["", "COMPONENTS",
            f"  expected {c['expected']}   matched {c['matched']}   recall {_pct(c['recall'])}   "
            f"(in matched sets only: {_pct(c['recall_in_matched_sets'])})",
            f"  matched by tier (A catalog+description, B catalog, C description): {c['matched_by_tier']}",
            "", "FIELD ACCURACY (labelled fields on matched components only; unlabelled fields are ignored)"]
    for k in COMPONENT_FIELDS:
        x = f[k]
        line = f"  {k:<15} {x['correct']}/{x['labelled']} = {_pct(x['accuracy'])}"
        if x["identity_pairs"]:
            line += f"   [{x['identity_pairs']} used as match key; excluding those: {_pct(x['accuracy_excluding_identity'])}]"
        out.append(line)
    out += ["", f"MFR/FINISH  swaps: {mf['swaps']}   possible swaps: {mf['possible_swaps']}"]
    out += ["", "WARNINGS"] + ([f"  - {w}" for w in r["warnings"]] or ["  none"])
    if c["missing"]:
        out += ["", "MISSING COMPONENTS"]
        for m in c["missing"][:25]:
            out.append(f"  set {m['set']} row {m.get('row', '-')}: {json.dumps(m['expected'], ensure_ascii=False)}"
                       + (f"  ~ closest extracted: {json.dumps(m['closest_unmatched_actual'], ensure_ascii=False)}"
                          if m.get("closest_unmatched_actual") else ""))
    if r["field_mismatches"]:
        out += ["", "FIELD MISMATCHES (expected -> actual, original values)"]
        for m in r["field_mismatches"][:40]:
            out.append(f"  set {m['set']} row {m['row']} {m['field']}: {m['expected']!r} -> {m['actual']!r}")
    out += ["", r["note"]]
    return "\n".join(out)


# --------------------------------------------------------------------------- scaffold (human labelling helper)
def make_scaffold(
    pdf: str | Path, *, pages: list[int] | None = None, limit: int | None = None, sets: list[str] | None = None
) -> dict:
    """A blank labelling skeleton. Set numbers are NOT filled in - detected headers appear only as `_hint`s."""
    from .headers import detect_set_headers
    from .ingest import load_pages

    headers = detect_set_headers(load_pages(pdf))
    if pages:
        headers = [h for h in headers if h.page in set(pages)]
    if sets:
        wanted = {norm_text(x) for x in sets}
        headers = [h for h in headers if norm_text(h.set_number) in wanted]
    if limit:
        headers = headers[:limit]
    return {
        "_comment": f"{MANUAL_BANNER} - UNVERIFIED SKELETON. Nothing here is an answer. Read each set in the PDF, "
                    f"replace {PLACEHOLDER}, add components, then set verified=true. Keys starting with '_' are ignored. "
                    "Omit a field to leave it unlabelled; write null to say it is really empty.",
        "document": Path(pdf).name, "verified": False, "labelled_by": None, "sets_complete": False,
        "sets": [{
            "set_number": PLACEHOLDER,
            "_hint": {"page": h.page, "line_id": h.line_id, "header_text": h.text},
            "_todo": 'Add "components": [ {qty, description, catalog_number, mfr, finish, notes}, ... ]. '
                     "Leaving the key out keeps the rows UNLABELLED (an empty list would mean 'this set has no rows').",
        } for h in headers],
    }


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwsets.evaluate", description="Offline evaluation against manual ground truth")
    ap.add_argument("--expected", type=Path, help="manual ground-truth JSON")
    ap.add_argument("--actual", type=Path, help="extracted JSON (ExtractionResult, or the API response)")
    ap.add_argument("--json", type=Path, help="also write the full report as JSON")
    ap.add_argument("--allow-unverified", action="store_true", help="score even if verified is not true (testing only)")
    ap.add_argument("--scaffold", type=Path, help="write a blank labelling skeleton here (needs --pdf)")
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--pages", help="scaffold: only headers on these 1-based pages, e.g. 285-294")
    ap.add_argument("--limit", type=int, help="scaffold: only the first N headers")
    ap.add_argument("--sets", help="scaffold: only these detected set numbers, e.g. 05,06,07 (matched exactly)")
    args = ap.parse_args(argv)

    if args.scaffold:
        if not args.pdf:
            ap.error("--scaffold needs --pdf")
        if args.scaffold.exists():
            print(f"refusing to overwrite {args.scaffold}", file=sys.stderr)
            return 2
        from .pages import parse_page_spec

        wanted_sets = [x.strip() for x in args.sets.split(",")] if args.sets else None
        skeleton = make_scaffold(args.pdf, pages=parse_page_spec(args.pages), limit=args.limit, sets=wanted_sets)
        args.scaffold.parent.mkdir(parents=True, exist_ok=True)
        args.scaffold.write_text(json.dumps(skeleton, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{MANUAL_BANNER}: wrote UNVERIFIED skeleton with {len(skeleton['sets'])} sets -> {args.scaffold}",
              file=sys.stderr)
        return 0

    if not (args.expected and args.actual):
        ap.error("--expected and --actual are required (or use --scaffold)")
    try:
        gt = load_ground_truth(args.expected, allow_unverified=args.allow_unverified)
        actual = json.loads(args.actual.read_text(encoding="utf-8"))
    except (ValueError, ValidationError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    report = evaluate(gt, actual)
    print(format_report(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
