"""Deterministic sample dataset with the problems the tool detects."""
from __future__ import annotations

import io
import random

import pandas as pd

_CITIES = ["Shanghai", "Beijing", "Shenzhen", "Hangzhou", "Chengdu"]
_NAMES = ["Alice Wang", "Bob Li", "Carol Zhang", "David Chen", "Eva Liu", "Frank Zhao", "Grace Yang"]
_STATUS = ["Paid", "Pending", "Shipped", "Refunded"]
_DATES = [
    lambda d: d.strftime("%Y-%m-%d"),
    lambda d: d.strftime("%m/%d/%Y"),
    lambda d: d.strftime("%d %b %Y"),
    lambda d: d.strftime("%b %d, %Y"),
]


def sample_dataframe() -> pd.DataFrame:
    rng = random.Random(42)
    base = pd.Timestamp("2024-01-03")
    rows = []
    for i in range(1, 51):
        d = base + pd.Timedelta(days=rng.randint(0, 150))
        # dates with day > 12 so day/month order is never ambiguous
        if d.day <= 12:
            d = d + pd.Timedelta(days=13)
        rows.append({
            "order_id": f"{i:05d}",                       # leading zeros must survive
            "customer": rng.choice(_NAMES),
            "order_date": rng.choice(_DATES)(d),          # mixed formats
            "amount": round(rng.uniform(20, 480), 2),
            "city": rng.choice(_CITIES),
            "status": rng.choice(_STATUS),
            "note": None,
        })
    df = pd.DataFrame(rows)
    df.loc[[6, 21], "amount"] = [25000.0, 18999.5]        # suspicious amounts
    df.loc[[3, 17, 29, 40], "amount"] = None              # missing
    df.loc[[5, 25], "city"] = None
    df.loc[[11, 33], "city"] = "   "                      # blank string
    for i in (2, 9, 14, 30):                              # extra spaces
        df.loc[i, "customer"] = f"  {df.loc[i, 'customer']} "
    df.loc[[8, 19], "status"] = ["Paid ", " Shipped"]
    df.loc[[1, 12, 44], "note"] = ["gift", "call first", "=SUM(A1:A5)"]  # formula-like text
    dup = df.loc[[4, 15, 27]].copy()                      # exact duplicates
    near = df.loc[[10]].copy()
    near["customer"] = " " + near["customer"] + "  "      # duplicate only after trimming
    df = pd.concat([df, dup, near], ignore_index=True)
    df.loc[len(df)] = [None] * df.shape[1]                # fully empty row
    return df


def sample_csv_bytes() -> bytes:
    buf = io.StringIO()
    sample_dataframe().to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")
