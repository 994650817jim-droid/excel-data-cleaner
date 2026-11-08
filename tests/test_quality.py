import numpy as np
import pandas as pd

from cleaner.fileio import read_table
from cleaner.quality import SCORE_RULES, analyze
from cleaner.sample import sample_csv_bytes


def csv_df(text: str) -> pd.DataFrame:
    return read_table(text.encode(), "t.csv").df


def test_basic_counts():
    df = csv_df("a,b\n1,x\n1,x\n2,\n,y\n")
    s = analyze(df)["stats"]
    assert (s["rows"], s["cols"]) == (4, 2)
    assert s["duplicates"] == 1
    assert s["missing_cells"] == 2  # one NaN in each column


def test_missing_ratio_per_column():
    df = csv_df("a,b\n1,x\n,y\n,z\n4,\n")
    cols = {c["name"]: c for c in analyze(df)["columns"]}
    assert cols["a"]["missing_pct"] == 50.0 and cols["b"]["missing_pct"] == 25.0


def test_blank_strings_count_as_missing_but_are_reported_separately():
    df = pd.DataFrame({"a": ["x", "  ", None, "y"]})
    s = analyze(df)["stats"]
    assert s["blank_cells"] == 1 and s["missing_nan"] == 1 and s["missing_cells"] == 2


def test_empty_rows_and_columns():
    df = pd.DataFrame({"a": [1, None, 3], "b": [None, None, None], "c": ["x", None, "z"]})
    s = analyze(df)["stats"]
    assert s["empty_rows"] == 1 and s["empty_cols"] == ["b"]


def test_whitespace_cells_only_counts_text_with_padding():
    df = pd.DataFrame({"a": [" x", "y ", "z", None, "  "]})
    assert analyze(df)["stats"]["whitespace_cells"] == 2  # blank-only and NaN are not "padded"


def test_mixed_type_column_flagged():
    df = pd.DataFrame({"a": [1, "two", 3, "four", 5, "six"], "b": ["x"] * 6})
    s = analyze(df)["stats"]
    assert s["mixed_columns"] == ["a"]


def test_numbers_stored_as_text_with_noise_flagged_mixed():
    df = pd.DataFrame({"a": ["10", "20", "N/A", "30", "40", "n.a", "50", "60", "70", "80"]})
    assert analyze(df)["stats"]["mixed_columns"] == ["a"]


def test_inconsistent_dates_detected():
    df = pd.DataFrame({"d": ["2024-01-05", "01/15/2024", "15 Mar 2024", "Mar 7, 2024", "2024-02-01"]})
    c = analyze(df)["columns"][0]
    assert c["kind"] == "date" and c["date_inconsistent"] and len(c["date_formats"]) == 4


def test_consistent_dates_not_flagged():
    df = pd.DataFrame({"d": ["2024-01-05", "2024-02-15", "2024-03-01"]})
    c = analyze(df)["columns"][0]
    assert c["kind"] == "date" and not c["date_inconsistent"]


def test_plain_numbers_text_not_mistaken_for_dates():
    df = pd.DataFrame({"v": ["2024", "1999", "2001"]})
    assert analyze(df)["columns"][0]["kind"] != "date"


def test_outliers_are_flagged_not_removed():
    vals = [10, 11, 9, 10, 12, 10, 11, 9, 10, 10_000]
    df = pd.DataFrame({"x": vals})
    rep = analyze(df)
    assert rep["stats"]["outlier_cells"] == 1
    assert len(df) == 10 and df["x"].max() == 10_000  # analysis never mutates data


def test_no_outlier_flag_for_small_or_constant_data():
    assert analyze(pd.DataFrame({"x": [1, 2, 1000]}))["stats"]["outlier_cells"] == 0
    assert analyze(pd.DataFrame({"x": [5] * 20}))["stats"]["outlier_cells"] == 0


def test_high_unique_column():
    df = pd.DataFrame({"id": [f"id{i}" for i in range(30)], "g": ["a", "b"] * 15})
    assert analyze(df)["stats"]["high_unique_columns"] == ["id"]


def test_numeric_text_suggested_but_leading_zero_ids_are_not():
    df = pd.DataFrame({"n": [" 1", "2 ", "3"], "id": ["001", "002", "003"]})
    s = analyze(df)["stats"]
    assert s["numeric_text_columns"] == ["n"]


def test_score_is_100_for_clean_data_and_formula_is_public():
    clean = pd.DataFrame({"a": range(20), "b": list("abcde") * 4})
    rep = analyze(clean)
    assert rep["score"]["value"] == 100.0
    assert sum(cap for _, cap, _ in SCORE_RULES) == 100  # caps add up to the full scale


def test_score_matches_documented_formula():
    df = csv_df("a,b\n1,x\n1,x\n,y\n4,z\n")  # 8 cells, 1 missing, 1 duplicate of 4 rows
    rep = analyze(df)
    p = rep["score"]["penalties"]
    assert p["missing"]["points"] == round(100 * 1 / 8, 2)       # 1 point per 1%
    assert p["duplicates"]["points"] == 20 and p["duplicates"]["raw"] == 25.0  # 25% -> capped at 20
    assert rep["score"]["value"] == round(100 - sum(v["points"] for v in p.values()), 1)


def test_score_penalties_never_exceed_caps_and_score_not_negative():
    df = pd.DataFrame({"a": [None] * 10, "b": [None] * 10})
    rep = analyze(df)
    for key, cap, _ in SCORE_RULES:
        assert rep["score"]["penalties"][key]["points"] <= cap
    assert 0 <= rep["score"]["value"] <= 100


def test_sample_dataset_contains_every_demo_problem():
    df = read_table(sample_csv_bytes(), "s.csv").df
    s = analyze(df)["stats"]
    assert s["duplicates"] >= 3 and s["missing_cells"] > 0 and s["date_inconsistent"] == ["order_date"]
    assert s["whitespace_cells"] > 0 and s["outlier_cells"] >= 2 and s["empty_rows"] == 1
    assert df["order_id"].iloc[0] == "00001"  # leading zeros intact


def test_analyze_does_not_modify_input():
    df = csv_df("a,b\n 1 ,x\n1,\n")
    before = df.copy()
    analyze(df)
    pd.testing.assert_frame_equal(df, before)


def test_single_row_and_single_column_frames():
    assert analyze(pd.DataFrame({"a": [1]}))["stats"]["rows"] == 1
    assert analyze(pd.DataFrame({"a": [np.nan, np.nan]}))["stats"]["empty_cols"] == ["a"]
