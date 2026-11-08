"""Excel Data Cleaner 2.0 - Streamlit UI.

All data processing lives in the `cleaner` package (pure functions, unit tested).
This file only handles layout, session state and downloads.
User data is kept in `st.session_state` (per browser session) - no module-level state,
no st.cache_* on user data, no files written.
"""
import pandas as pd
import streamlit as st

from cleaner import __version__
from cleaner.assistant import parse_instruction
from cleaner.batch import MAX_FILES, run_batch
from cleaner.fileio import (FileReadError, export_csv, export_xlsx, list_sheets,
                            neutralize_formulas, read_table)
from cleaner.i18n import t as _t
from cleaner.quality import SCORE_RULES, analyze, unresolved
from cleaner.report import build_report, to_html, to_json, to_log_csv
from cleaner.rules import (DATE_FORMATS, RULE_ORDER, STRATEGIES, apply_rules, cfg_to_rules, compare,
                           normalize_name, rules_to_cfg, suggest_rules)
from cleaner.sample import sample_csv_bytes
from cleaner.templates import TemplateError, export_template, parse_template

st.set_page_config(page_title="Excel Data Cleaner", page_icon="🧹", layout="wide")

SS = st.session_state
STEP_KEYS = ["step_upload", "step_analyze", "step_rules", "step_preview", "step_download"]
HIGHLIGHT = "background-color: rgba(250, 204, 21, 0.5)"
MAX_PREVIEW_ROWS = 200
LAZY_XLSX_ROWS = 20_000  # above this, building .xlsx waits for a button press (slow + memory heavy)


def T(key: str, **kw) -> str:
    return _t(SS.get("lang", "en"), key, **kw)


def tr(prefix: str):
    """format_func bound to the language at render time (not looked up lazily)."""
    lang = SS.get("lang", "en")
    return lambda v: _t(lang, prefix + str(v))


def go(step: int):
    SS["step_pos"] = step
    SS["nav"] = step


# ------------------------------------------------------------------- data flow


def load_dataset(raw: bytes, name: str, sheet: str | None = None) -> bool:
    try:
        lr = read_table(raw, name, sheet)
    except FileReadError as e:
        st.error(T("err_" + e.code, **{k: v for k, v in e.data.items()}))
        return False
    report = analyze(lr.df)
    SS["data"] = {"raw": raw, "name": name, "lr": lr, "df": lr.df, "report": report}
    SS["cfg"] = rules_to_cfg(suggest_rules(report))
    SS["result"] = None
    SS["export_cache"] = {}
    for k in [k for k in SS if k.startswith("w_")]:
        del SS[k]  # forget widget values of the previous dataset
    return True


def clear_data():
    for k in ("data", "cfg", "result", "export_cache"):
        SS.pop(k, None)
    go(0)


def current_rules() -> list[dict]:
    return cfg_to_rules(SS["cfg"])


# --------------------------------------------------------------------- helpers


def warn_text(w: dict) -> str:
    kw = {k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in w.items() if k != "code"}
    return T("w_" + w["code"], **kw)


def rule_label(rule_id: str) -> str:
    return T("rule_" + rule_id)


def kind_label(kind: str) -> str:
    return T("kind_" + kind)


def styled(df: pd.DataFrame, mask: pd.DataFrame):
    def paint(col):
        m = mask[col.name].reindex(col.index, fill_value=False) if col.name in mask.columns \
            else pd.Series(False, index=col.index)
        return [HIGHLIGHT if x else "" for x in m]
    return df.style.apply(paint, axis=0)


def need_data() -> bool:
    if "data" in SS:
        return True
    st.info(T("need_data"))
    st.button(T("go_upload"), on_click=go, args=(0,))
    return False


def nav_buttons(back: int | None, nxt: int | None, next_disabled: bool = False):
    c1, c2, _ = st.columns([1, 1, 6])
    if back is not None:
        c1.button(T("back"), on_click=go, args=(back,), key=f"back{back}")
    if nxt is not None:
        c2.button(T("next"), on_click=go, args=(nxt,), type="primary", disabled=next_disabled, key=f"next{nxt}")


