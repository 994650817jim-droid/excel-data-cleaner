# Audit of Excel Data Cleaner v1.x (before 2.0)

Audited commit: `c055968` on `main` (single file `app.py`, ~150 lines, no tests, no config).

## 1. Implemented features

- Upload `.csv` / `.xlsx`, sheet picker for multi-sheet workbooks
- GBK/UTF-8 CSV fallback (strict decode, then GB18030)
- Sidebar options: trim, blank→NA, dedup, drop empty rows/cols, normalize column names, 4 missing-value strategies
- Before/after row & missing counts, 3 tabs (cleaned / original / column info)
- Download `.xlsx` and `.csv`

## 2. Problems and bugs (verified, not guessed)

Probes were run against pandas 3.1.1 / openpyxl 3.1.5.

| # | Issue | Evidence |
|---|-------|----------|
| B1 | **Leading zeros lost** in CSV (`00123` → `123`, `0912` → `912`) | `pd.read_csv` default dtype inference |
| B2 | **Long numbers**: values >15 digits stay `object` only by accident; any >15-digit value in a numeric-looking column is at risk of float rounding | probe |
| B3 | **Excel formula injection**: a text cell `=1+1` is written as a real formula (`data_type == 'f'`) by openpyxl | probe |
| B4 | **CSV formula injection**: `=1+1` is written verbatim and executes when opened in Excel | probe |
| B5 | `st.cache_data` keyed on the uploaded bytes keeps user files in **server memory shared across sessions** (not cross-readable, but retained until eviction) | code review |
| B6 | No dates handling at all; mixed date formats pass through untouched | code review |
| B7 | No row cap: a 200 MB upload can exhaust the ~1 GB Streamlit Cloud limit | code review |
| B8 | Every widget change recomputes everything; nothing is confirmed by the user before it is applied | code review |
| B9 | No operation log, no cell-level diff, no way to restore the original | code review |
| B10 | No tests, no pinned dependency lower bounds, no Streamlit config | repo listing |
| B11 | Empty / corrupt / renamed files give raw exception text | code review |
| B12 | `README` and UI promised "never stored" — cannot be fully verified on a hosted platform (see Privacy) | — |

## 3. Code worth keeping

- Strict-decode-then-fallback CSV approach (fixed to decode explicitly)
- The cleaning concepts (trim / blank→NA / dedup / drop-empty / fill strategies) — re-implemented as pure functions
- `st.download_button` usage and the xlsx/csv export shape

## 4. Added in 2.0 (this branch)

See `README.md`. Priorities used:

- **P0**: data-quality analysis + public score, rule suggestions with user confirmation, before/after with cell highlighting and operation log, restore/re-run, charts, sample dataset, JSON/HTML/CSV reports, safe loading (leading zeros, long numbers), formula-injection protection, pytest suite
- **P1**: cleaning templates (JSON import/export), batch cleaning with ZIP, English/中文, rule-based natural-language assistant (no external AI)
- **P2 (not done)**: LLM-backed assistant (needs user-supplied key and explicit consent), per-column rule scoping UI beyond dates/fill, undo history stack

## 5. Privacy facts (what is actually true)

- The app code writes **no files** and keeps **no logs of data**. Uploaded bytes live in the Streamlit session (`st.session_state` / `UploadedFile`, in process memory) and are dropped when the session ends.
- Streamlit's server may hold uploaded files in memory until the session closes; the hosting platform (Streamlit Community Cloud) runs the process and can see platform-level logs and traffic. Do not upload confidential data to a public instance you do not control.
- v2.0 removes `st.cache_data` for user data (B5).
