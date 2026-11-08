"""Reading and writing tables. Everything works on in-memory bytes (no temp files)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import pandas as pd

from .common import infer_types, is_texty

MAX_ROWS = 200_000
MAX_COLS = 500
ENCODINGS = ("utf-8-sig", "gb18030")
XLSX_MAGIC = b"PK\x03\x04"
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class FileReadError(ValueError):
    """Raised with a stable `code` so the UI can show a translated message."""

    def __init__(self, code: str, **data):
        super().__init__(code)
        self.code = code
        self.data = data


@dataclass
class LoadResult:
    df: pd.DataFrame
    kind: str  # "csv" | "xlsx"
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    converted_columns: list[str] = field(default_factory=list)


def detect_kind(name: str, raw: bytes) -> str:
    n = (name or "").lower()
    if not raw:
        raise FileReadError("empty_file")
    if n.endswith(".csv"):
        if raw[:4] == XLSX_MAGIC:
            raise FileReadError("not_csv_text")
        return "csv"
    if n.endswith(".xlsx"):
        return "xlsx"
    raise FileReadError("unsupported_type")


def list_sheets(raw: bytes) -> list[str]:
    if not raw:
        raise FileReadError("empty_file")
    try:
        with pd.ExcelFile(io.BytesIO(raw), engine="openpyxl") as xl:
            return list(xl.sheet_names)
    except Exception as e:  # corrupt zip, wrong format, ...
        raise FileReadError("corrupt_xlsx", detail=str(e)[:200]) from e


def _decode(raw: bytes) -> tuple[str, str]:
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise FileReadError("bad_encoding")


def _sniff(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _finalize(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    if df.shape[1] > MAX_COLS:
        raise FileReadError("too_many_cols", limit=MAX_COLS)
    if df.shape[0] > MAX_ROWS:
        raise FileReadError("too_many_rows", limit=MAX_ROWS)
    if df.shape[0] == 0 or df.shape[1] == 0:
        raise FileReadError("no_data")
    df = df.reset_index(drop=True)
    df, converted = infer_types(df)
    return df, converted


def read_table(raw: bytes, name: str, sheet: str | None = None) -> LoadResult:
    kind = detect_kind(name, raw)
    if kind == "csv":
        text, enc = _decode(raw)
        if not text.strip():
            raise FileReadError("empty_file")
        delim = _sniff(text)
        try:
            # dtype=str: nothing is guessed here, so leading zeros and long numbers survive.
            df = pd.read_csv(io.StringIO(text), sep=delim, dtype=str)
        except pd.errors.EmptyDataError as e:
            raise FileReadError("empty_file") from e
        except (pd.errors.ParserError, csv.Error) as e:
            raise FileReadError("malformed_csv", detail=str(e)[:200]) from e
        df, converted = _finalize(df)
        return LoadResult(df, "csv", encoding=enc, delimiter=delim, converted_columns=converted)

    sheets = list_sheets(raw)
    chosen = sheet if sheet in sheets else sheets[0]
    try:
        # dtype=object keeps each cell's native Excel type; the default re-parses text
        # columns and would turn the text "00123" into the number 123.
        df = pd.read_excel(io.BytesIO(raw), sheet_name=chosen, engine="openpyxl",
                           dtype=object).infer_objects()
    except Exception as e:
        raise FileReadError("corrupt_xlsx", detail=str(e)[:200]) from e
    df, converted = _finalize(df)
    return LoadResult(df, "xlsx", sheet=chosen, sheets=sheets, converted_columns=converted)


# --------------------------------------------------------------------- export


def neutralize_formulas(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Prefix text that spreadsheets would execute (= + - @ ...) with a quote.

    Numbers are untouched; only string cells and column names are changed.
    Returns the new frame and the number of cells/headers modified.
    """
    out = df.copy()
    count = 0

    def fix(v):
        nonlocal count
        if isinstance(v, str) and v.startswith(DANGEROUS_PREFIXES):
            count += 1
            return "'" + v
        return v

    for c in out.columns:
        if is_texty(out[c]):
            out[c] = out[c].map(fix)
    new_cols = [fix(c) for c in out.columns]
    out.columns = new_cols
    return out, count


def export_csv(df: pd.DataFrame, sanitize: bool = True) -> bytes:
    data = neutralize_formulas(df)[0] if sanitize else df
    # utf-8-sig so Excel on Windows opens Chinese text correctly
    return data.to_csv(index=False).encode("utf-8-sig")


def export_xlsx(df: pd.DataFrame, sanitize: bool = True) -> bytes:
    """Write with openpyxl's streaming (write-only) mode: pandas.to_excel needs ~5x the
    memory and was measured at ~110 s for 300k rows x 8 columns."""
    from openpyxl import Workbook

    data = neutralize_formulas(df)[0] if sanitize else df
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Sheet1")
    ws.append([str(c) for c in data.columns])
    obj = data.astype(object).where(data.notna(), None)
    for row in obj.itertuples(index=False, name=None):
        ws.append([v.to_pydatetime() if isinstance(v, pd.Timestamp) else v for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