# ------------------------------------------------------------------ step 1


def step_upload():
    st.subheader(T("s1_title"))
    st.caption(T("s1_caption"))
    left, right = st.columns([3, 2])
    with left:
        up = st.file_uploader(T("upload_label"), type=["csv", "xlsx"], key="uploader",
                              help=T("upload_help"))
    with right:
        with st.container(border=True):
            st.markdown(f"**{T('sample_title')}**")
            st.caption(T("sample_desc"))
            c1, c2 = st.columns(2)
            if c1.button(T("use_sample"), type="primary"):
                if load_dataset(sample_csv_bytes(), "sample_orders.csv"):
                    st.rerun()
            c2.download_button(T("download_sample"), sample_csv_bytes(), "sample_orders.csv", "text/csv",
                               on_click="ignore")

    # Load only when the uploader holds a file we have not loaded yet, so choosing the
    # sample dataset (or removing data) is not undone by a file still sitting in the uploader.
    if up is not None and SS.get("last_upload_id") != up.file_id:
        if load_dataset(up.getvalue(), up.name):
            SS["last_upload_id"] = up.file_id
            st.rerun()

    data = SS.get("data")
    if not data:
        st.info(T("upload_hint"))
        return
    lr = data["lr"]
    st.success(T("loaded", name=data["name"], rows=len(data["df"]), cols=data["df"].shape[1]))
    info = [T("kind_" + lr.kind + "_file")]
    if lr.encoding:
        info.append(T("encoding", enc=lr.encoding))
    if lr.converted_columns:
        info.append(T("converted", cols=", ".join(lr.converted_columns)))
    st.caption(" · ".join(info))
    if lr.kind == "xlsx" and len(lr.sheets) > 1:
        chosen = st.selectbox(T("select_sheet"), lr.sheets, index=lr.sheets.index(lr.sheet), key="sheet_sel")
        if chosen != lr.sheet and load_dataset(data["raw"], data["name"], chosen):
            st.rerun()
    st.dataframe(data["df"].head(100), width="stretch")
    st.caption(T("preview_note", n=min(100, len(data["df"]))))
    c1, c2 = st.columns([1, 6])
    c1.button(T("next"), on_click=go, args=(1,), type="primary", key="next1")
    c2.button(T("remove_data"), on_click=clear_data)


# ------------------------------------------------------------------ step 2


