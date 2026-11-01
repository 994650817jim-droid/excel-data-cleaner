
import io
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Excel Data Cleaner")
st.title("Excel Data Cleaner")
st.write("Upload an Excel or CSV file to clean your data.")

file = st.file_uploader(
    "Choose a file",
    type=["csv", "xlsx"]
)

if file is not None:
    try:
        if file.name.lower().endswith(".csv"):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        st.subheader("Original Data")
        st.dataframe(df.head(20))

        original_rows = len(df)
        cleaned = df.copy()

        for col in cleaned.select_dtypes(include="object"):
            cleaned[col] = cleaned[col].map(
                lambda x: x.strip() if isinstance(x, str) else x
            )

        cleaned = cleaned.drop_duplicates()

        st.subheader("Cleaning Summary")
        st.write("Original rows:", original_rows)
        st.write("Cleaned rows:", len(cleaned))
        st.write("Duplicates removed:", original_rows - len(cleaned))
        st.write("Missing values:", int(cleaned.isna().sum().sum()))

        st.subheader("Cleaned Data")
        st.dataframe(cleaned.head(20))

        output = cleaned.to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            "Download Cleaned CSV",
            output,
            file_name="cleaned_data.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"Unable to process file: {e}")

