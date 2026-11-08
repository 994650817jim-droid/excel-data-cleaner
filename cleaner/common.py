"""Small shared helpers (text masks, safe numeric inference, date parsing)."""
from __future__ import annotations

import re

import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype, is_string_dtype

# ---------------------------------------------------------------- text helpers


def is_texty(s: pd.Series) -> bool:
    return s.dtype == object or is_string_dtype(s)


def blank_mask(s: pd.Series) -> pd.Series:
    """True where the cell is a string that is empty or whitespace-only."""
    if s.dtype == object:
        return s.map(lambda x: isinstance(x, str) and not x.strip()).astype(bool)
    if is_string_dtype(s):
        return s.str.strip().eq("").fillna(False).astype(bool)
    return pd.Series(False, index=s.index)


def space_mask(s: pd.Series) -> pd.Series:
    """True where a non-blank string has leading/trailing whitespace."""
    if s.dtype == object:
        return s.map(lambda x: isinstance(x, str) and bool(x.strip()) and x != x.strip()).astype(bool)
    if is_string_dtype(s):
        stripped = s.str.strip()
        return (s.notna() & (stripped != s) & stripped.ne("")).fillna(False).astype(bool)
    return pd.Series(False, index=s.index)


def strip_series(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        return s.map(lambda x: x.strip() if isinstance(x, str) else x)
    if is_string_dtype(s):
        return s.str.strip()
    return s


def missing_effective(s: pd.Series) -> pd.Series:
    """NaN or blank string (what a user would call "missing")."""
    return s.isna() | blank_mask(s)


# --------------------------------------------------------- numeric inference

_LEAD_ZERO = re.compile(r"^[+-]?0\d")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
MAX_SAFE_DIGITS = 15


def numeric_like(s: pd.Series) -> pd.Series:
    """Boolean mask: strings that look like plain numbers (after strip)."""
    sv = s.dropna()
    sv = sv[sv.map(lambda x: isinstance(x, str))]
    if sv.empty:
        return pd.Series(False, index=s.index)
    m = sv.str.strip().str.fullmatch(_FLOAT)
    return m.reindex(s.index, fill_value=False).fillna(False).astype(bool)


def infer_series(s: pd.Series) -> pd.Series | None:
    """Convert a text column to numbers only when that is lossless.

    Returns None when the column must stay text: leading zeros (IDs, phone
    numbers), more than 15 digits (precision loss), surrounding whitespace,
    thousands separators, or any non-numeric value.
    """
    if not is_texty(s) or is_bool_dtype(s):
        return None
    nn = s.dropna()
    if nn.empty or not all(isinstance(v, str) for v in nn):
        return None
    if (nn != nn.str.strip()).any():
        return None
    if nn.str.match(_LEAD_ZERO).any():
        return None
    if not nn.str.fullmatch(_FLOAT).all():
        return None
    if (nn.str.replace(r"[^0-9]", "", regex=True).str.len() > MAX_SAFE_DIGITS).any():
        return None
    if nn.str.fullmatch(_INT).all():
        return pd.to_numeric(s).astype("Int64") if s.isna().any() else pd.to_numeric(s).astype("int64")
    return pd.to_numeric(s).astype("float64")


def infer_types(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    converted = []
    for c in out.columns:
        new = infer_series(out[c])
        if new is not None:
            out[c] = new
            converted.append(c)
    return out, converted


# ---------------------------------------------------------------------- dates

_NUMDATE = re.compile(
    r"^(\d{1,4})([-/.])(\d{1,2})\2(\d{1,4})(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?$"
)
_DMY_NAME = re.compile(r"^\d{1,2}[ \-/]([A-Za-z]{3,9})\.?[ \-/,]+\d{2,4}$")
_MDY_NAME = re.compile(r"^([A-Za-z]{3,9})\.? \d{1,2},? \d{2,4}$")
_DM_PATTERN = r"^(\d{1,2})[-/.](\d{1,2})[-/.]\d{2,4}"


def date_signature(v: str) -> str | None:
    """Describe the *shape* of a date string, or None if it is not date-like."""
    v = v.strip()
    m = _NUMDATE.match(v)
    if m:
        a, sep = m.group(1), m.group(2)
        return f"YYYY{sep}MM{sep}DD" if len(a) == 4 else f"DD{sep}MM{sep}YYYY or MM{sep}DD{sep}YYYY"
    if _DMY_NAME.match(v):
        return "D Mon YYYY"
    if _MDY_NAME.match(v):
        return "Mon D, YYYY"
    return None


def parse_dates(sv: pd.Series, dayfirst: str | bool = "auto") -> tuple[pd.Series, dict]:
    """Parse date strings. Returns (datetime64 series with NaT, info).

    Day/month order is decided per value: a first token > 12 means day-first, a
    second token > 12 means month-first, otherwise the column default applies
    (explicit user choice, or the majority of unambiguous values, else month-first).
    ISO-style (year-first) and month-name dates are never ambiguous.
    """
    stripped = sv.map(lambda v: v.strip() if isinstance(v, str) else v).astype(object)
    ext = stripped.str.extract(_DM_PATTERN)
    a = pd.to_numeric(ext[0], errors="coerce")
    b = pd.to_numeric(ext[1], errors="coerce")
    matched = a.notna()
    day_ev = matched & (a > 12)
    month_ev = matched & (b > 12)
    if dayfirst == "auto":
        default = bool(day_ev.sum() > month_ev.sum())
    else:
        default = bool(dayfirst)
    if dayfirst == "auto":
        flag = day_ev | (matched & ~month_ev & default)
    else:
        flag = matched & default
    ambiguous = int((matched & ~day_ev & ~month_ev & (a != b)).sum())
    info = {
        "ambiguous": ambiguous if (dayfirst == "auto" and day_ev.sum() + month_ev.sum() == 0) else 0,
        "conflict": bool(day_ev.any() and month_ev.any()),
    }
    parts = []
    for value in (True, False):
        idx = flag == value
        sub = stripped[idx]
        if sub.notna().any():
            try:
                parts.append(pd.to_datetime(sub, format="mixed", dayfirst=value, errors="coerce"))
            except (ValueError, TypeError):
                parts.append(pd.Series(pd.NaT, index=sub.index, dtype="datetime64[ns]"))
    if not parts:
        return pd.Series(pd.NaT, index=sv.index, dtype="datetime64[ns]"), info
    out = pd.concat(parts).reindex(sv.index)
    return out, info


def format_dates(parsed: pd.Series, fmt: str) -> pd.Series:
    """strftime, keeping the time part if any value has one and fmt is date-only."""
    ok = parsed.dropna()
    if fmt == "%Y-%m-%d" and not ok.empty and (ok != ok.dt.normalize()).any():
        fmt = "%Y-%m-%d %H:%M:%S"
    return parsed.dt.strftime(fmt)