def step_analyze():
    st.subheader(T("s2_title"))
    st.caption(T("s2_caption"))
    if not need_data():
        return
    rep, df = SS["data"]["report"], SS["data"]["df"]
    s = rep["stats"]
    c = st.columns(5)
    c[0].metric(T("m_rows"), f"{s['rows']:,}")
    c[1].metric(T("m_cols"), s["cols"])
    c[2].metric(T("m_score"), f"{rep['score']['value']}/100", help=T("score_help"))
    c[3].metric(T("m_missing"), f"{s['missing_pct']}%", help=T("missing_help"))
    c[4].metric(T("m_dups"), s["duplicates"])

    with st.expander(T("score_how")):
        st.write(T("score_intro"))
        rows = [{T("c_penalty"): T("pen_" + k), T("c_points"): rep["score"]["penalties"][k]["points"],
                 T("c_max"): cap, T("c_rule"): T("score_" + k)} for k, cap, _ in SCORE_RULES]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.markdown(f"**{T('issues_title')}**")
    if not rep["issues"]:
        st.success(T("no_issues"))
    for issue in rep["issues"]:
        text = T("issue_" + issue["code"], count=issue["count"], columns=", ".join(issue["columns"]))
        {"warning": st.warning, "info": st.info}.get(issue["severity"], st.info)(text)

    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown(f"**{T('chart_missing')}**")
        miss = pd.DataFrame({T("c_missing_pct"): [c["missing_pct"] for c in rep["columns"]]},
                            index=[c["name"] for c in rep["columns"]])
        st.bar_chart(miss)
    with g2:
        st.markdown(f"**{T('chart_types')}**")
        st.bar_chart(pd.DataFrame({T("c_columns"): list(rep["kind_counts"].values())},
                                  index=[kind_label(k) for k in rep["kind_counts"]]))
    with g3:
        st.markdown(f"**{T('chart_dups')}**")
        st.bar_chart(pd.DataFrame({T("m_rows"): [s["rows"] - s["duplicates"], s["duplicates"]]},
                                  index=[T("unique_rows"), T("duplicate_rows")]))

    st.markdown(f"**{T('profile_title')}**")
    prof = pd.DataFrame([{
        T("c_column"): c["name"], T("c_kind"): kind_label(c["kind"]), T("c_dtype"): c["dtype"],
        T("c_missing"): c["missing"] + c["blank"], T("c_missing_pct"): c["missing_pct"],
        T("c_unique"): c["unique"], T("c_spaces"): c["whitespace"],
        T("c_outliers"): c["outliers"]["count"] if c["outliers"] else 0,
        T("c_dates"): ", ".join(c["date_formats"]),
    } for c in rep["columns"]])
    st.dataframe(prof, hide_index=True, width="stretch")

    flagged = [c for c in rep["columns"] if c["outliers"]]
    if flagged:
        with st.expander(T("outliers_title")):
            st.caption(T("outliers_note"))
            for c in flagged:
                o = c["outliers"]
                st.write(T("outlier_line", name=c["name"], count=o["count"], lo=round(o["lower"], 2),
                           hi=round(o["upper"], 2), ex=", ".join(f"{x:g}" for x in o["examples"])))
                st.dataframe(df.loc[o["row_ids"][:50]].rename(index=lambda i: i + 1), width="stretch")
    nav_buttons(0, 2)


# ------------------------------------------------------------------ step 3


def seeded(name: str, value):
    key = "w_" + name
    SS.setdefault(key, value)
    return key


def step_rules():
    st.subheader(T("s3_title"))
    st.caption(T("s3_caption"))
    if not need_data():
        return
    rep, df, cfg = SS["data"]["report"], SS["data"]["df"], SS["cfg"]
    s = rep["stats"]
    cols = list(df.columns)

    assistant_box(rep, cfg)
    template_box(cfg)

    detected = {
        "trim": s["whitespace_cells"], "blank_to_na": s["blank_cells"],
        "infer_types": len(s["numeric_text_columns"]), "drop_empty": s["empty_rows"] + len(s["empty_cols"]),
        "standardize_dates": len(s["date_inconsistent"]), "dedup": s["duplicates"],
        "fill_missing": s["missing_cells"],
        "normalize_columns": sum(1 for c in cols if normalize_name(c) != c),
    }
    st.markdown(f"**{T('rules_title')}**")
    for rid in RULE_ORDER:
        with st.container(border=True):
            top = st.columns([3, 2])
            cfg["on"][rid] = top[0].checkbox(f"**{rule_label(rid)}**", key=seeded(f"on_{rid}", cfg["on"][rid]))
            n = detected[rid]
            top[1].caption(T("detected", n=n) if n else T("not_detected"))
            st.caption(T("desc_" + rid))
            if not cfg["on"][rid]:
                continue
            if rid == "standardize_dates":
                opts = [c for c in cols]
                cfg["dates_cols"] = [c for c in cfg["dates_cols"] if c in opts]
                cfg["dates_cols"] = st.multiselect(
                    T("p_date_cols"), opts, key=seeded("dates_cols", cfg["dates_cols"]))
                p1, p2 = st.columns(2)
                cfg["dates_dayfirst"] = p1.selectbox(
                    T("p_dayfirst"), ["auto", "day", "month"], format_func=tr("dayfirst_"),
                    key=seeded("dates_dayfirst", cfg["dates_dayfirst"]))
                cfg["dates_fmt"] = p2.selectbox(
                    T("p_date_fmt"), list(DATE_FORMATS), key=seeded("dates_fmt", cfg["dates_fmt"]))
                if not cfg["dates_cols"]:
                    st.warning(T("pick_date_cols"))
            elif rid == "dedup":
                cfg["dedup_subset"] = [c for c in cfg["dedup_subset"] if c in cols]
                cfg["dedup_subset"] = st.multiselect(
                    T("p_dedup_cols"), cols, key=seeded("dedup_subset", cfg["dedup_subset"]),
                    help=T("p_dedup_help"))
            elif rid == "fill_missing":
                cfg["fill_strategy"] = st.selectbox(
                    T("p_strategy"), list(STRATEGIES), format_func=tr("st_"),
                    key=seeded("fill_strategy", cfg["fill_strategy"]))
                cfg["fill_cols"] = [c for c in cfg["fill_cols"] if c in cols]
                f1, f2 = st.columns(2)
                cfg["fill_cols"] = f1.multiselect(
                    T("p_fill_cols"), cols, key=seeded("fill_cols", cfg["fill_cols"]))
                if cfg["fill_strategy"] == "constant":
                    cfg["fill_const"] = f2.text_input(T("p_constant"), key=seeded("fill_const", cfg["fill_const"]))
                if cfg["fill_strategy"] == "none":
                    st.info(T("fill_none_hint"))
    nav_buttons(1, 3)


