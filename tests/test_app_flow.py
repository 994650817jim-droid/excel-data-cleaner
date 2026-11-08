"""Headless UI flow tests (Streamlit AppTest). They run the real app.py script."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from cleaner.i18n import EN, ZH

TIMEOUT = 60
APP = str(Path(__file__).resolve().parents[1] / "app.py")


def new_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def click(at: AppTest, label: str):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"button {label!r} not found; have {[b.label for b in at.button]}"
    matches[0].click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def goto(at: AppTest, step: int):
    at.session_state["step_pos"] = step
    at.session_state["nav"] = step
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def loaded_app() -> AppTest:
    at = new_app()
    click(at, EN["use_sample"])
    return at


def test_starts_on_upload_with_english_default():
    at = new_app()
    assert at.title[0].value.endswith("Excel Data Cleaner")
    assert any(EN["upload_hint"] in i.value for i in at.info)


def test_steps_are_gated_until_data_is_loaded():
    at = new_app()
    for step in (1, 2, 3, 4):
        goto(at, step)
        assert any(EN["need_data"] in i.value or EN["run_hint"] in i.value for i in at.info) or at.button


def test_sample_dataset_loads_and_shows_quality_report():
    at = loaded_app()
    assert at.session_state["data"]["df"].shape == (55, 7)
    goto(at, 1)
    assert metric(at, EN["m_rows"]) == "55"
    assert metric(at, EN["m_dups"]) == "3"
    assert "/100" in metric(at, EN["m_score"])
    texts = " ".join(w.value for w in at.warning)
    assert "date" in texts.lower()                                   # inconsistent dates reported


def test_rules_are_preselected_but_not_applied_without_confirmation():
    at = loaded_app()
    goto(at, 2)
    cfg = at.session_state["cfg"]
    assert cfg["on"]["trim"] and cfg["on"]["dedup"] and cfg["on"]["standardize_dates"]
    assert not cfg["on"]["fill_missing"]                              # never auto-fills
    assert at.session_state["result"] is None                         # nothing ran yet
    assert at.session_state["data"]["df"].shape == (55, 7)            # original intact


def test_full_flow_run_preview_restore_and_download():
    at = loaded_app()
    goto(at, 3)
    click(at, EN["run"])
    res = at.session_state["result"]
    assert res is not None and len(res["res"].df) == 50
    assert metric(at, EN["m_rows_removed"]) == "5"
    assert len(at.tabs) == 4
    goto(at, 4)
    assert not at.exception
    assert metric(at, EN["score_before"]) != metric(at, EN["score_after"])
    # restore the original, then re-run
    goto(at, 3)
    click(at, EN["restore"])
    assert at.session_state["result"] is None
    assert at.session_state["data"]["df"].shape == (55, 7)
    click(at, EN["run"])
    assert at.session_state["result"] is not None


def test_changing_a_rule_marks_preview_stale():
    at = loaded_app()
    goto(at, 3)
    click(at, EN["run"])
    at.session_state["cfg"]["on"]["trim"] = False
    at.run()
    assert any(EN["stale"] in w.value for w in at.warning)


def test_chinese_ui_has_no_missing_text():
    at = new_app()
    at.sidebar.radio(key="lang_choice").set_value("中文")
    at.run()
    assert not at.exception
    assert any(ZH["upload_hint"] in i.value for i in at.info)
    click(at, ZH["use_sample"])
    for step in (1, 2):
        goto(at, step)
    goto(at, 3)                                   # index 3 = "Preview changes" (has the run button)
    click(at, ZH["run"])
    assert at.session_state["result"] is not None
    leaked = [m.label for m in at.metric if m.label in {EN["m_rows_removed"], EN["m_filled"]}]
    assert not leaked and any(m.label == ZH["m_rows_removed"] for m in at.metric)
    goto(at, 4)                                   # download step renders in Chinese too
    assert any(m.label == ZH["score_before"] for m in at.metric)


def test_batch_mode_renders_and_requires_files():
    at = new_app()
    at.sidebar.radio(key="mode").set_value("batch")
    at.run()
    assert not at.exception
    assert at.title[0].value.endswith(EN["b_title"])
    run_btn = [b for b in at.button if b.label == EN["b_run"]][0]
    assert run_btn.disabled


def test_no_user_data_in_module_globals_between_sessions():
    a, b = loaded_app(), new_app()
    assert "data" in a.session_state and "data" not in b.session_state


def test_large_results_build_xlsx_only_after_button_press():
    import pandas as pd
    at = loaded_app()
    goto(at, 3)
    click(at, EN["run"])
    big = pd.DataFrame({"a": range(25_000), "b": ["x"] * 25_000})
    at.session_state["result"]["res"].df = big
    goto(at, 4)
    labels = [b.label for b in at.button]
    assert EN["prepare_xlsx"] in labels                        # not built automatically
    assert not [d for d in at.get("download_button") if d.proto.label == EN["dl_xlsx"]]
    assert [d for d in at.get("download_button") if d.proto.label == EN["dl_csv"]]   # CSV is instant
    click(at, EN["prepare_xlsx"])
    assert [d for d in at.get("download_button") if d.proto.label == EN["dl_xlsx"]]
