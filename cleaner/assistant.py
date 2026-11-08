"""Rule-based natural-language assistant (English / 中文).

It only maps keywords to the same structured rules the UI uses. It does not call any
external AI, never sends data anywhere, and its output is a *proposal* that the user
must apply explicitly.
"""
from __future__ import annotations

import re

from .rules import make_rule, normalize_rules

_PATTERNS = [
    ("dedup", r"duplicat|dedup|重复"),
    ("trim", r"whitespace|white space|trim|extra spaces|strip|空格|空白字符"),
    ("standardize_dates", r"\bdates?\b|日期|时间格式"),
    ("blank_to_na", r"blank (string|cell|value)s?|empty (string|cell)s?|空字符串"),
    ("drop_empty", r"(empty|blank) (rows?|columns?)|空行|空列"),
    ("normalize_columns", r"column names?|headers?|列名|表头"),
    ("infer_types", r"numbers?\b|numeric|数字|数值"),
    ("fill_missing", r"missing|null|\bnan\b|empty values?|blanks?\b|缺失|空值"),
]
_STRATEGY = [
    ("mean", r"\bmean\b|average|均值|平均"),
    ("median", r"median|中位"),
    ("mode", r"\bmode\b|most (frequent|common)|众数|最常见"),
    ("drop_rows", r"drop|remove|delete|discard|删除|去掉|丢弃"),
]


def parse_instruction(text: str) -> dict:
    """Return {"rules": [...], "notes": [...]} - rules are normalized rule dicts."""
    text = (text or "")[:1000]
    low = text.lower()
    found: list[dict] = []
    notes: list[str] = []

    for rid, pattern in _PATTERNS:
        if not re.search(pattern, low):
            continue
        if rid == "fill_missing":
            strategy = next((s for s, p in _STRATEGY if re.search(p, low)), None)
            # "remove empty rows" is the drop_empty rule, not a missing-value strategy
            only_empty_rows = re.search(r"(empty|blank) (rows?|columns?)|空行|空列", low) and not re.search(
                r"missing|null|nan|缺失|空值", low)
            if only_empty_rows:
                continue
            if strategy is None:
                notes.append("missing_no_strategy")
                found.append(make_rule("fill_missing", False))
            else:
                found.append(make_rule("fill_missing", True, strategy=strategy))
        else:
            found.append(make_rule(rid, True))
    if not found:
        notes.append("nothing_understood")
    return {"rules": normalize_rules(found), "notes": notes}
