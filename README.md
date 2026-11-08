# Excel Data Cleaner 2.0

A free, open-source tool that **finds problems in Excel / CSV data, suggests fixes, lets you confirm them, shows exactly what changed, and exports the result with a report.** English and 中文 UI.

Live app (production, v1.x until this branch is merged): https://excel-data-cleaner-jim.streamlit.app/

## The 5-step flow

| Step | What happens |
|------|--------------|
| 1. Upload | CSV (UTF-8 / GBK / `;` or tab separated) or XLSX with sheet picker. Or load the built-in **sample dataset**. Nothing is changed. |
| 2. Analyze | Data Quality Report: rows/columns, duplicates, missing values, empty rows/columns, types and type conflicts, mixed date formats, extra spaces, suspicious numbers, ID-like columns, charts and a **quality score with a public formula**. |
| 3. Review rules | Rules matching the detected problems are pre-selected. You can switch them off, change parameters, import a template, or describe what you want in a sentence. **Nothing runs yet.** |
| 4. Preview changes | Press *Run cleaning*. See rows removed, missing values filled, cells changed (highlighted before/after), the removed rows and which rule removed them, and the operation log. *Restore original* and *Run again* are one click. |
| 5. Download | Cleaned `.xlsx` / `.csv`, report (HTML / JSON), operation log (CSV), reusable template (JSON), before/after score. |

There is also a **Batch** mode: one rule set applied to up to 10 files with identical columns, downloaded as a ZIP.

## Cleaning rules

Executed in a fixed order (so results do not depend on click order):
trim whitespace → blank text as missing → numeric text to numbers → remove empty rows/columns → standardize dates → remove duplicates → handle missing values → normalize column names.

* **Nothing is filled or deleted without your confirmation.** Missing-value handling defaults to *do nothing*.
* **Outliers are only flagged** (1.5×IQR). They are never removed or changed.
* Numbers are converted from text only when lossless: values with leading zeros (`00123`, phone numbers) or more than 15 digits stay text.
* Dates: day/month order is decided per value from evidence (a first part >12 means day-first, a second part >12 means month-first). Truly ambiguous dates (`03/04/2024`) follow your choice (default month-first) and are reported as a warning. Unparseable values are left unchanged and reported.
* Row identity is the original row number, so before/after stays correct after rows are removed.
* The pipeline is idempotent: cleaning already-cleaned data with the same rules changes nothing (covered by tests). Deduplication runs a second time after a filling strategy for this reason.

## Quality score (how it is calculated)

`score = 100 − sum of capped penalties` (caps add up to exactly 100; the same table is shown in the app and in every report).

| Penalty | Rule | Cap |
|---------|------|-----|
| Missing / blank cells | 1 point per 1% of cells | 30 |
| Duplicate rows | 1 point per 1% of rows | 20 |
| Extra spaces | 1 point per 1% of cells | 10 |
| Mixed date formats | 5 points per affected column | 15 |
| Mixed types | 5 points per affected column | 10 |
| Suspicious numbers | 1 point per 1% of numeric cells | 10 |
| Empty rows / columns | 1 point per 1% of rows+columns | 5 |

The score is a simple summary of the checks above, not a guarantee that the data is correct.

## Safety and quality details

* **Formula injection**: text starting with `=`, `+`, `-`, `@` is prefixed with `'` on export (default on, can be turned off for trusted data). Verified: without it, openpyxl writes `=1+1` as a real formula cell.
* **Leading zeros / long numbers**: CSV is read as text first; Excel is read without re-parsing text cells (pandas' default would turn the text `00123` into `123`).
* **Encodings**: strict UTF-8 (with/without BOM), then GB18030 (covers GBK). Delimiter is sniffed (`,` `;` tab `|`).
* **Bad files**: empty files, header-only files, damaged `.xlsx`, `.xlsx` renamed to `.csv`, ragged CSVs, binary garbage → friendly translated errors, no stack traces.
* **Templates** are JSON, parsed with a strict whitelist (known rule ids, typed params, size limit). Unknown keys are ignored. **No `eval` / `exec` anywhere**; the sentence assistant maps keywords to the same whitelisted rules.

## Natural-language assistant (rule-based, no AI service)

Type e.g. *"Remove duplicates, standardize dates, and clean whitespace."* or *"删除重复行,去掉空格,缺失值用中位数填充"*. It recognises a fixed set of keywords in English and 中文 and shows a proposed plan; you must press *Apply* and later *Run cleaning*. It runs entirely inside the app: **no model is called, no API key is needed, no data leaves the app.** An LLM-backed version is deliberately not included (it would need a user-supplied key and explicit consent to send data).

## Privacy: what is actually true

* The app code writes **no files** and keeps **no data logs**. Your file lives in your browser session's memory (`st.session_state`) and is dropped when the session ends. v2.0 no longer uses `st.cache_data` for user data (v1.x did, which kept uploaded bytes in server memory across sessions).
* The app runs on **Streamlit Community Cloud** (or wherever you host it). That platform processes your upload and has its own logging and retention that this project cannot control or verify. **Do not upload confidential data you would not give to a hosted tool**; for sensitive data, run the app locally.

## Limits

* 50 MB per file, 200,000 rows, 500 columns (memory: Community Cloud has about 1 GB shared by all sessions).
* Measured on a laptop with 300,000 rows × 8 columns: load ≈ 10 s, analysis ≈ 10 s, cleaning ≈ 5 s, CSV export ≈ 3 s, **XLSX export ≈ 66 s** (openpyxl is slow per cell). So `.xlsx` for results over 20,000 rows is built only after you press *Prepare Excel file*. Cloud servers are usually slower than a laptop.
* Batch mode reads the first sheet of each Excel file.
* Only the first 100 rows are previewed in step 1; before/after shows up to 200 changed rows. Exports always contain all rows.

## Run locally

```bash
pip install -r requirements-dev.txt
streamlit run app.py
pytest
```

## Project layout

```
app.py                 Streamlit UI (layout + session state only)
cleaner/
  fileio.py            read/validate/export (CSV, XLSX), formula neutralizing
  common.py            text masks, lossless number inference, date parsing
  quality.py           analysis, issues, public score
  rules.py             rules, suggestions, engine, before/after comparison
  templates.py         strict JSON templates
  assistant.py         keyword assistant (EN/中文)
  report.py            JSON / HTML / CSV reports
  batch.py             multi-file ZIP
  sample.py            deterministic sample dataset
  i18n.py              English / 中文 strings
tests/                 pytest suite (core + headless UI flow)
docs/AUDIT.md          audit of v1.x
```

## Deploying

Streamlit Community Cloud deploys the branch configured for the app (currently `main`). To release 2.0: merge the feature branch into `main`, then reboot the app. Dependencies are in `requirements.txt` (`streamlit>=1.50`, `pandas>=2.2`, `openpyxl>=3.1`); upload limit and theme are in `.streamlit/config.toml`. Streamlit Community Cloud is free; this project uses no paid service.
