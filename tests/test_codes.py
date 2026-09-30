from hwsets.codes import ColumnProfile, check_mfr_finish, classify_token, value_conflict


def test_token_classes():
    assert classify_token("US26D") == "finish"
    assert classify_token("630") == "finish"
    assert classify_token("BSP") == "finish"
    assert classify_token("LCN") == "mfr"
    assert classify_token("SCH") == "mfr"
    assert classify_token("PE") == "ambiguous"
    assert classify_token("NO") == "ambiguous"
    assert classify_token(None) == "unknown"


def test_column_role_from_majority():
    assert ColumnProfile(["MK", "LCN", "SCH", "PE"]).role == "mfr"
    assert ColumnProfile(["US26D", "630", "BSP", "PE"]).role == "finish"
    assert ColumnProfile(["PE", "NO"]).role == "unclear"


def test_ambiguous_value_never_conflicts():
    assert not value_conflict("PE", "mfr") and not value_conflict("PE", "finish")
    assert value_conflict("US26D", "mfr")
    assert value_conflict("LCN", "finish")


def test_detects_swapped_columns():
    verdict, _ = check_mfr_finish(["US26D", "630", "BSP"], ["MK", "LCN", "SCH"])
    assert verdict == "swapped"
    verdict, _ = check_mfr_finish(["MK", "LCN", "SCH"], ["US26D", "630", "BSP"])
    assert verdict == "ok"