def assistant_box(rep, cfg):
    with st.expander(T("ass_title")):
        st.caption(T("ass_caption"))
        text = st.text_input(T("ass_label"), placeholder=T("ass_placeholder"), key="ass_text", max_chars=300)
        if not text:
            return
        out = parse_instruction(text)
        for note in out["notes"]:
            st.warning(T("ass_" + note))
        if not out["rules"]:
            return
        st.markdown(f"**{T('ass_plan')}**")
        for r in out["rules"]:
            extra = ""
            if r["id"] == "fill_missing":
                extra = f" - {T('st_' + r['params']['strategy'])}"
            st.write(f"- {rule_label(r['id'])}{extra}" + ("" if r["enabled"] else f" ({T('off')})"))
        if st.button(T("ass_apply")):
            apply_plan_to_cfg(out["rules"], rep, cfg)
            st.rerun()


def apply_plan_to_cfg(rules: list[dict], rep: dict, cfg: dict):
    for r in rules:
        cfg["on"][r["id"]] = r["enabled"]
        if r["id"] == "standardize_dates" and not cfg["dates_cols"]:
            cfg["dates_cols"] = list(rep["stats"]["date_columns"])
        if r["id"] == "fill_missing" and r["enabled"]:
            cfg["fill_strategy"] = r["params"]["strategy"]
    for k in [k for k in SS if k.startswith("w_")]:
        del SS[k]  # widgets re-seed from cfg


def template_box(cfg):
    with st.expander(T("tpl_title")):
        st.caption(T("tpl_caption"))
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(T("tpl_name"), value="my-template", max_chars=60, key="tpl_name")
            st.download_button(T("tpl_export"), export_template(cfg_to_rules(cfg), name),
                               f"{(name or 'template').strip().replace(' ', '_')[:40]}.json",
                               "application/json", on_click="ignore")
        with c2:
            up = st.file_uploader(T("tpl_import"), type=["json"], key="tpl_upload")
            if up is not None:
                try:
                    rules, tname = parse_template(up.getvalue())
                except TemplateError as e:
                    st.error(T("tpl_error", detail=str(e)))
                else:
                    st.success(T("tpl_ok", name=tname or up.name))
                    if st.button(T("tpl_load")):
                        SS["cfg"] = rules_to_cfg(rules)
                        for k in [k for k in SS if k.startswith("w_")]:
                            del SS[k]
                        st.rerun()


# ------------------------------------------------------------------ step 4


