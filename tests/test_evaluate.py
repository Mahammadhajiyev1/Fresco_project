"""Offline evaluator tests - synthetic fixtures only (invented data; nothing here is a real answer)."""
import json

import pytest

from hwsets.evaluate import (
    GroundTruth, evaluate, format_report, load_ground_truth, main, make_scaffold, match_components, norm_code,
    norm_qty, norm_text,
)


def comp(qty=1, description="WIDGET HINGE", catalog="W-100", mfr="ACME", finish="F1", notes=None):
    return {"qty": qty, "description": description, "catalog_number": catalog, "mfr": mfr, "finish": finish, "notes": notes}


def aset(number, comps, status="active", description=None, pages=(1,)):
    return {"set_number": number, "description": description, "status": status,
            "locations": [{"page": p, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}, "line_ids": []} for p in pages],
            "components": comps}


def gt(sets, complete=False, verified=True):
    return GroundTruth.model_validate(
        {"document": "d.pdf", "verified": verified, "sets_complete": complete, "sets": sets})


def full_expected():
    return gt([
        {"set_number": "X1", "components": [
            {"qty": 2, "description": "WIDGET HINGE", "catalog_number": "W-100", "mfr": "ACME", "finish": "F1", "notes": None},
            {"qty": 1, "description": "GADGET CLOSER", "catalog_number": "G-200", "mfr": "BOLT", "finish": "F2", "notes": None}]},
        {"set_number": "X2", "status": "not_used", "components": []},
    ], complete=True)


def good_actual():
    return {"sets": [
        aset("X1", [comp(2, "WIDGET HINGE", "W-100", "ACME", "F1"), comp(1, "GADGET CLOSER", "G-200", "BOLT", "F2")]),
        aset("X2", [], status="not_used")]}


def test_perfect_extraction():
    r = evaluate(full_expected(), good_actual())
    assert r["sets"]["recall"] == 1.0 and r["sets"]["missing"] == [] and r["sets"]["extra"] == []
    assert r["components"]["expected"] == 2 and r["components"]["matched"] == 2 and r["components"]["recall"] == 1.0
    for f in ("qty", "description", "catalog_number", "mfr", "finish", "notes"):
        assert r["fields"][f]["accuracy"] == 1.0, f
    assert r["mfr_finish"]["swaps"] == 0 and r["warnings"] == []
    assert "overall" not in json.dumps(r).lower().replace("no overall score", "")   # no invented single score


def test_missing_set():
    act = {"sets": [good_actual()["sets"][0]]}
    r = evaluate(full_expected(), act)
    assert r["sets"]["missing"] == ["X2"] and r["sets"]["recall"] == 0.5
    assert any("missing sets" in w for w in r["warnings"])


def test_extra_set_is_an_error_only_when_ground_truth_is_complete():
    act = good_actual()
    act["sets"].append(aset("X3", [comp()]))
    r = evaluate(full_expected(), act)
    assert r["sets"]["extra"] == ["X3"] and r["sets"]["extra_are_errors"] is True
    assert any("extra (unexpected) sets" in w for w in r["warnings"])
    partial = gt([{"set_number": "X1"}], complete=False)
    r2 = evaluate(partial, act)
    assert r2["sets"]["extra_are_errors"] is False and r2["sets"]["unlabelled_extracted"] == 2
    assert not any("extra (unexpected)" in w for w in r2["warnings"])


def test_missing_component():
    act = good_actual()
    act["sets"][0]["components"].pop()
    r = evaluate(full_expected(), act)
    assert r["components"]["matched"] == 1 and r["components"]["recall"] == 0.5
    assert r["components"]["missing"][0]["expected"]["catalog_number"] == "G-200"
    assert any("not found" in w for w in r["warnings"])


def test_quantity_null_is_a_real_expectation():
    exp = gt([{"set_number": "X1", "components": [{"qty": None, "description": "BY OTHERS", "catalog_number": None}]}])
    ok = evaluate(exp, {"sets": [aset("X1", [comp(None, "BY OTHERS", None, None, None)])]})
    assert ok["fields"]["qty"]["accuracy"] == 1.0
    guessed = evaluate(exp, {"sets": [aset("X1", [comp(1, "BY OTHERS", None, None, None)])]})   # model guessed 1
    assert guessed["fields"]["qty"]["accuracy"] == 0.0
    assert guessed["field_mismatches"][0] == {"set": "X1", "row": 1, "field": "qty", "expected": None, "actual": 1}


