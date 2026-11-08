"""Data-quality analysis and the (public) quality score."""
from __future__ import annotations

import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

from .common import (
    blank_mask,
    date_signature,
    infer_series,
    is_texty,
    missing_effective,
    numeric_like,
    parse_dates,
    space_mask,
    strip_series,
)

DATE_MIN_SHARE = 0.8
OUTLIER_MIN_ROWS = 8
OUTLIER_IQR_K = 1.5
HIGH_UNIQUE_RATIO = 0.95
HIGH_UNIQUE_MIN_ROWS = 20
MIXED_MINORITY_SHARE = 0.05

# Score = 100 - sum(penalties). Each penalty is capped; caps sum to exactly 100.
SCORE_RULES = [
    # key, cap, points per 1% of the ratio (or per item), description key
    ("missing", 30, "1 point per 1% of cells that are missing or blank"),
    ("duplicates", 20, "1 point per 1% of rows that are exact duplicates"),
    ("whitespace", 10, "1 point per 1% of cells with leading/trailing spaces"),
    ("date_formats", 15, "5 points per date column with mixed formats"),
    ("mixed_types", 10, "5 points per column mixing numbers and text"),
    ("outliers", 10, "1 point per 1% of numeric cells flagged as suspicious"),
    ("empty", 5, "1 point per 1% of rows/columns that are completely empty"),
]


def _py(v):
    if hasattr(v, "item"):
        return v.item()
    return v


def _text_profile(name: str, s: pd.Series) -> dict:
    """Extra checks for text/object columns: dates, mixed types, numeric text."""
    info = {"kind": "text", "date_formats": [], "date_inconsistent": False,
            "date_unparsed": 0, "mixed": False, "numeric_text": False}
    nn = s.dropna()
    if nn.empty:
        return info
    # python-level mixed types (only possible in object columns)
    if s.dtype == object and nn.map(type).nunique() > 1:
        info["kind"], info["mixed"] = "mixed", True
        return info
    sv = nn[nn.map(lambda x: isinstance(x, str))]
    sv = sv[~blank_mask(sv)]
    if sv.empty:
        return info
    # dates
    sigs = sv.map(date_signature)
    share = sigs.notna().mean()
    if share >= DATE_MIN_SHARE:
        parsed, _ = parse_dates(sv[sigs.notna()])
        ok_share = parsed.notna().sum() / len(sv)
        if ok_share >= DATE_MIN_SHARE:
            fmts = sorted(set(sigs.dropna()))
            info.update(kind="date", date_formats=fmts, date_inconsistent=len(fmts) > 1,
                        date_unparsed=int(len(sv) - parsed.notna().sum()))
            return info
    # numbers stored as text
    stripped = strip_series(sv)
    nl = numeric_like(stripped)
    share_num = nl.mean()
    if 0 < share_num < 1 and min(share_num, 1 - share_num) >= MIXED_MINORITY_SHARE:
        info["kind"], info["mixed"] = "mixed", True
    elif share_num == 1:
        cleaned = strip_series(s)
        cleaned = cleaned.mask(blank_mask(cleaned))
        info["numeric_text"] = infer_series(cleaned) is not None
    return info


def _outliers(s: pd.Series) -> dict | None:
    nn = pd.to_numeric(s.dropna(), errors="coerce").dropna().astype(float)
    if len(nn) < OUTLIER_MIN_ROWS:
        return None
    q1, q3 = nn.quantile(0.25), nn.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return None
    lo, hi = q1 - OUTLIER_IQR_K * iqr, q3 + OUTLIER_IQR_K * iqr
    flagged = nn[(nn < lo) | (nn > hi)]
    if flagged.empty:
        return None
    return {"count": int(len(flagged)), "lower": float(lo), "upper": float(hi),
            "examples": [float(x) for x in flagged.head(5)],
            "row_ids": [int(i) for i in flagged.index[:200]]}


def profile_column(name, s: pd.Series, n_rows: int) -> dict:
    missing = int(s.isna().sum())
    blank = int(blank_mask(s).sum())
    nn = s.dropna()
    unique = int(nn.nunique()) if len(nn) else 0
    prof = {
        "name": str(name),
        "dtype": str(s.dtype),
        "kind": "text",
        "missing": missing,
        "blank": blank,
        "missing_pct": round(100 * (missing + blank) / n_rows, 2) if n_rows else 0.0,
        "unique": unique,
        "unique_ratio": round(unique / len(nn), 4) if len(nn) else 0.0,
        "whitespace": int(space_mask(s).sum()),
        "date_formats": [], "date_inconsistent": False, "date_unparsed": 0,
        "mixed": False, "numeric_text": False,
        "outliers": None, "high_unique": False,
    }
    if is_bool_dtype(s):
        prof["kind"] = "boolean"
    elif is_numeric_dtype(s):
        prof["kind"] = "numeric"
        prof["outliers"] = _outliers(s)
    elif is_datetime64_any_dtype(s):
        prof["kind"] = "datetime"
    elif is_texty(s):
        prof.update(_text_profile(name, s))
        if (prof["kind"] == "text" and len(nn) >= HIGH_UNIQUE_MIN_ROWS
                and prof["unique_ratio"] >= HIGH_UNIQUE_RATIO):
            prof["high_unique"] = True
    return prof


