import io
import json
import zipfile

import pandas as pd
import pytest

from cleaner.assistant import parse_instruction
from cleaner.batch import run_batch, safe_name
from cleaner.fileio import read_table
from cleaner.quality import analyze
from cleaner.report import build_report, to_html, to_json, to_log_csv
from cleaner.rules import apply_rules, compare, make_rule, suggest_rules
from cleaner.sample import sample_csv_bytes
from cleaner.templates import TemplateError, export_template, parse_template


def rules_for_sample():
    return [make_rule("trim"), make_rule("blank_to_na"), make_rule("drop_empty"),
            make_rule("standardize_dates", columns=["order_date"]), make_rule("dedup"),
            make_rule("fill_missing", strategy="median"), make_rule("normalize_columns", False)]


# ----------------------------------------------------------------- templates
def test_template_roundtrip_and_reuse_gives_identical_results():
    rules = rules_for_sample()
    text = export_template(rules, "sales-clean")
    parsed, name = parse_template(text)
    assert name == "sales-clean"
    assert [(r["id"], r["enabled"], r["params"]) for r in parsed] == \
           [(r["id"], r["enabled"], r["params"]) for r in apply_order(rules)]
    df = read_table(sample_csv_bytes(), "s.csv").df
    a = apply_rules(df, rules).df
    b = apply_rules(df, parsed).df
    c = apply_rules(df, parse_template(text)[0]).df          # run the same template again
    assert a.equals(b) and b.equals(c)


def apply_order(rules):
    from cleaner.rules import normalize_rules
    return normalize_rules(rules)


@pytest.mark.parametrize("bad", [
    "not json", "[]", '{"version": 2, "rules": []}', '{"version": 1, "rules": "x"}',
    '{"version": 1, "rules": [{"id": "os.system"}]}',
    '{"version": 1, "rules": [{"id": "trim", "enabled": "yes"}]}',
    '{"version": 1, "rules": [{"id": "fill_missing", "params": {"strategy": "__import__(\'os\')"}}]}',
    '{"version": 1, "rules": [{"id": "standardize_dates", "params": {"fmt": "%Y;rm -rf"}}]}',
    '{"version": 1, "rules": [{"id": "dedup", "params": {"subset": [1, 2]}}]}',
])
def test_malformed_or_hostile_templates_are_rejected(bad):
    with pytest.raises(TemplateError):
        parse_template(bad)


def test_template_size_limit_and_unknown_params_ignored():
    with pytest.raises(TemplateError):
        parse_template(b"x" * 300_000)
    rules, _ = parse_template('{"version":1,"rules":[{"id":"trim","params":{"eval":"1+1"}}]}')
    assert rules[0]["params"] == {}


def test_template_for_other_file_warns_about_missing_columns():
    rules = [make_rule("standardize_dates", columns=["nope"]), make_rule("trim")]
    res = apply_rules(pd.DataFrame({"a": [" x"]}), rules)
    assert res.df["a"].tolist() == ["x"]
    assert res.warnings[0]["code"] == "unknown_column"


# ----------------------------------------------------------------- assistant
def test_assistant_english():
    out = parse_instruction("Remove duplicates, standardize dates, and clean whitespace.")
    ids = {r["id"]: r for r in out["rules"]}
    assert set(ids) == {"dedup", "standardize_dates", "trim"} and all(r["enabled"] for r in ids.values())


def test_assistant_chinese_and_strategy():
    out = parse_instruction("删除重复行,去掉空格,缺失值用中位数填充")
    ids = {r["id"]: r for r in out["rules"]}
    assert {"dedup", "trim", "fill_missing"} <= set(ids)
    assert ids["fill_missing"]["params"]["strategy"] == "median" and ids["fill_missing"]["enabled"]


def test_assistant_missing_without_strategy_stays_off_and_empty_rows_are_not_fill():
    out = parse_instruction("handle missing values")
    r = out["rules"][0]
    assert r["id"] == "fill_missing" and not r["enabled"] and "missing_no_strategy" in out["notes"]
    out = parse_instruction("remove empty rows")
    assert [r["id"] for r in out["rules"]] == ["drop_empty"]


