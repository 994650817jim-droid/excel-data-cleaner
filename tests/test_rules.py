import pandas as pd
import pytest

from cleaner.fileio import read_table
from cleaner.quality import analyze
from cleaner.rules import apply_rules, compare, make_rule, normalize_name, normalize_rules, suggest_rules
from cleaner.sample import sample_csv_bytes


def R(*specs):
    out = []
    for s in specs:
        rid, params = (s, {}) if isinstance(s, str) else s
        out.append(make_rule(rid, True, **params))
    return out


def clean(df, *specs):
    return apply_rules(df, R(*specs))


# 4. duplicates
def test_dedup_keeps_first_and_preserves_row_ids():
    df = pd.DataFrame({"a": [1, 2, 1, 3, 2]})
    res = clean(df, "dedup")
    assert res.df["a"].tolist() == [1, 2, 3]
    assert res.df.index.tolist() == [0, 1, 3]            # stable ids
    assert res.removed == {2: "dedup", 4: "dedup"}


def test_dedup_subset_and_unknown_column_warning():
    df = pd.DataFrame({"k": [1, 1, 2], "v": ["a", "b", "c"]})
    res = clean(df, ("dedup", {"subset": ["k", "ghost"]}))
    assert len(res.df) == 2
    assert any(w["code"] == "unknown_column" and w["column"] == "ghost" for w in res.warnings)


# 11. whitespace
def test_trim_and_blank_to_na():
    df = pd.DataFrame({"a": ["  x ", "y", "   ", None]})
    res = clean(df, "trim", "blank_to_na")
    assert res.df["a"].tolist()[:2] == ["x", "y"]
    assert res.df["a"].isna().tolist() == [False, False, True, True]


def test_trim_then_dedup_finds_hidden_duplicates():
    df = pd.DataFrame({"a": ["x", " x "]})
    assert len(clean(df, "dedup").df) == 2
    assert len(clean(df, "trim", "dedup").df) == 1


# 5. missing values
@pytest.fixture
def nums():
    return pd.DataFrame({"n": [1.0, None, 3.0, 8.0], "t": ["a", None, "a", "b"]})


def test_fill_mean_median(nums):
    assert clean(nums, ("fill_missing", {"strategy": "mean"})).df["n"].tolist() == [1, 4, 3, 8]
    assert clean(nums, ("fill_missing", {"strategy": "median"})).df["n"].tolist() == [1, 3, 3, 8]


def test_fill_mean_skips_text_with_warning(nums):
    res = clean(nums, ("fill_missing", {"strategy": "mean"}))
    assert res.df["t"].isna().sum() == 1
    assert any(w["code"] == "fill_skipped" and w["columns"] == ["t"] for w in res.warnings)


def test_fill_mode_and_constant(nums):
    res = clean(nums, ("fill_missing", {"strategy": "mode"}))
    assert res.df["t"].tolist() == ["a", "a", "a", "b"]
    res = clean(nums, ("fill_missing", {"strategy": "constant", "constant": "0"}))
    assert res.df["n"].tolist() == [1, 0, 3, 8] and res.df["t"].tolist()[1] == "0"


def test_fill_constant_non_numeric_for_numeric_column_is_skipped_not_corrupted(nums):
    res = clean(nums, ("fill_missing", {"strategy": "constant", "constant": "n/a"}))
    assert res.df["n"].isna().sum() == 1 and res.df["t"].tolist()[1] == "n/a"


def test_fill_drop_rows_and_column_scope(nums):
    res = clean(nums, ("fill_missing", {"strategy": "drop_rows", "columns": ["n"]}))
    assert res.df.index.tolist() == [0, 2, 3]


def test_integer_column_mean_fill_is_not_silently_rounded():
    df = pd.DataFrame({"n": pd.array([1, None, 2], dtype="Int64")})
    out = clean(df, ("fill_missing", {"strategy": "mean"})).df["n"]
    assert out.tolist() == [1.0, 1.5, 2.0]


def test_strategy_none_changes_nothing(nums):
    res = clean(nums, ("fill_missing", {"strategy": "none"}))
    assert res.df.equals(nums)


# 9. dates
def dates_df(values):
    return pd.DataFrame({"d": values})


def test_standardize_mixed_dates():
    df = dates_df(["2024-01-05", "01/15/2024", "15 Mar 2024", "Mar 7, 2024", "20/02/2024", None])
    res = clean(df, ("standardize_dates", {"columns": ["d"]}))
    assert res.df["d"].tolist()[:5] == ["2024-01-05", "2024-01-15", "2024-03-15", "2024-03-07", "2024-02-20"]
    assert pd.isna(res.df["d"].iloc[5])
    # "01/15/2024" is month-first and "20/02/2024" is day-first: the user is told
    assert [w["code"] for w in res.warnings] == ["dates_conflict"]


