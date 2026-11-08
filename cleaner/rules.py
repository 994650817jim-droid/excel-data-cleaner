"""Cleaning rules and the engine that applies them.

Design notes
- The original frame is never modified; every rule works on a copy.
- Row identity: the index of the loaded frame (0..n-1) is kept through cleaning, so
  before/after comparisons stay correct after rows are removed.
- Column-scoped params (dates, fill, dedup subset) always refer to the ORIGINAL
  column names. `normalize_columns` runs last so these names stay valid.
- Rules are plain dicts: {"id": str, "enabled": bool, "params": dict}. No code
  from templates or from the assistant is ever executed.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

from .common import (
    blank_mask,
    format_dates,
    infer_types,
    is_texty,
    missing_effective,
    parse_dates,
    strip_series,
)

RULE_ORDER = [
    "trim", "blank_to_na", "infer_types", "drop_empty", "standardize_dates",
    "dedup", "fill_missing", "normalize_columns",
]
STRATEGIES = ("none", "drop_rows", "mean", "median", "mode", "constant")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d")
DAYFIRST_OPTIONS = ("auto", True, False)

DEFAULT_PARAMS = {
    "trim": {},
    "blank_to_na": {},
    "infer_types": {},
    "drop_empty": {},
    "standardize_dates": {"columns": [], "dayfirst": "auto", "fmt": "%Y-%m-%d"},
    "dedup": {"subset": None},
    "fill_missing": {"strategy": "none", "columns": None, "constant": ""},
    "normalize_columns": {},
}


# ------------------------------------------------------------------ rule specs


def make_rule(rule_id: str, enabled: bool = True, **params) -> dict:
    if rule_id not in RULE_ORDER:
        raise ValueError(f"unknown rule: {rule_id}")
    merged = copy.deepcopy(DEFAULT_PARAMS[rule_id])
    for k, v in params.items():
        if k in merged:
            merged[k] = v
    return {"id": rule_id, "enabled": bool(enabled), "params": merged}


def normalize_rules(rules: list[dict]) -> list[dict]:
    """Validate, fill defaults, drop duplicates (last wins) and fix the execution order."""
    by_id: dict[str, dict] = {}
    for r in rules:
        rid = r.get("id")
        if rid not in RULE_ORDER:
            raise ValueError(f"unknown rule: {rid}")
        by_id[rid] = make_rule(rid, r.get("enabled", True), **(r.get("params") or {}))
    return [by_id[i] for i in RULE_ORDER if i in by_id]


def suggest_rules(report: dict) -> list[dict]:
    """Recommended rules for a quality report. Nothing is applied by this function."""
    s = report["stats"]
    return normalize_rules([
        make_rule("trim", s["whitespace_cells"] > 0),
        make_rule("blank_to_na", s["blank_cells"] > 0),
        make_rule("infer_types", bool(s["numeric_text_columns"])),
        make_rule("drop_empty", bool(s["empty_rows"] or s["empty_cols"])),
        make_rule("standardize_dates", bool(s["date_inconsistent"]), columns=list(s["date_inconsistent"])),
        make_rule("dedup", s["duplicates"] > 0),
        make_rule("fill_missing", False),
        make_rule("normalize_columns", False),
    ])


# ------------------------------------------------- flat form used by the UI

_DAYFIRST_TO_CFG = {"auto": "auto", True: "day", False: "month"}
_CFG_TO_DAYFIRST = {v: k for k, v in _DAYFIRST_TO_CFG.items()}


def rules_to_cfg(rules: list[dict]) -> dict:
    by = {r["id"]: r for r in normalize_rules(rules)}
    params = lambda i: by[i]["params"] if i in by else DEFAULT_PARAMS[i]  # noqa: E731
    d, f, dd = params("standardize_dates"), params("fill_missing"), params("dedup")
    return {
        "on": {i: (i in by and by[i]["enabled"]) for i in RULE_ORDER},
        "dates_cols": list(d["columns"]), "dates_dayfirst": _DAYFIRST_TO_CFG[d["dayfirst"]],
        "dates_fmt": d["fmt"],
        "fill_strategy": f["strategy"], "fill_cols": list(f["columns"] or []), "fill_const": f["constant"],
        "dedup_subset": list(dd["subset"] or []),
    }


def cfg_to_rules(cfg: dict) -> list[dict]:
    rules = []
    for rid in RULE_ORDER:
        enabled = bool(cfg["on"].get(rid))
        params = {}
        if rid == "standardize_dates":
            params = {"columns": list(cfg["dates_cols"]), "dayfirst": _CFG_TO_DAYFIRST[cfg["dates_dayfirst"]],
                      "fmt": cfg["dates_fmt"]}
        elif rid == "fill_missing":
            params = {"strategy": cfg["fill_strategy"], "columns": list(cfg["fill_cols"]) or None,
                      "constant": cfg["fill_const"]}
        elif rid == "dedup":
            params = {"subset": list(cfg["dedup_subset"]) or None}
        rules.append(make_rule(rid, enabled, **params))
    return rules


# -------------------------------------------------------------------- results


@dataclass
class CleanResult:
    df: pd.DataFrame
    log: list[dict] = field(default_factory=list)
    removed: dict[int, str] = field(default_factory=dict)   # row id -> rule id
    col_map: dict[str, str | None] = field(default_factory=dict)  # original -> final name / None
    warnings: list[dict] = field(default_factory=list)


def diff_mask(a: pd.DataFrame, b: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """Cell-level change mask for rows present in both frames (index = b's rows)."""
    idx = b.index.intersection(a.index)
    cols = {}
    for ca, cb in pairs:
        sa = a.loc[idx, ca].astype("string")
        sb = b.loc[idx, cb].astype("string")
        both_na = sa.isna() & sb.isna()
        cols[cb] = ((sa != sb).fillna(True) & ~both_na).astype(bool)
    return pd.DataFrame(cols, index=idx)


# ------------------------------------------------------------------- handlers


def _h_trim(df, p, ctx):
    out = df.copy()
    for c in out.columns:
        if is_texty(out[c]):
            out[c] = strip_series(out[c])
    return out, {}


def _h_blank(df, p, ctx):
    out = df.copy()
    for c in out.columns:
        if is_texty(out[c]):
            m = blank_mask(out[c])
            if m.any():
                out[c] = out[c].mask(m)
    return out, {}


def _h_infer(df, p, ctx):
    out, converted = infer_types(df)
    return out, {"skip_diff": True, "cols_changed": len(converted), "extra": {"columns": converted}}


def _h_drop_empty(df, p, ctx):
    miss = pd.DataFrame({c: missing_effective(df[c]) for c in df.columns}, index=df.index)
    rows_all = miss.all(axis=1)
    cols_all = miss.all(axis=0)
    if rows_all.all():  # never reduce the table to nothing silently
        return df.copy(), {"warnings": [{"code": "all_rows_empty"}]}
    return df.loc[~rows_all, ~cols_all].copy(), {}


def _existing(cols, df, ctx, warnings, rule):
    out = []
    for c in cols or []:
        if c in df.columns:
            out.append(c)
        elif c not in ctx["orig_cols"]:
            warnings.append({"code": "unknown_column", "rule": rule, "column": c})
    return out


def _h_dedup(df, p, ctx):
    warns: list[dict] = []
    subset = _existing(p.get("subset"), df, ctx, warns, "dedup") or None
    return df.drop_duplicates(subset=subset).copy(), {"warnings": warns}


def _h_dates(df, p, ctx):
    out = df.copy()
    warns: list[dict] = []
    fmt, dayfirst = p["fmt"], p["dayfirst"]
    for c in _existing(p["columns"], df, ctx, warns, "standardize_dates"):
        s = out[c]
        if is_datetime64_any_dtype(s):
            out[c] = format_dates(s, fmt)
            continue
        if not is_texty(s):
            warns.append({"code": "dates_not_text", "column": c})
            continue
        is_str = s.map(lambda v: isinstance(v, str))
        sv = s[is_str & ~blank_mask(s)]
        if sv.empty:
            continue
        parsed, info = parse_dates(sv, dayfirst)
        ok = parsed.notna()
        if ok.any():
            formatted = format_dates(parsed[ok], fmt)
            new = s.copy()
            new.loc[formatted.index] = formatted
            out[c] = new
        bad = sv[~ok]
        if len(bad):
            warns.append({"code": "dates_unparsed", "column": c, "count": int(len(bad)),
                          "examples": [str(x) for x in bad.head(3)]})
        if info["ambiguous"]:
            warns.append({"code": "dates_ambiguous", "column": c, "count": info["ambiguous"]})
        if info["conflict"]:
            warns.append({"code": "dates_conflict", "column": c})
    return out, {"warnings": warns}


def _h_fill(df, p, ctx):
    strategy = p["strategy"]
    warns: list[dict] = []
    if strategy == "none":
        return df.copy(), {}
    cols = _existing(p["columns"], df, ctx, warns, "fill_missing") if p["columns"] else list(df.columns)
    if strategy == "drop_rows":
        return df.dropna(subset=cols).copy(), {"warnings": warns}
    out = df.copy()
    skipped = []
    for c in cols:
        s = out[c]
        if not s.isna().any():
            continue
        numeric = is_numeric_dtype(s) and not is_bool_dtype(s)
        if strategy in ("mean", "median"):
            if not numeric:
                skipped.append(c)
                continue
            val = s.mean() if strategy == "mean" else s.median()
            if pd.isna(val):
                continue
            out[c] = _fill_numeric(s, val)
        elif strategy == "mode":
            m = s.dropna().mode()
            if len(m):
                out[c] = s.fillna(m.iloc[0])
        elif strategy == "constant":
            const = p["constant"]
            if numeric:
                try:
                    num = float(const)
                except ValueError:
                    skipped.append(c)
                    continue
                out[c] = _fill_numeric(s, num)
            elif is_texty(s):
                out[c] = s.fillna(const)
            else:
                skipped.append(c)
    if skipped:
        warns.append({"code": "fill_skipped", "columns": skipped, "strategy": strategy})
    return out, {"warnings": warns}


def _fill_numeric(s: pd.Series, val: float) -> pd.Series:
    integer_col = str(s.dtype).lower().startswith("int")
    if integer_col and float(val).is_integer():
        return s.fillna(int(val))
    return s.astype("float64").fillna(float(val))


_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_name(name: str) -> str:
    return _NON_WORD.sub("_", str(name).strip().lower()).strip("_")


def _h_normalize(df, p, ctx):
    names: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(df.columns, 1):
        base = normalize_name(c) or f"column_{i}"
        new, n = base, 2
        while new in seen:
            new = f"{base}_{n}"
            n += 1
        seen.add(new)
        names.append(new)
    out = df.copy()
    out.columns = names
    return out, {"rename": dict(zip(df.columns, names))}


HANDLERS = {
    "trim": _h_trim, "blank_to_na": _h_blank, "infer_types": _h_infer,
    "drop_empty": _h_drop_empty, "standardize_dates": _h_dates, "dedup": _h_dedup,
    "fill_missing": _h_fill, "normalize_columns": _h_normalize,
}


# --------------------------------------------------------------------- engine


def _sequence(rules: list[dict]) -> list[tuple[dict, int]]:
    """Execution plan. Dedup also runs again after a filling strategy: filling can
    make rows identical, and a second pass keeps the whole pipeline idempotent."""
    enabled = {r["id"]: r for r in rules if r["enabled"]}
    seq: list[tuple[dict, int]] = []
    for rid in RULE_ORDER:
        if rid in enabled:
            seq.append((enabled[rid], 1))
            if (rid == "fill_missing" and "dedup" in enabled
                    and enabled[rid]["params"]["strategy"] not in ("none", "drop_rows")):
                seq.append((enabled["dedup"], 2))
    return seq


def apply_rules(df: pd.DataFrame, rules: list[dict]) -> CleanResult:
    rules = normalize_rules(rules)
    ctx = {"orig_cols": list(df.columns)}
    work = df.copy()
    col_map: dict[str, str | None] = {c: c for c in df.columns}
    result = CleanResult(df=work, col_map=col_map)

    for rule, phase in _sequence(rules):
        before = work
        new, info = HANDLERS[rule["id"]](before, rule["params"], ctx)
        entry = {"rule": rule["id"], "phase": phase, "params": rule["params"],
                 "rows_removed": 0, "cols_removed": 0, "cells_changed": 0,
                 "filled": 0, "cols_changed": info.get("cols_changed", 0)}
        entry.update(info.get("extra", {}))

        for rid in before.index.difference(new.index):
            result.removed[int(rid)] = rule["id"]
        entry["rows_removed"] = int(len(before) - len(new))

        if "rename" in info:
            mapping = info["rename"]
            for o, cur in list(col_map.items()):
                if cur is not None:
                    col_map[o] = mapping[cur]
            entry["cols_changed"] = sum(1 for a, b in mapping.items() if a != b)
        else:
            kept = [c for c in new.columns if c in before.columns]
            entry["cols_removed"] = len(before.columns) - len(kept)
            for o, cur in list(col_map.items()):
                if cur is not None and cur not in new.columns:
                    col_map[o] = None
            if not info.get("skip_diff") and len(new):
                mask = diff_mask(before, new, [(c, c) for c in kept])
                entry["cells_changed"] = int(mask.to_numpy().sum())
                if rule["id"] == "fill_missing":
                    was_na = before.loc[new.index, kept].isna()
                    now_ok = new[kept].notna()
                    entry["filled"] = int((was_na & now_ok).to_numpy().sum())
        for w in info.get("warnings", []):
            result.warnings.append(w)
        result.log.append(entry)
        work = new

    result.df = work
    return result


def compare(orig: pd.DataFrame, result: CleanResult) -> dict:
    """Before/after statistics and a cell-change mask aligned with the cleaned frame."""
    final = result.df
    pairs = [(o, n) for o, n in result.col_map.items() if n is not None]
    mask = diff_mask(orig, final, pairs) if len(final) else pd.DataFrame(columns=[n for _, n in pairs])
    missing_filled = 0
    if len(final):
        for o, n in pairs:
            was = missing_effective(orig.loc[final.index, o])
            now = missing_effective(final[n])
            missing_filled += int((was & ~now).sum())
    return {
        "rows_before": len(orig), "rows_after": len(final),
        "rows_removed": len(orig) - len(final),
        "cols_before": orig.shape[1], "cols_after": final.shape[1],
        "cols_removed": sum(1 for v in result.col_map.values() if v is None),
        "cols_renamed": sum(1 for o, n in result.col_map.items() if n is not None and o != n),
        "cells_changed": int(mask.to_numpy().sum()) if len(mask) else 0,
        "missing_filled": missing_filled,
        "mask": mask,
    }