def test_assistant_unknown_text_and_injection_are_harmless():
    out = parse_instruction("__import__('os').system('rm -rf /')")
    assert out["rules"] == [] and out["notes"] == ["nothing_understood"]
    assert parse_instruction("")["rules"] == []


# -------------------------------------------------------------------- report
def make_docs():
    df = read_table(sample_csv_bytes(), "s.csv").df
    rules = rules_for_sample()
    res = apply_rules(df, rules)
    before, after = analyze(df), analyze(res.df)
    doc = build_report(filename="s<script>.csv", sheet=None, before=before, after=after,
                       rules=rules, result=res, summary=compare(df, res))
    return doc, before, after, res


def test_report_json_matches_real_numbers():
    doc, before, after, res = make_docs()
    parsed = json.loads(to_json(doc))
    assert parsed["original"]["rows"] == before["stats"]["rows"] == 55
    assert parsed["cleaned"]["rows"] == after["stats"]["rows"] == len(res.df)
    assert parsed["summary"]["rows_removed"] == 55 - len(res.df)
    assert parsed["original"]["quality_score"] == before["score"]["value"]
    assert any(s["rule"] == "dedup" for s in parsed["steps"])
    assert "never removed" in parsed["outliers_note"]
    codes = {i["code"] for i in parsed["unresolved_issues"]}
    assert "outliers" in codes and "duplicates" not in codes and "whitespace" not in codes


def test_report_html_escapes_untrusted_text():
    doc, *_ = make_docs()
    html = to_html(doc, lambda k, **kw: k).decode()
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_report_log_csv_is_formula_safe():
    doc, *_ = make_docs()
    doc["steps"][0]["rule"] = "=HYPERLINK(\"http://x\")"
    text = to_log_csv(doc).decode("utf-8-sig")
    assert "'=HYPERLINK" in text


def test_report_without_cleaning_still_valid():
    df = pd.DataFrame({"a": [1, 2]})
    doc = build_report(filename="x.csv", sheet=None, before=analyze(df), after=None,
                       rules=[], result=None, summary=None)
    assert "steps" not in json.loads(to_json(doc))
    assert b"<html" in to_html(doc)


# --------------------------------------------------------------------- batch
def csv_file(name, text):
    return name, text.encode()


def test_batch_zip_applies_same_rules_and_reports_skipped_files():
    files = [
        csv_file("a.csv", "name,v\n x,1\nx,1\ny,2\n"),
        csv_file("b.csv", "name,v\n z ,3\n z,3\n"),
        csv_file("other.csv", "totally,different\n1,2\n"),
        ("broken.xlsx", b"nope"),
        ("empty.csv", b""),
    ]
    rules = [make_rule("trim"), make_rule("dedup")]
    data, summary = run_batch(files, rules, out_format="csv")
    status = {s["file"]: s["status"] for s in summary}
    assert status == {"a.csv": "ok", "b.csv": "ok", "other.csv": "skipped",
                      "broken.xlsx": "skipped", "empty.csv": "skipped"}
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(zf.namelist()) == ["batch_summary.csv", "cleaned_a.csv", "cleaned_b.csv"]
    a = read_table(zf.read("cleaned_a.csv"), "a.csv").df
    assert a["name"].tolist() == ["x", "y"]
    assert next(s for s in summary if s["file"] == "a.csv")["rows_after"] == 2


def test_batch_xlsx_output_and_name_collisions():
    files = [csv_file("d/x.csv", "a\n1\n"), csv_file("e/x.csv", "a\n2\n")]
    data, _ = run_batch(files, [make_rule("trim")], out_format="xlsx")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "cleaned_x.xlsx" in names and "cleaned_x_2.xlsx" in names


def test_batch_nothing_valid_returns_none_and_names_are_sanitised():
    data, summary = run_batch([("bad.csv", b"")], [make_rule("trim")])
    assert data is None and summary[0]["status"] == "skipped"
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("a/b\\c<>.csv") == "c__.csv"


def test_batch_limits():
    with pytest.raises(ValueError):
        run_batch([("a.csv", b"a\n1")] * 11, [])
