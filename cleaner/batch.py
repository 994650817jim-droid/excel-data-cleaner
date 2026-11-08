"""Batch cleaning: one rule set applied to several files with the same structure."""
from __future__ import annotations

import io
import os
import re
import zipfile

import pandas as pd

from .fileio import FileReadError, export_csv, export_xlsx, neutralize_formulas, read_table
from .rules import apply_rules, compare

MAX_FILES = 10
MAX_TOTAL_BYTES = 100 * 1024 * 1024


def safe_name(name: str) -> str:
    base = os.path.basename((name or "file").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ]", "_", base, flags=re.UNICODE).strip(" .") or "file"
    return base[:80]


def run_batch(files: list[tuple[str, bytes]], rules: list[dict], out_format: str = "xlsx",
              sanitize: bool = True) -> tuple[bytes | None, list[dict]]:
    """Returns (zip bytes or None, per-file summary rows).

    Files must have the same set of column names as the first readable file;
    others are skipped (and reported), never silently merged or altered.
    """
    summary: list[dict] = []
    if len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files")
    if sum(len(b) for _, b in files) > MAX_TOTAL_BYTES:
        raise ValueError("batch too large")

    ref_cols: set[str] | None = None
    used: set[str] = set()
    buf = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, raw in files:
            row = {"file": name, "status": "ok", "detail": "", "rows_before": 0, "rows_after": 0,
                   "cells_changed": 0}
            try:
                lr = read_table(raw, name)
            except FileReadError as e:
                row.update(status="skipped", detail=f"read error: {e.code}")
                summary.append(row)
                continue
            cols = {str(c).strip() for c in lr.df.columns}
            if ref_cols is None:
                ref_cols = cols
            elif cols != ref_cols:
                missing = sorted(ref_cols - cols)[:5]
                extra = sorted(cols - ref_cols)[:5]
                row.update(status="skipped", detail=f"columns differ: missing {missing}, extra {extra}")
                summary.append(row)
                continue
            res = apply_rules(lr.df, rules)
            stats = compare(lr.df, res)
            data = export_xlsx(res.df, sanitize) if out_format == "xlsx" else export_csv(res.df, sanitize)
            stem = os.path.splitext(safe_name(name))[0]
            out_name, n = f"cleaned_{stem}.{out_format}", 2
            while out_name in used:
                out_name, n = f"cleaned_{stem}_{n}.{out_format}", n + 1
            used.add(out_name)
            zf.writestr(out_name, data)
            written += 1
            row.update(rows_before=stats["rows_before"], rows_after=stats["rows_after"],
                       cells_changed=stats["cells_changed"], detail=out_name)
            summary.append(row)
        s_df, _ = neutralize_formulas(pd.DataFrame(summary))
        zf.writestr("batch_summary.csv", s_df.to_csv(index=False).encode("utf-8-sig"))
    return (buf.getvalue() if written else None), summary