def test_ambiguous_dates_are_reported_and_user_can_choose_order():
    df = dates_df(["03/04/2024", "05/06/2024"])
    res = clean(df, ("standardize_dates", {"columns": ["d"]}))
    assert any(w["code"] == "dates_ambiguous" for w in res.warnings)
    assert res.df["d"].tolist() == ["2024-03-04", "2024-05-06"]          # default month-first
    res = clean(df, ("standardize_dates", {"columns": ["d"], "dayfirst": True}))
    assert res.df["d"].tolist() == ["2024-04-03", "2024-06-05"]


def test_day_first_inferred_from_unambiguous_values():
    df = dates_df(["25/12/2024", "03/04/2024"])
    res = clean(df, ("standardize_dates", {"columns": ["d"]}))
    assert res.df["d"].tolist() == ["2024-12-25", "2024-04-03"]


def test_unparseable_dates_left_untouched_and_reported():
    df = dates_df(["2024-01-05", "not a date", "2024-13-45"])
    res = clean(df, ("standardize_dates", {"columns": ["d"]}))
    assert res.df["d"].tolist() == ["2024-01-05", "not a date", "2024-13-45"]
    w = [w for w in res.warnings if w["code"] == "dates_unparsed"][0]
    assert w["count"] == 2


def test_custom_date_format():
    df = dates_df(["2024-01-05", "Mar 7, 2024"])
    res = clean(df, ("standardize_dates", {"columns": ["d"], "fmt": "%d/%m/%Y"}))
    assert res.df["d"].tolist() == ["05/01/2024", "07/03/2024"]


def test_dates_with_time_keep_time():
    df = dates_df(["2024-01-05 10:30:00", "2024-01-06"])
    res = clean(df, ("standardize_dates", {"columns": ["d"]}))
    assert res.df["d"].tolist() == ["2024-01-05 10:30:00", "2024-01-06 00:00:00"]


def test_excel_datetime_column_is_formatted():
    df = pd.DataFrame({"d": pd.to_datetime(["2024-01-05", "2024-02-06"])})
    res = clean(df, ("standardize_dates", {"columns": ["d"]}))
    assert res.df["d"].tolist() == ["2024-01-05", "2024-02-06"]


# column names
def test_normalize_columns_and_collisions():
    df = pd.DataFrame([[1, 2, 3, 4]], columns=["First Name", " first-name ", "Total (¥)", ""])
    res = clean(df, "normalize_columns")
    assert res.df.columns.tolist() == ["first_name", "first_name_2", "total", "column_4"]
    assert res.col_map["First Name"] == "first_name"
    assert normalize_name("  A b-C ") == "a_b_c"


def test_column_scoped_params_use_original_names_even_with_normalize():
    df = pd.DataFrame({"Order Date": ["2024-01-05", "Mar 7, 2024"]})
    res = clean(df, ("standardize_dates", {"columns": ["Order Date"]}), "normalize_columns")
    assert res.df.columns.tolist() == ["order_date"]
    assert res.df["order_date"].tolist() == ["2024-01-05", "2024-03-07"]


def test_drop_empty_rows_and_columns():
    df = pd.DataFrame({"a": [1, None, 3], "b": [None] * 3, "c": ["x", "  ", "z"]})
    res = clean(df, "drop_empty")
    assert res.df.shape == (2, 2)          # row 1 is NaN + NaN + whitespace-only -> empty
    assert "b" not in res.df.columns


def test_infer_types_after_trim():
    df = pd.DataFrame({"n": [" 1", "2 ", None], "id": ["007", "008", "009"]})
    res = clean(df, "trim", "infer_types")
    assert str(res.df["n"].dtype) == "Int64" and res.df["id"].tolist() == ["007", "008", "009"]