def test_wrong_manufacturer_and_wrong_finish_are_reported_separately():
    act = good_actual()
    act["sets"][0]["components"][0]["mfr"] = "WRONG"
    act["sets"][0]["components"][1]["finish"] = "F9"
    r = evaluate(full_expected(), act)
    assert r["fields"]["mfr"]["accuracy"] == 0.5 and r["fields"]["finish"]["accuracy"] == 0.5
    assert r["fields"]["qty"]["accuracy"] == 1.0 and r["mfr_finish"]["swaps"] == 0


def test_swapped_manufacturer_and_finish():
    act = good_actual()
    row = act["sets"][0]["components"][0]
    row["mfr"], row["finish"] = row["finish"], row["mfr"]
    r = evaluate(full_expected(), act)
    assert r["mfr_finish"]["swaps"] == 1 and r["mfr_finish"]["possible_swaps"] == 0
    assert r["fields"]["mfr"]["accuracy"] == 0.5 and r["fields"]["finish"]["accuracy"] == 0.5
    assert any("SWAPPED" in w for w in r["warnings"])
    d = r["mfr_finish"]["swap_details"][0]
    assert (d["expected_mfr"], d["actual_mfr"]) == ("ACME", "F1")


def test_one_sided_swap_is_only_a_possible_swap():
    act = good_actual()
    act["sets"][0]["components"][0]["finish"] = "ACME"          # manufacturer text landed in the finish column
    act["sets"][0]["components"][0]["mfr"] = None
    r = evaluate(full_expected(), act)
    assert r["mfr_finish"]["swaps"] == 0 and r["mfr_finish"]["possible_swaps"] == 1


def test_partially_labelled_ground_truth_never_penalises_unlabelled_fields():
    exp = gt([{"set_number": "X1", "components": [{"description": "WIDGET HINGE", "mfr": "ACME"}]}])
    act = {"sets": [aset("X1", [comp(99, "WIDGET HINGE", "totally-different", "ACME", "zzz", "some note")])]}
    r = evaluate(exp, act)
    assert r["fields"]["mfr"] == {**r["fields"]["mfr"], "labelled": 1, "correct": 1, "accuracy": 1.0}
    for f in ("qty", "catalog_number", "finish", "notes"):
        assert r["fields"][f]["labelled"] == 0 and r["fields"][f]["accuracy"] is None, f
    assert r["warnings"] == []


def test_set_with_components_unlabelled_is_scored_on_detection_only():
    r = evaluate(gt([{"set_number": "X1"}]), {"sets": [aset("X1", [comp()])]})
    assert r["sets"]["matched"] == 1 and r["components"]["expected"] == 0 and r["components"]["recall"] is None


def test_components_complete_false_does_not_count_extra_rows():
    exp = gt([{"set_number": "X1", "components_complete": False, "components": [{"catalog_number": "W-100"}]}])
    r = evaluate(exp, {"sets": [aset("X1", [comp(), comp(1, "GADGET CLOSER", "G-200")])]})
    assert r["components"]["extra_in_fully_labelled_sets"] == []
    full = evaluate(gt([{"set_number": "X1", "components": [{"catalog_number": "W-100"}]}]),
                    {"sets": [aset("X1", [comp(), comp(1, "GADGET CLOSER", "G-200")])]})
    assert len(full["components"]["extra_in_fully_labelled_sets"]) == 1


