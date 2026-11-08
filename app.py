import io

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Excel Data Cleaner", page_icon="🧹", layout="wide")

ENCODINGS = ["utf-8-sig", "gb18030"]

NA_KEEP = "Keep as is"
NA_DROP = "Drop rows with missing values"
NA_MEAN = "Fill numeric columns with mean"
NA_MEDIAN = "Fill numeric columns with median"
NA_MODE = "Fill all columns with most frequent value"


@st.cache_data(show_spinner=False)
def list_sheets(raw: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(raw)).sheet_names


@st.cache_data(show_spinner=False)
def load_data(raw: bytes, name: str, sheet: str | None) -> pd.DataFrame:
    if name.lower().endswith(".csv"):
        for enc in ENCODINGS:  # Chinese CSVs are often GBK
            try:
                text = raw.decode(enc)  # strict decode so we can fall back
            except UnicodeDecodeError:
                continue
            return pd.read_csv(io.StringIO(text))
        raise ValueError("Unrecognized CSV encoding. Please save it as UTF-8 or GBK.")
    return pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0)


def clean(df: pd.DataFrame, opts: dict) -> pd.DataFrame:
    out = df.copy()
    if opts["norm_cols"]:
        out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    if opts["strip"]:
        for col in out.columns:
            if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
                out[col] = out[col].map(lambda x: x.strip() if isinstance(x, str) else x)
    if opts["blank_na"]:
        out = out.replace(r"^\s*$", pd.NA, regex=True)
    if opts["dedup"]:
        out = out.drop_duplicates()
    if opts["drop_empty"]:
        out = out.dropna(axis=0, how="all").dropna(axis=1, how="all")

    mode = opts["na_mode"]
    if mode == NA_DROP:
        out = out.dropna()
    elif mode != NA_KEEP:
        for col in out.columns:
            if not out[col].isna().any():
                continue
            if pd.api.types.is_numeric_dtype(out[col]):
                if mode == NA_MEAN:
                    out[col] = out[col].fillna(out[col].mean())
                elif mode == NA_MEDIAN:
                    out[col] = out[col].fillna(out[col].median())
            if mode == NA_MODE:
                m = out[col].mode()
                if not m.empty:
                    out[col] = out[col].fillna(m.iloc[0])
    return out


def to_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    return buf.getvalue()


st.title("🧹 Excel Data Cleaner")
st.write(
    "Upload an Excel or CSV file, pick your cleaning options, and download the cleaned data. "
    "Files are processed in memory only and are never stored."
)

with st.sidebar:
    st.header("Cleaning options")
    opts = {
        "norm_cols": st.checkbox("Normalize column names (trim, lowercase, underscores)", value=False),
        "strip": st.checkbox("Trim leading/trailing spaces in text", value=True),
        "blank_na": st.checkbox("Treat empty strings as missing values", value=True),
        "dedup": st.checkbox("Remove duplicate rows", value=True),
        "drop_empty": st.checkbox("Remove fully empty rows and columns", value=True),
        "na_mode": st.selectbox(
            "Missing values",
            [NA_KEEP, NA_DROP, NA_MEAN, NA_MEDIAN, NA_MODE],
        ),
    }

file = st.file_uploader("Choose a file", type=["csv", "xlsx"])

if file is None:
    st.info("Upload a CSV or XLSX file to get started.")
    st.stop()

try:
    raw = file.getvalue()
    sheet = None
    if file.name.lower().endswith(".xlsx"):
        sheets = list_sheets(raw)
        if len(sheets) > 1:
            sheet = st.selectbox("Select a sheet", sheets)
        else:
            sheet = sheets[0]
    df = load_data(raw, file.name, sheet)
except Exception as e:
    st.error(f"Unable to read the file: {e}")
    st.stop()

if df.empty:
    st.warning("The file contains no data.")
    st.stop()

cleaned = clean(df, opts)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Original rows", len(df))
c2.metric("Cleaned rows", len(cleaned), delta=len(cleaned) - len(df))
c3.metric("Original missing values", int(df.isna().sum().sum()))
c4.metric("Cleaned missing values", int(cleaned.isna().sum().sum()))

tab1, tab2, tab3 = st.tabs(["Cleaned data", "Original data", "Column info"])
with tab1:
    st.dataframe(cleaned.head(100), use_container_width=True)
with tab2:
    st.dataframe(df.head(100), use_container_width=True)
with tab3:
    info = pd.DataFrame(
        {
            "Type": cleaned.dtypes.astype(str),
            "Missing": cleaned.isna().sum(),
            "Missing %": (cleaned.isna().mean() * 100).round(1).astype(str) + "%",
            "Unique values": cleaned.nunique(),
        }
    )
    st.dataframe(info, use_container_width=True)

st.subheader("Download")
d1, d2 = st.columns(2)
d1.download_button(
    "Download Excel (.xlsx)",
    to_xlsx(cleaned),
    file_name="cleaned_data.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
d2.download_button(
    "Download CSV (.csv)",
    cleaned.to_csv(index=False).encode("utf-8-sig"),
    file_name="cleaned_data.csv",
    mime="text/csv",
)