def run_cleaning():
    data, rules = SS["data"], current_rules()
    res = apply_rules(data["df"], rules)
    SS["result"] = {"rules": rules, "res": res, "summary": compare(data["df"], res),
                    "after": analyze(res.df) if len(res.df) else None}
    SS["export_cache"] = {}
    SS["want_xlsx"] = False


def step_preview():
    st.subheader(T("s4_title"))
    st.caption(T("s4_caption"))
    if not need_data():
        return
    data = SS["data"]
    rules = current_rules()
    active = [r for r in rules if r["enabled"]]
    st.markdown(f"**{T('plan_title')}**")
    if active:
        st.write(" → ".join(rule_label(r["id"]) for r in active))
    else:
        st.info(T("nothing_enabled"))

    result = SS.get("result")
    c1, c2, _ = st.columns([1.2, 1.2, 5])
    c1.button(T("run") if result is None else T("rerun"), type="primary", on_click=run_cleaning,
              disabled=not active)
    c2.button(T("restore"), on_click=lambda: SS.update(result=None, export_cache={}, want_xlsx=False), disabled=result is None,
              help=T("restore_help"))
    if result is None:
        st.info(T("run_hint"))
        nav_buttons(2, None)
        return
    if result["rules"] != rules:
        st.warning(T("stale"))

    res, summ, after = result["res"], result["summary"], result["after"]
    if after is None:
        st.error(T("all_removed"))
        return
    before_score, after_score = data["report"]["score"]["value"], after["score"]["value"]
    m = st.columns(6)
    m[0].metric(T("m_rows_removed"), summ["rows_removed"])
    m[1].metric(T("m_filled"), summ["missing_filled"])
    m[2].metric(T("m_cells_changed"), summ["cells_changed"])
    m[3].metric(T("m_cols_renamed"), summ["cols_renamed"])
    m[4].metric(T("m_cols_removed"), summ["cols_removed"])
    m[5].metric(T("m_score"), f"{after_score}", delta=round(after_score - before_score, 1))

    for w in res.warnings:
        st.warning(warn_text(w))

    tab1, tab2, tab3, tab4 = st.tabs([T("tab_changes"), T("tab_removed"), T("tab_log"), T("tab_cleaned")])
    with tab1:
        changes_view(data["df"], res, summ)
    with tab2:
        removed_view(data["df"], res)
    with tab3:
        log_view(res)
    with tab4:
        st.dataframe(res.df.head(500).rename(index=lambda i: i + 1), width="stretch")
        st.caption(T("preview_note", n=min(500, len(res.df))))
    nav_buttons(2, 4)


def changes_view(orig, res, summ):
    mask = summ["mask"]
    only = st.toggle(T("only_changed"), value=True)
    ids = mask.index[mask.any(axis=1)] if only and len(mask) else mask.index
    if len(ids) == 0:
        st.info(T("no_changes"))
        return
    shown = ids[:MAX_PREVIEW_ROWS]
    st.caption(T("legend", n=len(shown), total=len(ids)))
    inverse = {n: o for o, n in res.col_map.items() if n is not None}
    before = orig.loc[shown]
    mask_before = mask.loc[shown].rename(columns=inverse)
    after = res.df.loc[shown]
    shift = lambda d: d.rename(index=lambda i: i + 1)  # noqa: E731
    l, r = st.columns(2)
    l.markdown(f"**{T('before')}**")
    l.dataframe(styled(shift(before), shift(mask_before)), width="stretch")
    r.markdown(f"**{T('after')}**")
    r.dataframe(styled(shift(after), shift(mask.loc[shown])), width="stretch")


def removed_view(orig, res):
    if not res.removed:
        st.info(T("no_removed"))
        return
    ids = list(res.removed)[:500]
    df = orig.loc[ids].copy()
    df.insert(0, T("removed_by"), [rule_label(res.removed[i]) for i in ids])
    st.dataframe(df.rename(index=lambda i: i + 1), width="stretch")
    st.caption(T("removed_note", n=len(ids), total=len(res.removed)))


