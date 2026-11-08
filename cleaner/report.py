"""Cleaning report (JSON / HTML / CSV). Only standard library + pandas, no extra deps."""
from __future__ import annotations

import datetime as dt
import html
import io
import json

import pandas as pd

from . import __version__
from .fileio import neutralize_formulas
from .quality import SCORE_RULES, unresolved


def _brief(report: dict) -> dict:
    s = report["stats"]
    return {
        "rows": s["rows"], "cols": s["cols"], "missing_cells": s["missing_cells"],
        "missing_pct": s["missing_pct"], "duplicates": s["duplicates"],
        "empty_rows": s["empty_rows"], "empty_cols": s["empty_cols"],
        "whitespace_cells": s["whitespace_cells"], "outlier_cells": s["outlier_cells"],
        "date_columns": s["date_columns"], "date_inconsistent": s["date_inconsistent"],
        "mixed_columns": s["mixed_columns"], "quality_score": report["score"]["value"],
        "score_penalties": {k: v["points"] for k, v in report["score"]["penalties"].items()},
    }


def build_report(*, filename: str, sheet: str | None, before: dict, after: dict | None,
                 rules: list[dict], result, summary: dict | None) -> dict:
    doc = {
        "tool": "Excel Data Cleaner", "version": __version__,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"filename": filename, "sheet": sheet},
        "original": _brief(before),
        "rules": [{"id": r["id"], "enabled": r["enabled"], "params": r["params"]} for r in rules],
        "score_rules": [{"key": k, "max_penalty": cap, "rule": text} for k, cap, text in SCORE_RULES],
        "outliers_note": "Suspicious values are only flagged. They are never removed or changed.",
    }
    if result is not None and after is not None and summary is not None:
        doc["steps"] = [
            {k: v for k, v in e.items() if k != "params"} for e in result.log
        ]
        doc["summary"] = {k: v for k, v in summary.items() if k != "mask"}
        doc["cleaned"] = _brief(after)
        doc["warnings"] = result.warnings
        doc["unresolved_issues"] = unresolved(after)
        doc["removed_rows"] = {"count": len(result.removed),
                               "by_rule": _count_values(result.removed.values())}
        doc["column_map"] = {k: v for k, v in result.col_map.items()}
    return doc


def _count_values(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def to_json(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def log_dataframe(doc: dict) -> pd.DataFrame:
    rows = []
    for s in doc.get("steps", []):
        rows.append({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                     for k, v in s.items()})
    return pd.DataFrame(rows)


def to_log_csv(doc: dict) -> bytes:
    df = log_dataframe(doc)
    if df.empty:
        df = pd.DataFrame({"info": ["no cleaning steps were run"]})
    df, _ = neutralize_formulas(df)
    return df.to_csv(index=False).encode("utf-8-sig")


def to_html(doc: dict, t=lambda k, **kw: k) -> bytes:
    """Self-contained HTML. All dynamic text is escaped."""
    e = html.escape

    def table(rows: list[tuple], header: tuple) -> str:
        head = "".join(f"<th>{e(str(h))}</th>" for h in header)
        body = "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def kv(brief: dict) -> str:
        keys = ["rows", "cols", "missing_cells", "missing_pct", "duplicates", "whitespace_cells",
                "outlier_cells", "quality_score"]
        return table([(t(f"rep_{k}"), brief[k]) for k in keys], (t("rep_metric"), t("rep_value")))

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{e(t('rep_title'))}</title>",
        "<style>body{font:15px system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1f2937}"
        "table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}"
        "th,td{border:1px solid #e5e7eb;padding:6px 10px;text-align:left;font-size:14px}"
        "th{background:#f3f4f6}h1{margin-bottom:0}h2{margin-top:2rem}.m{color:#6b7280}</style></head><body>",
        f"<h1>{e(t('rep_title'))}</h1>",
        f"<p class='m'>{e(doc['source']['filename'] or '-')} · {e(str(doc['source']['sheet'] or '-'))} · "
        f"{e(doc['generated_utc'])} · v{e(doc['version'])}</p>",
        f"<h2>{e(t('rep_original'))}</h2>", kv(doc["original"]),
        f"<h2>{e(t('rep_rules'))}</h2>",
        table([(t(f"rule_{r['id']}"), t("on") if r["enabled"] else t("off"),
                json.dumps(r["params"], ensure_ascii=False)) for r in doc["rules"]],
              (t("rep_rule"), t("rep_state"), t("rep_params"))),
    ]
    if "steps" in doc:
        parts += [
            f"<h2>{e(t('rep_results'))}</h2>",
            table([(t(f"rule_{s['rule']}"), s["rows_removed"], s["cols_removed"], s["cells_changed"],
                    s["filled"], s["cols_changed"]) for s in doc["steps"]],
                  (t("rep_rule"), t("rep_rows_removed"), t("rep_cols_removed"), t("rep_cells_changed"),
                   t("rep_filled"), t("rep_cols_changed"))),
            f"<h2>{e(t('rep_cleaned'))}</h2>", kv(doc["cleaned"]),
            f"<h2>{e(t('rep_warnings'))}</h2>",
        ]
        warn = [(w.get("code"), json.dumps({k: v for k, v in w.items() if k != "code"}, ensure_ascii=False))
                for w in doc["warnings"]]
        unres = [(i["code"], i["count"], ", ".join(i["columns"])) for i in doc["unresolved_issues"]]
        parts.append(table(warn, ("code", "detail")) if warn else f"<p>{e(t('none'))}</p>")
        parts.append(f"<h2>{e(t('rep_unresolved'))}</h2>")
        parts.append(table(unres, ("code", "count", "columns")) if unres else f"<p>{e(t('none'))}</p>")
    parts.append(f"<h2>{e(t('rep_score_rules'))}</h2>")
    parts.append(table([(r["key"], r["max_penalty"], r["rule"]) for r in doc["score_rules"]],
                       ("penalty", "max", "rule")))
    parts.append(f"<p class='m'>{e(doc['outliers_note'])}</p></body></html>")
    return "".join(parts).encode("utf-8")
