"""Aggregate settlement rows by merchant and month, keeping monetary measures separate."""
from __future__ import annotations
import pandas as pd
MERCHANT = "스타플랫폼가맹점번호"
MONTH = "집계년월"


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0, index=df.index, dtype="float64")
    values = df[column].astype("string").str.replace(",", "", regex=False)
    return pd.to_numeric(values, errors="coerce").fillna(0)


def aggregate_monthly(df: pd.DataFrame) -> pd.DataFrame:
    required = {MERCHANT, MONTH, "승인건수", "승인금액"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(missing)}")
    data = df.copy()
    data[MERCHANT] = data[MERCHANT].astype("string").str.strip()
    data["base_month"] = data[MONTH].astype("string").str.replace(r"[^0-9]", "", regex=True).str[:6]
    mapping = {
        "approval_count":"승인건수", "approval_amount":"승인금액",
        "cancel_count":"취소건수", "cancel_amount":"취소금액",
        "partial_cancel_count":"부분취소건수", "partial_cancel_amount":"부분취소금액"
    }
    for target, source in mapping.items():
        data[target] = _numeric(data, source)
    result = data.groupby([MERCHANT, "base_month"], dropna=False, as_index=False).agg(
        approval_count=("approval_count", "sum"), approval_amount=("approval_amount", "sum"),
        cancel_count=("cancel_count", "sum"), cancel_amount=("cancel_amount", "sum"),
        partial_cancel_count=("partial_cancel_count", "sum"), partial_cancel_amount=("partial_cancel_amount", "sum")
    )
    return result.sort_values([MERCHANT, "base_month"]).reset_index(drop=True)