# 15. statistics accuracy and row identity
def test_summary_statistics_are_exact():
    df = pd.DataFrame({
        "Name": [" a", "a", "b", None, None],
        "Val": [1.0, 1.0, None, 4.0, None],
        "Empty": [None] * 5,
    })
    rules = R("trim", "drop_empty", "dedup", ("fill_missing", {"strategy": "constant", "constant": "0"}),
              "normalize_columns")
    res = apply_rules(df, rules)
    s = compare(df, res)
    assert s["rows_before"] == 5 and s["rows_after"] == 3
    assert s["rows_removed"] == 2                       # row 4 fully empty, row 1 duplicate of row 0
    assert res.removed == {1: "dedup", 4: "drop_empty"}
    assert s["cols_removed"] == 1 and s["cols_renamed"] == 2
    assert s["missing_filled"] == 2                     # Name on row 3, Val on row 2
    assert s["cells_changed"] == 3                      # row 0 trimmed + the two filled cells
    assert s["cells_changed"] == int(s["mask"].to_numpy().sum())
    by = {e["rule"]: e for e in res.log}
    assert by["fill_missing"]["filled"] == 2 and by["trim"]["cells_changed"] == 1


def test_mask_aligns_with_cleaned_frame_after_row_removal():
    df = pd.DataFrame({"a": ["x", "x", " y"], "b": [1, 1, 2]})
    res = clean(df, "trim", "dedup")
    mask = compare(df, res)["mask"]
    assert mask.index.tolist() == res.df.index.tolist() == [0, 2]
    assert mask.loc[2, "a"] and not mask.loc[0, "a"]


def test_original_dataframe_is_never_modified():
    df = pd.DataFrame({"a": [" x", "x", None], "b": [1.0, 1.0, None]})
    snapshot = df.copy()
    apply_rules(df, R("trim", "dedup", ("fill_missing", {"strategy": "mean"}), "normalize_columns"))
    pd.testing.assert_frame_equal(df, snapshot)


def test_log_entries_report_per_rule_counts():
    df = pd.DataFrame({"a": [" x", "x", "y"]})
    res = clean(df, "trim", "dedup")
    by = {e["rule"]: e for e in res.log}
    assert by["trim"]["cells_changed"] == 1 and by["dedup"]["rows_removed"] == 1


# idempotency
def test_pipeline_is_idempotent_on_sample_data():
    df = read_table(sample_csv_bytes(), "s.csv").df
    rules = suggest_rules(analyze(df))
    rules = normalize_rules(rules + R(("fill_missing", {"strategy": "median"}), "normalize_columns"))
    first = apply_rules(df, rules)
    second = apply_rules(first.df, rules)
    assert second.df.equals(first.df)
    assert all(e["rows_removed"] == 0 and e["cells_changed"] == 0 for e in second.log
               if e["rule"] != "normalize_columns")


def test_dedup_runs_again_after_fill_so_result_is_stable():
    df = pd.DataFrame({"a": [1.0, None, 1.0], "b": ["x", "x", "x"]})
    rules = R("dedup", ("fill_missing", {"strategy": "median"}))
    first = apply_rules(df, rules)
    assert len(first.df) == 1                              # filling made all rows identical
    assert apply_rules(first.df, rules).df.equals(first.df)


def test_suggestions_follow_the_report_and_change_nothing():
    df = read_table(sample_csv_bytes(), "s.csv").df
    before = df.copy()
    rules = {r["id"]: r for r in suggest_rules(analyze(df))}
    assert rules["trim"]["enabled"] and rules["dedup"]["enabled"] and rules["standardize_dates"]["enabled"]
    assert rules["standardize_dates"]["params"]["columns"] == ["order_date"]
    assert not rules["fill_missing"]["enabled"]            # never auto-fill without the user choosing
    pd.testing.assert_frame_equal(df, before)


def test_disabled_rules_do_nothing():
    df = pd.DataFrame({"a": [" x", " x"]})
    res = apply_rules(df, [make_rule("trim", False), make_rule("dedup", False)])
    assert res.df.equals(df) and res.log == []


def test_unknown_rule_rejected():
    with pytest.raises(ValueError):
        normalize_rules([{"id": "os.system", "enabled": True}])


def test_mixed_type_column_survives_all_rules():
    df = pd.DataFrame({"m": [1, "two", 3.5, None, " x "]})
    res = apply_rules(df, R("trim", "blank_to_na", "infer_types", "dedup",
                            ("fill_missing", {"strategy": "mode"})))
    # the filled row becomes a duplicate of row 0 and is removed by the post-fill dedup pass
    assert res.removed == {3: "dedup"}
    assert res.df["m"].tolist() == [1, "two", 3.5, "x"]


def test_everything_empty_is_not_reduced_to_nothing_silently():
    df = pd.DataFrame({"a": [None, None], "b": [None, None]})
    res = clean(df, "drop_empty")
    assert res.df.shape == (2, 2) and res.warnings[0]["code"] == "all_rows_empty"
