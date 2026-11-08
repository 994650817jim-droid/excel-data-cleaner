"""Cleaning templates: rule sets saved as JSON. Parsing is strict and whitelist-based."""
from __future__ import annotations

import json
import re

from .rules import DATE_FORMATS, DEFAULT_PARAMS, RULE_ORDER, STRATEGIES, make_rule, normalize_rules

TEMPLATE_VERSION = 1
MAX_BYTES = 200_000
MAX_COLUMNS = 500
_SAFE_FMT = re.compile(r"^[%A-Za-z0-9 :/.\-_,]{1,40}$")


class TemplateError(ValueError):
    pass


def export_template(rules: list[dict], name: str = "my-template") -> str:
    doc = {"version": TEMPLATE_VERSION, "name": str(name)[:100],
           "rules": [{"id": r["id"], "enabled": r["enabled"], "params": r["params"]}
                     for r in normalize_rules(rules)]}
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _str_list(v, field, allow_none=False):
    if v is None and allow_none:
        return None
    if not isinstance(v, list) or len(v) > MAX_COLUMNS or not all(isinstance(x, str) and len(x) <= 200 for x in v):
        raise TemplateError(f"invalid {field}")
    return v


def _clean_params(rule_id: str, params) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise TemplateError(f"params of {rule_id} must be an object")
    allowed = DEFAULT_PARAMS[rule_id]
    out = {}
    for k, v in params.items():
        if k not in allowed:
            continue  # unknown keys are ignored, never executed
        if rule_id == "dedup" and k == "subset":
            out[k] = _str_list(v, "subset", allow_none=True)
        elif rule_id == "standardize_dates":
            if k == "columns":
                out[k] = _str_list(v, "columns")
            elif k == "dayfirst":
                if v not in ("auto", True, False):
                    raise TemplateError("invalid dayfirst")
                out[k] = v
            elif k == "fmt":
                if not isinstance(v, str) or not _SAFE_FMT.match(v):
                    raise TemplateError("invalid date format")
                out[k] = v
        elif rule_id == "fill_missing":
            if k == "strategy":
                if v not in STRATEGIES:
                    raise TemplateError("invalid strategy")
                out[k] = v
            elif k == "columns":
                out[k] = _str_list(v, "columns", allow_none=True)
            elif k == "constant":
                if not isinstance(v, str) or len(v) > 200:
                    raise TemplateError("invalid constant")
                out[k] = v
    return out


def parse_template(text: str | bytes) -> tuple[list[dict], str]:
    """Return (rules, name). Raises TemplateError on anything unexpected."""
    if isinstance(text, bytes):
        if len(text) > MAX_BYTES:
            raise TemplateError("template too large")
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise TemplateError("template is not UTF-8") from e
    elif len(text.encode("utf-8")) > MAX_BYTES:
        raise TemplateError("template too large")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise TemplateError("template is not valid JSON") from e
    if not isinstance(doc, dict) or doc.get("version") != TEMPLATE_VERSION:
        raise TemplateError("unsupported template version")
    raw = doc.get("rules")
    if not isinstance(raw, list) or len(raw) > 50:
        raise TemplateError("invalid rules list")
    rules = []
    for item in raw:
        if not isinstance(item, dict) or item.get("id") not in RULE_ORDER:
            raise TemplateError(f"unknown rule: {item.get('id') if isinstance(item, dict) else item!r}")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TemplateError("enabled must be true/false")
        rules.append(make_rule(item["id"], enabled, **_clean_params(item["id"], item.get("params"))))
    name = doc.get("name", "")
    return normalize_rules(rules), name if isinstance(name, str) else ""