def log_view(res):
    rows = [{
        T("c_rule_name"): rule_label(e["rule"]) + (f" ({T('second_pass')})" if e["phase"] == 2 else ""),
        T("c_rows_removed"): e["rows_removed"], T("c_cols_removed"): e["cols_removed"],
        T("c_cells_changed"): e["cells_changed"], T("c_filled"): e["filled"],
        T("c_cols_changed"): e["cols_changed"],
    } for e in res.log]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if not res.warnings:
        st.caption(T("no_warnings"))


# ------------------------------------------------------------------ step 5


def cached_export(kind: str, builder):
    cache = SS.setdefault("export_cache", {})
    if kind not in cache:
        cache[kind] = builder()
    return cache[kind]


def step_download():
    st.subheader(T("s5_title"))
    st.caption(T("s5_caption"))
    if not need_data():
        return
    result = SS.get("result")
    if result is None or result["after"] is None:
        st.info(T("run_first"))
        st.button(T("go_preview"), on_click=go, args=(3,))
        return
    data, res, after = SS["data"], result["res"], result["after"]
    before = data["report"]

    sanitize = st.checkbox(T("sanitize"), value=True, help=T("sanitize_help"), key="sanitize")
    n_safe = cached_export("n_safe", lambda: neutralize_formulas(res.df)[1])
    if sanitize and n_safe:
        st.caption(T("sanitize_count", n=n_safe))
    elif not sanitize and n_safe:
        st.warning(T("sanitize_off", n=n_safe))

    stem = data["name"].rsplit(".", 1)[0][:60]
    doc = cached_export("doc", lambda: build_report(
        filename=data["name"], sheet=data["lr"].sheet, before=before, after=after, rules=result["rules"],
        result=res, summary=result["summary"]))
    d = st.columns(3)
    with d[0]:
        st.markdown(f"**{T('dl_data')}**")
        if len(res.df) <= LAZY_XLSX_ROWS or SS.get("want_xlsx"):
            with st.spinner(T("preparing")):
                xlsx = cached_export(f"xlsx{sanitize}", lambda: export_xlsx(res.df, sanitize))
            st.download_button(T("dl_xlsx"), xlsx, f"cleaned_{stem}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               on_click="ignore", type="primary")
        else:
            st.button(T("prepare_xlsx"), on_click=lambda: SS.update(want_xlsx=True), type="primary")
            st.caption(T("prepare_hint"))
        st.download_button(T("dl_csv"), cached_export(f"csv{sanitize}", lambda: export_csv(res.df, sanitize)),
                           f"cleaned_{stem}.csv", "text/csv", on_click="ignore")
    with d[1]:
        st.markdown(f"**{T('dl_report')}**")
        st.download_button(T("dl_report_html"), cached_export(f"html{SS['lang']}", lambda: to_html(doc, T)),
                           f"report_{stem}.html", "text/html", on_click="ignore")
        st.download_button(T("dl_report_json"), cached_export("json", lambda: to_json(doc)),
                           f"report_{stem}.json", "application/json", on_click="ignore")
        st.download_button(T("dl_log"), cached_export("logcsv", lambda: to_log_csv(doc)),
                           f"log_{stem}.csv", "text/csv", on_click="ignore")
    with d[2]:
        st.markdown(f"**{T('dl_template')}**")
        st.caption(T("dl_template_help"))
        st.download_button(T("tpl_export"), export_template(result["rules"], stem),
                           f"template_{stem}.json", "application/json", on_click="ignore")

    st.divider()
    st.markdown(f"**{T('ba_title')}**")
    a, b, c = st.columns(3)
    a.metric(T("score_before"), before["score"]["value"])
    b.metric(T("score_after"), after["score"]["value"], delta=round(after["score"]["value"] - before["score"]["value"], 1))
    c.metric(T("m_rows"), f"{len(res.df):,}", delta=len(res.df) - len(data["df"]))
    comp = pd.DataFrame({
        T("score_before"): [before["score"]["penalties"][k]["points"] for k, _, _ in SCORE_RULES],
        T("score_after"): [after["score"]["penalties"][k]["points"] for k, _, _ in SCORE_RULES],
    }, index=[T("pen_" + k) for k, _, _ in SCORE_RULES])
    st.caption(T("ba_chart_caption"))
    st.bar_chart(comp, stack=False)

    left = unresolved(after)
    st.markdown(f"**{T('unresolved_title')}**")
    if not left:
        st.success(T("no_unresolved"))
    for issue in left:
        st.info(T("issue_" + issue["code"], count=issue["count"], columns=", ".join(issue["columns"])))
    nav_buttons(3, None)


# ------------------------------------------------------------------- batch


def batch_mode():
    st.title("🧹 " + T("b_title"))
    st.caption(T("b_intro", n=MAX_FILES))
    files = st.file_uploader(T("b_upload"), type=["csv", "xlsx"], accept_multiple_files=True, key="batch_files")
    src = st.radio(T("b_rules_src"), ["current", "template"], horizontal=True,
                   format_func=tr("b_src_"), key="b_src")
    rules = None
    if src == "template":
        up = st.file_uploader(T("tpl_import"), type=["json"], key="b_tpl")
        if up is not None:
            try:
                rules, _ = parse_template(up.getvalue())
            except TemplateError as e:
                st.error(T("tpl_error", detail=str(e)))
    elif "cfg" in SS:
        rules = current_rules()
        st.caption(T("b_current", n=sum(r["enabled"] for r in rules)))
    else:
        st.info(T("b_no_rules"))
    c1, c2 = st.columns(2)
    fmt = c1.radio(T("b_format"), ["xlsx", "csv"], horizontal=True, key="b_fmt")
    sanitize = c2.checkbox(T("sanitize"), value=True, help=T("sanitize_help"), key="b_sanitize")
    if len(files) > MAX_FILES:
        st.error(T("b_too_many", n=MAX_FILES))
        return
    if st.button(T("b_run"), type="primary", disabled=not files or rules is None):
        try:
            zip_bytes, summary = run_batch([(f.name, f.getvalue()) for f in files], rules, fmt, sanitize)
        except ValueError as e:
            st.error(str(e))
            return
        SS["batch"] = (zip_bytes, summary)
    if "batch" in SS:
        zip_bytes, summary = SS["batch"]
        df = pd.DataFrame(summary)
        df["status"] = df["status"].map(lambda v: T("b_status_" + v))
        df.columns = [T("b_col_" + c) for c in df.columns]
        st.dataframe(df, hide_index=True, width="stretch")
        if zip_bytes:
            st.download_button(T("b_download"), zip_bytes, "cleaned_files.zip", "application/zip",
                               on_click="ignore", type="primary")
        else:
            st.error(T("b_none_valid"))


# -------------------------------------------------------------------- layout


def sidebar():
    with st.sidebar:
        st.markdown("### 🧹 Excel Data Cleaner")
        choice = st.radio("Language / 语言", ["English", "中文"], horizontal=True, key="lang_choice")
        SS["lang"] = "zh" if choice == "中文" else "en"
        st.radio(T("mode"), ["single", "batch"], format_func=tr("mode_"), key="mode")
        st.divider()
        st.caption(T("privacy"))
        st.caption(f"v{__version__}")


def main():
    sidebar()
    if SS["mode"] == "batch":
        batch_mode()
        return
    st.title("🧹 Excel Data Cleaner")
    st.caption(T("tagline"))
    SS.setdefault("step_pos", 0)
    if "nav" not in SS:
        SS["nav"] = SS["step_pos"]
    lang = SS["lang"]
    st.radio("steps", list(range(5)), format_func=lambda i: f"{i + 1}. {_t(lang, STEP_KEYS[i])}",
             horizontal=True, key="nav", label_visibility="collapsed")
    SS["step_pos"] = SS["nav"]
    [step_upload, step_analyze, step_rules, step_preview, step_download][SS["step_pos"]]()


main()
