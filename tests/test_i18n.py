"""Every key the UI/report can ask for must exist in both languages, with the same placeholders."""
import re
import string
from pathlib import Path

import pytest

from cleaner.i18n import EN, ZH, t
from cleaner.quality import SCORE_RULES
from cleaner.rules import RULE_ORDER, STRATEGIES

ROOT = Path(__file__).resolve().parents[1]
STATIC_KEY = re.compile(r"""\bT\(\s*f?["']([a-z0-9_]+)["']""")
REPORT_KEY = re.compile(r"""\bt\(\s*["']([a-z0-9_]+)["']""")


def placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def dynamic_keys() -> set[str]:
    keys = {f"rule_{i}" for i in RULE_ORDER} | {f"desc_{i}" for i in RULE_ORDER}
    keys |= {f"pen_{k}" for k, _, _ in SCORE_RULES} | {f"score_{k}" for k, _, _ in SCORE_RULES}
    keys |= {f"st_{s}" for s in STRATEGIES} | {f"dayfirst_{v}" for v in ("auto", "day", "month")}
    keys |= {f"err_{c}" for c in ("empty_file", "corrupt_xlsx", "not_csv_text", "bad_encoding", "malformed_csv",
                                 "no_data", "too_many_rows", "too_many_cols", "unsupported_type")}
    keys |= {f"kind_{k}" for k in ("numeric", "text", "date", "datetime", "boolean", "mixed", "csv_file", "xlsx_file")}
    keys |= {f"issue_{c}" for c in ("duplicates", "missing", "blank_strings", "empty", "whitespace", "date_formats",
                                   "mixed_types", "numeric_text", "outliers", "high_unique")}
    keys |= {f"w_{c}" for c in ("dates_unparsed", "dates_ambiguous", "dates_conflict", "unknown_column",
                               "fill_skipped", "all_rows_empty", "dates_not_text")}
    keys |= {"ass_missing_no_strategy", "ass_nothing_understood", "mode_single", "mode_batch",
             "b_src_current", "b_src_template", "b_status_ok", "b_status_skipped"}
    keys |= {f"b_col_{c}" for c in ("file", "status", "detail", "rows_before", "rows_after", "cells_changed")}
    keys |= {f"step_{s}" for s in ("upload", "analyze", "rules", "preview", "download")}
    keys |= {f"rep_{k}" for k in ("rows", "cols", "missing_cells", "missing_pct", "duplicates", "whitespace_cells",
                                 "outlier_cells", "quality_score")}
    return keys


def used_static_keys() -> set[str]:
    keys = set()
    for name, rx in (("app.py", STATIC_KEY), ("cleaner/report.py", REPORT_KEY)):
        keys |= set(rx.findall((ROOT / name).read_text(encoding="utf-8")))
    return {k for k in keys if not k.endswith("_")}


def test_languages_have_identical_keys():
    assert set(EN) == set(ZH)


def test_every_used_key_is_defined():
    missing = (used_static_keys() | dynamic_keys()) - set(EN)
    assert not missing, sorted(missing)


@pytest.mark.parametrize("key", sorted(EN))
def test_placeholders_match_between_languages(key):
    assert placeholders(EN[key]) == placeholders(ZH[key]), key


def test_no_untranslated_chinese_values():
    same = [k for k in EN if EN[k] == ZH[k] and re.search(r"[A-Za-z]{4,}", EN[k])
            and k not in {"tpl_export", "dl_report_html", "dl_report_json"}]
    assert not same, same


def test_t_fallbacks_and_formatting():
    assert t("zh", "loaded", name="a", rows=1234, cols=2) == "已载入 a:1,234 行 × 2 列。"
    assert t("fr", "next") == EN["next"]
    assert t("en", "no_such_key") == "no_such_key"
    assert "{name}" in t("en", "loaded")                       # missing params never raise