def test_harmless_normalisation_differences_still_match():
    exp = gt([{"set_number": "Hw 5", "components": [
        {"qty": 3, "description": "Butt Hinge – 4.5’", "catalog_number": "TA2714 4.5x4.5", "mfr": "IVES", "finish": "US26D"}]}])
    act = {"sets": [aset("  HW   5 ", [
        {"qty": "3", "description": "butt   hinge - 4.5'", "catalog_number": "ta2714  4.5X4.5\uf09d", "mfr": "ives.", "finish": " us26d "}])]}
    r = evaluate(exp, act)
    assert r["sets"]["matched"] == 1 and r["components"]["matched"] == 1
    assert all(r["fields"][f]["accuracy"] == 1.0 for f in ("qty", "description", "catalog_number", "mfr", "finish"))
    assert r["warnings"] == []


def test_normalisation_is_not_loose_enough_to_hide_real_differences():
    assert norm_text("US26D") != norm_text("US26")
    assert norm_code("PE") == norm_code(" pe. ") and norm_code("PE") != norm_code("PEM")
    assert norm_qty("2") == norm_qty(2.0) == 2 and norm_qty("") is None and norm_qty(None) is None and norm_qty("two") == "two"


def test_set_numbers_one_and_zero_one_are_not_matched_but_flagged():
    r = evaluate(gt([{"set_number": "1"}], complete=True), {"sets": [aset("01", [])]})
    assert r["sets"]["matched"] == 0 and r["sets"]["near_matches"] == [{"expected": "1", "extracted": "01"}]
    assert any("NOT matched" in w for w in r["warnings"])


def test_matching_is_strict_and_reports_wrong_rows_as_missing_and_extra():
    exp = gt([{"set_number": "X1", "components": [{"qty": 1, "description": "WIDGET HINGE", "catalog_number": "W-100"}]}], complete=True)
    act = {"sets": [aset("X1", [comp(1, "WIDGET HINGE", "W-999")])]}       # right description, wrong catalog
    r = evaluate(exp, act)
    assert r["components"]["matched"] == 1                                   # tier C: description equal
    assert r["fields"]["catalog_number"]["correct"] == 0 and r["field_mismatches"][0]["field"] == "catalog_number"
    unrelated = evaluate(exp, {"sets": [aset("X1", [comp(1, "SOMETHING ELSE", "Z-1")])]})
    assert unrelated["components"]["matched"] == 0 and len(unrelated["components"]["missing"]) == 1
    assert len(unrelated["components"]["extra_in_fully_labelled_sets"]) == 1


def test_duplicate_rows_are_matched_in_order_and_each_only_once():
    rows = [{"qty": 1, "description": "CORE", "catalog_number": "C-1", "notes": "first"},
            {"qty": 1, "description": "CORE", "catalog_number": "C-1", "notes": "second"}]
    exp = gt([{"set_number": "X1", "components": rows}])
    act = {"sets": [aset("X1", [comp(1, "CORE", "C-1", None, None, "first"), comp(1, "CORE", "C-1", None, None, "second")])]}
    r = evaluate(exp, act)
    assert r["components"]["matched"] == 2 and r["fields"]["notes"]["accuracy"] == 1.0 and r["components"]["out_of_order"] == 0
    swapped = {"sets": [aset("X1", [comp(1, "CORE", "C-1", None, None, "second"), comp(1, "CORE", "C-1", None, None, "first")])]}
    assert evaluate(exp, swapped)["fields"]["notes"]["accuracy"] == 0.0


def test_set_level_fields_status_pages_description():
    exp = gt([{"set_number": "X1", "description": None, "status": "not_used", "pages": [3, 4]}])
    r = evaluate(exp, {"sets": [aset("X1", [], status="not_used", pages=(3, 4))]})
    assert r["sets"]["status"]["accuracy"] == 1.0 and r["sets"]["pages"]["accuracy"] == 1.0
    assert r["sets"]["description"]["labelled"] == 1 and r["sets"]["description"]["accuracy"] == 1.0   # null expected
    bad = evaluate(exp, {"sets": [aset("X1", [], status="active", pages=(3,), description="something")]})
    assert bad["sets"]["status"]["accuracy"] == 0.0 and bad["sets"]["pages"]["accuracy"] == 0.0
    assert bad["sets"]["description"]["accuracy"] == 0.0 and len(bad["sets"]["mismatches"]) == 3


