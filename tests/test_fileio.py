import io

import openpyxl
import pandas as pd
import pytest

from cleaner.fileio import (FileReadError, export_csv, export_xlsx, list_sheets,
                            neutralize_formulas, read_table)


def xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
    return buf.getvalue()


# 1. normal CSV
def test_normal_csv():
    lr = read_table(b"a,b\n1,x\n2,y\n", "t.csv")
    assert lr.kind == "csv" and lr.df.shape == (2, 2)
    assert lr.df["a"].tolist() == [1, 2]
    assert lr.df["b"].tolist() == ["x", "y"]


# 2. normal XLSX
def test_normal_xlsx():
    raw = xlsx_bytes({"S": pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})})
    lr = read_table(raw, "t.xlsx")
    assert lr.kind == "xlsx" and lr.sheet == "S" and lr.df.shape == (2, 2)


# 3. GBK Chinese CSV (and UTF-8 / BOM)
@pytest.mark.parametrize("enc", ["gbk", "gb18030", "utf-8", "utf-8-sig"])
def test_chinese_csv_encodings(enc):
    text = "姓名,城市\n张三,北京\n李四,上海\n"
    lr = read_table(text.encode(enc), "t.csv")
    assert list(lr.df.columns) == ["姓名", "城市"]
    assert lr.df["姓名"].tolist() == ["张三", "李四"]


# 10. leading zeros and long numbers survive loading
def test_leading_zeros_and_long_numbers_preserved():
    csv = "id,phone,big,n\n00123,0912345,12345678901234567890,7\n00456,0333111,98765432109876543210,8\n"
    df = read_table(csv.encode(), "t.csv").df
    assert df["id"].tolist() == ["00123", "00456"]
    assert df["phone"].tolist() == ["0912345", "0333111"]
    assert df["big"].tolist() == ["12345678901234567890", "98765432109876543210"]
    assert df["n"].tolist() == [7, 8]  # safe columns still become numbers


def test_decimals_and_missing_numeric_inference():
    df = read_table(b"a,b\n1,1.5\n,2.25\n3,\n", "t.csv").df
    assert str(df["a"].dtype) == "Int64"
    assert df["b"].dtype == "float64"


def test_semicolon_delimiter():
    df = read_table(b"a;b\n1;2\n3;4\n", "t.csv").df
    assert list(df.columns) == ["a", "b"] and df.shape == (2, 2)


# 6. empty file
@pytest.mark.parametrize("raw", [b"", b"   \n\n", b"\xef\xbb\xbf"])
def test_empty_csv(raw):
    with pytest.raises(FileReadError) as e:
        read_table(raw, "t.csv")
    assert e.value.code == "empty_file"


def test_header_only_is_no_data():
    with pytest.raises(FileReadError) as e:
        read_table(b"a,b,c\n", "t.csv")
    assert e.value.code == "no_data"


def test_blank_xlsx_is_no_data():
    raw = xlsx_bytes({"S": pd.DataFrame()})
    with pytest.raises(FileReadError) as e:
        read_table(raw, "t.xlsx")
    assert e.value.code == "no_data"


# 14. broken / disguised files
def test_corrupt_xlsx():
    with pytest.raises(FileReadError) as e:
        read_table(b"this is not a zip", "t.xlsx")
    assert e.value.code == "corrupt_xlsx"


def test_xlsx_renamed_to_csv():
    raw = xlsx_bytes({"S": pd.DataFrame({"a": [1]})})
    with pytest.raises(FileReadError) as e:
        read_table(raw, "t.csv")
    assert e.value.code == "not_csv_text"


def test_unsupported_extension():
    with pytest.raises(FileReadError) as e:
        read_table(b"a,b\n1,2", "t.txt")
    assert e.value.code == "unsupported_type"


def test_binary_garbage_csv_does_not_crash():
    with pytest.raises(FileReadError):
        read_table(bytes(range(256)) * 20, "t.csv")


def test_ragged_csv_reports_malformed():
    with pytest.raises(FileReadError) as e:
        read_table(b"a,b\n1,2\n3,4,5,6,7\n", "t.csv")
    assert e.value.code == "malformed_csv"


# 8. multiple sheets
def test_multi_sheet_selection():
    raw = xlsx_bytes({"A": pd.DataFrame({"x": [1, 2]}), "B": pd.DataFrame({"y": [3, 4, 5]})})
    assert list_sheets(raw) == ["A", "B"]
    assert read_table(raw, "t.xlsx", "B").df.shape == (3, 1)
    assert read_table(raw, "t.xlsx", "A").df.shape == (2, 1)
    assert read_table(raw, "t.xlsx", "nope").sheet == "A"  # unknown sheet falls back safely


# 12. export consistency
def test_export_roundtrip_csv_and_xlsx():
    df = pd.DataFrame({"id": ["00123", "00456"], "name": ["张三", "Li, \"Bo\""],
                       "v": [1.5, 2.0], "n": pd.array([1, None], dtype="Int64")})
    for data, fname in ((export_csv(df), "x.csv"), (export_xlsx(df), "x.xlsx")):
        back = read_table(data, fname).df
        assert back["id"].tolist() == ["00123", "00456"]
        assert back["name"].tolist() == ["张三", 'Li, "Bo"']
        assert back["v"].tolist() == [1.5, 2.0]
        assert back["n"].isna().tolist() == [False, True]


def test_formula_injection_is_neutralized():
    df = pd.DataFrame({"a": ["=1+1", "@cmd", "+5", "-x", "ok"], "=evil": [1, 2, 3, 4, 5]})
    safe, n = neutralize_formulas(df)
    assert n == 5  # four cells and one header
    assert safe["a"].tolist() == ["'=1+1", "'@cmd", "'+5", "'-x", "ok"]
    wb = openpyxl.load_workbook(io.BytesIO(export_xlsx(df)))
    types = {c.data_type for c in wb.active["A"][1:]}
    assert "f" not in types  # no real formula cells
    assert b"'=1+1" in export_csv(df)


def test_sanitize_off_keeps_values_but_is_opt_in():
    df = pd.DataFrame({"a": ["=1+1"]})
    wb = openpyxl.load_workbook(io.BytesIO(export_xlsx(df, sanitize=False)))
    assert wb.active["A2"].data_type == "f"  # documents why sanitising is the default


def test_negative_numbers_are_not_touched():
    df = pd.DataFrame({"a": [-5, 3.5]})
    assert neutralize_formulas(df)[1] == 0
