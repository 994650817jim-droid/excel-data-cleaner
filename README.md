# Excel Data Cleaner

A Streamlit app for cleaning Excel and CSV files.

Live app: https://excel-data-cleaner-jim.streamlit.app/

## Features

- Upload `.xlsx` or `.csv` (GBK and UTF-8 CSVs are both detected automatically)
- Pick a sheet for multi-sheet workbooks
- Trim spaces, treat blank strings as missing, remove duplicates, drop empty rows/columns
- Handle missing values: keep, drop rows, or fill with mean / median / most frequent value
- Before/after metrics, column info, and downloads as `.xlsx` or `.csv`

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```