def test_missing_set_components_count_as_expected_components():
    r = evaluate(full_expected(), {"sets": []})
    assert r["components"]["expected"] == 2 and r["components"]["matched"] == 0
    assert r["components"]["expected_in_matched_sets"] == 0 and r["components"]["recall_in_matched_sets"] is None


def test_accepts_the_api_response_shape():
    r = evaluate(full_expected(), {"id": "abc", "result": good_actual()})
    assert r["sets"]["recall"] == 1.0


def test_match_components_reports_tiers():
    exp = gt([{"set_number": "X", "components": [
        {"catalog_number": "A", "description": "d1"}, {"catalog_number": "B"}, {"description": "d3"}]}]).sets[0].components
    act = [{"catalog_number": "A", "description": "d1"}, {"catalog_number": "B", "description": "zzz"}, {"catalog_number": "Q", "description": "d3"}]
    m = match_components(exp, act)
    assert [t for _, _, t in m["pairs"]] == ["A", "B", "C"] and not m["missing"] and not m["extra"]


# ---- ground-truth loading / safety -----------------------------------------------------------------------------
def write(tmp_path, obj, name="gt.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_unverified_ground_truth_is_refused(tmp_path):
    p = write(tmp_path, {"document": "d.pdf", "verified": False, "sets": []})
    with pytest.raises(ValueError, match="verified"):
        load_ground_truth(p)
    assert load_ground_truth(p, allow_unverified=True).sets == []


def test_placeholder_entries_are_refused(tmp_path):
    p = write(tmp_path, {"document": "d.pdf", "verified": True, "sets": [{"set_number": "<FILL IN>"}]})
    with pytest.raises(ValueError, match="FILL IN"):
        load_ground_truth(p)


def test_underscore_keys_are_ignored_and_typos_are_rejected(tmp_path):
    ok = write(tmp_path, {"_comment": "x", "document": "d.pdf", "verified": True,
                          "sets": [{"set_number": "1", "_hint": {"page": 1}, "components": [{"qty": 1, "_c": "n"}]}]})
    assert load_ground_truth(ok).sets[0].components[0].qty == 1
    typo = write(tmp_path, {"document": "d.pdf", "verified": True, "sets": [{"set_number": "1", "componets": []}]}, "t.json")
    with pytest.raises(Exception):
        load_ground_truth(typo)


def test_shipped_template_is_empty_and_marked_manual():
    from pathlib import Path
    t = Path(__file__).parents[1] / "data" / "ground_truth" / "TEMPLATE.manual_ground_truth.json"
    raw = json.loads(t.read_text(encoding="utf-8"))
    assert raw["sets"] == [] and raw["verified"] is False and "MANUAL GROUND TRUTH" in raw["_comment"]
    with pytest.raises(ValueError):
        load_ground_truth(t)                                                 # cannot be scored as-is


def test_scaffold_contains_hints_but_no_answers(furniture_pdf, tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(furniture_pdf)
    sk = make_scaffold(pdf)
    assert sk["verified"] is False and "MANUAL GROUND TRUTH" in sk["_comment"]
    assert [s["set_number"] for s in sk["sets"]] == ["<FILL IN>"] * 3
    assert [s["_hint"]["page"] for s in sk["sets"]] == [1, 3, 3]
    assert all("components" not in s for s in sk["sets"])                  # absent = unlabelled, NOT "has no rows"
    assert len(make_scaffold(pdf, limit=1)["sets"]) == 1 and len(make_scaffold(pdf, pages=[3])["sets"]) == 2
    picked = make_scaffold(pdf, sets=["3", "1"])["sets"]
    assert [s["_hint"]["header_text"] for s in picked] == ["HARDWARE SET #1 - ENTRANCE DOORS", "HARDWARE SET #3"]


# ---- CLI ------------------------------------------------------------------------------------------------------
def test_cli_scores_offline_and_writes_json(tmp_path, capsys):
    exp = write(tmp_path, {"document": "d.pdf", "verified": True, "sets_complete": True,
                           "sets": [{"set_number": "X1", "components": [
                               {"qty": 2, "description": "WIDGET HINGE", "catalog_number": "W-100", "mfr": "ACME", "finish": "F1"}]}]})
    row = comp(2, "WIDGET HINGE", "W-100", "F1", "ACME")                      # swapped on purpose
    act = write(tmp_path, {"sets": [aset("X1", [row])]}, "act.json")
    out = tmp_path / "rep" / "r.json"
    assert main(["--expected", str(exp), "--actual", str(act), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "MANUAL GROUND TRUTH" in text and "swaps: 1" in text and "No overall score" in text
    assert json.loads(out.read_text(encoding="utf-8"))["mfr_finish"]["swaps"] == 1


def test_cli_refuses_unverified_and_never_overwrites_scaffold(tmp_path, furniture_pdf, capsys):
    exp = write(tmp_path, {"document": "d.pdf", "verified": False, "sets": []})
    act = write(tmp_path, {"sets": []}, "a.json")
    assert main(["--expected", str(exp), "--actual", str(act)]) == 2
    assert main(["--expected", str(exp), "--actual", str(act), "--allow-unverified"]) == 0
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(furniture_pdf)
    target = tmp_path / "gt" / "b.json"
    assert main(["--scaffold", str(target), "--pdf", str(pdf)]) == 0 and target.exists()
    assert main(["--scaffold", str(target), "--pdf", str(pdf)]) == 2         # refuses to clobber human work


def test_report_text_lists_unlabelled_as_such():
    exp = gt([{"set_number": "X1", "components": [{"description": "WIDGET HINGE"}]}])
    text = format_report(evaluate(exp, {"sets": [aset("X1", [comp()])]}))
    assert "nothing labelled" in text and "unlabelled fields are ignored" in text


def test_shipped_ground_truth_files_are_never_presented_as_verified_by_a_machine():
    """Every file in data/ground_truth is a blank skeleton, an explicitly-labelled unverified DRAFT, or a
    human-verified file that names its labeller. Unverified files can never be scored."""
    from pathlib import Path
    files = [p for p in (Path(__file__).parents[1] / "data" / "ground_truth").glob("*.json")]
    assert files
    for p in files:
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert "MANUAL GROUND TRUTH" in raw["_comment"], p.name
        if raw["verified"] is True:
            assert raw.get("labelled_by"), f"{p.name}: verified ground truth must name who labelled it"
            continue
        with pytest.raises(ValueError):
            load_ground_truth(p)                                                # unverified => cannot be scored
        assert raw.get("labelled_by") is None, f"{p.name}: nobody has signed off, so labelled_by must be null"
        if any("components" in s for s in raw["sets"]):                         # a draft holds answers ...
            assert "DRAFT" in raw["_comment"] and "NOT VERIFIED" in raw["_comment"], p.name   # ... and must say so
        else:                                                                   # a skeleton holds none
            assert all(s["set_number"] == "<FILL IN>" for s in raw["sets"]), p.name


def test_lyons_first5_draft_has_the_agreed_shape():
    """Structural guard for the first real ground-truth draft (holds whether or not a human has verified it yet)."""
    from pathlib import Path
    p = Path(__file__).parents[1] / "data" / "ground_truth" / "lyons_first5.json"
    gt = load_ground_truth(p, allow_unverified=True)
    assert gt.sets_complete is False and gt.document == "Project Manual (1).pdf"      # 5 of 29 sets labelled
    assert [s.set_number for s in gt.sets] == ["02", "05", "07", "14", "15"]
    assert [len(s.components) for s in gt.sets] == [2, 0, 7, 12, 6]
    assert all(s.components_complete for s in gt.sets)
    s05 = gt.sets[1]
    assert s05.status == "not_used" and s05.components == [] and s05.pages == [285]
    assert all(s.status == "active" for s in gt.sets if s.set_number != "05")
    rows = [c for s in gt.sets for c in s.components]
    assert all("notes" not in c.labelled() for c in rows)                       # unit/notes policy left to the human
    assert all({"qty", "description", "mfr", "finish"} <= set(c.labelled()) for c in rows)
    unfilled = [c for c in rows if "catalog_number" not in c.labelled()]
    assert len(unfilled) == 2                                                   # the two ambiguous catalog qualifiers
    assert all(c.qty is not None for c in rows)                                 # every printed row has a quantity