def analyze(df: pd.DataFrame) -> dict:
    rows, cols = df.shape
    cells = rows * cols
    columns = [profile_column(c, df[c], rows) for c in df.columns]

    miss_mask = pd.DataFrame({c: missing_effective(df[c]) for c in df.columns}) if cols else pd.DataFrame()
    missing_cells = int(miss_mask.to_numpy().sum()) if cols else 0
    empty_rows = int(miss_mask.all(axis=1).sum()) if cols else 0
    empty_cols = [str(c) for c in df.columns if miss_mask[c].all()] if rows else []
    duplicates = int(df.duplicated().sum()) if rows else 0
    ws_cells = sum(c["whitespace"] for c in columns)
    blank_cells = sum(c["blank"] for c in columns)
    numeric_cells = sum(rows - c["missing"] for c in columns if c["kind"] == "numeric")
    outlier_cells = sum(c["outliers"]["count"] for c in columns if c["outliers"])

    stats = {
        "rows": rows, "cols": cols, "cells": cells,
        "missing_cells": missing_cells,
        "missing_nan": int(df.isna().sum().sum()) if cols else 0,
        "blank_cells": blank_cells,
        "missing_pct": round(100 * missing_cells / cells, 2) if cells else 0.0,
        "duplicates": duplicates,
        "empty_rows": empty_rows, "empty_cols": empty_cols,
        "whitespace_cells": ws_cells,
        "date_columns": [c["name"] for c in columns if c["kind"] == "date"],
        "date_inconsistent": [c["name"] for c in columns if c["date_inconsistent"]],
        "mixed_columns": [c["name"] for c in columns if c["mixed"]],
        "numeric_text_columns": [c["name"] for c in columns if c["numeric_text"]],
        "outlier_columns": [c["name"] for c in columns if c["outliers"]],
        "outlier_cells": outlier_cells,
        "high_unique_columns": [c["name"] for c in columns if c["high_unique"]],
    }
    report = {"stats": stats, "columns": columns}
    report["score"] = score(stats, numeric_cells)
    report["issues"] = issues(stats)
    report["kind_counts"] = _kind_counts(columns)
    return report


def _kind_counts(columns: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for c in columns:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    return counts


def score(stats: dict, numeric_cells: int) -> dict:
    """Public, deterministic score. See SCORE_RULES for the rules."""
    rows, cols, cells = stats["rows"], stats["cols"], stats["cells"]
    pct = lambda a, b: 100 * a / b if b else 0.0  # noqa: E731
    raw = {
        "missing": pct(stats["missing_cells"], cells),
        "duplicates": pct(stats["duplicates"], rows),
        "whitespace": pct(stats["whitespace_cells"], cells),
        "date_formats": 5 * len(stats["date_inconsistent"]),
        "mixed_types": 5 * len(stats["mixed_columns"]),
        "outliers": pct(stats["outlier_cells"], numeric_cells),
        "empty": pct(stats["empty_rows"] + len(stats["empty_cols"]), rows + cols),
    }
    penalties = {}
    total = 0.0
    for key, cap, _ in SCORE_RULES:
        pts = round(min(cap, raw[key]), 2)
        penalties[key] = {"points": pts, "cap": cap, "raw": round(raw[key], 2)}
        total += pts
    return {"value": round(max(0.0, 100 - total), 1), "penalties": penalties}


def issues(stats: dict) -> list[dict]:
    out = []

    def add(code, severity, count, columns=None):
        out.append({"code": code, "severity": severity, "count": int(count), "columns": columns or []})

    if stats["duplicates"]:
        add("duplicates", "warning", stats["duplicates"])
    if stats["missing_cells"]:
        add("missing", "warning", stats["missing_cells"])
    if stats["blank_cells"]:
        add("blank_strings", "info", stats["blank_cells"])
    if stats["empty_rows"] or stats["empty_cols"]:
        add("empty", "info", stats["empty_rows"] + len(stats["empty_cols"]), stats["empty_cols"])
    if stats["whitespace_cells"]:
        add("whitespace", "warning", stats["whitespace_cells"])
    if stats["date_inconsistent"]:
        add("date_formats", "warning", len(stats["date_inconsistent"]), stats["date_inconsistent"])
    if stats["mixed_columns"]:
        add("mixed_types", "warning", len(stats["mixed_columns"]), stats["mixed_columns"])
    if stats["numeric_text_columns"]:
        add("numeric_text", "info", len(stats["numeric_text_columns"]), stats["numeric_text_columns"])
    if stats["outlier_cells"]:
        add("outliers", "info", stats["outlier_cells"], stats["outlier_columns"])
    if stats["high_unique_columns"]:
        add("high_unique", "info", len(stats["high_unique_columns"]), stats["high_unique_columns"])
    return out


def unresolved(report: dict) -> list[dict]:
    """Issues that remain after cleaning (outliers are never removed automatically)."""
    keep = {"duplicates", "missing", "blank_strings", "empty", "whitespace", "date_formats",
            "mixed_types", "numeric_text", "outliers"}
    return [i for i in report["issues"] if i["code"] in keep]
