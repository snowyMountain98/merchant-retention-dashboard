"""Build company-level monthly analysis from the source workbook, without merchant-level output.

The workbook is read only. The model operates on enterprise-month aggregates, not
individual merchant records. The output contains enterprise-level summary/history
only and OpenAI-generated company-level text.
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

COMPANY_ID = "기업관리번호"
COMPANY_NAME = "기업명"
MONTH_COL = "집계년월"
DATE_COL = "집계년월일"
PAYMENT_METHOD_COL = "결제수단"
FINANCIAL_COMPANY_COL = "금융회사"
NUMERIC_COLUMNS = {
    "approval_count": "승인건수",
    "approval_amount": "승인금액",
    "cancel_count": "취소건수",
    "cancel_amount": "취소금액",
    "partial_cancel_count": "부분취소건수",
    "partial_cancel_amount": "부분취소금액",
}
REQUIRED_COLUMNS = {COMPANY_ID, COMPANY_NAME, MONTH_COL, DATE_COL, PAYMENT_METHOD_COL, FINANCIAL_COMPANY_COL, "승인건수", "승인금액"}


def _digits(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\D", "", str(value))


def _id(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return re.sub(r"\.0+$", "", text) if re.fullmatch(r"\d+\.0+", text) else text


def _month(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).strftime("%Y-%m")
    raw = str(value).strip()
    digits = _digits(raw)
    if len(digits) >= 6 and 1900 <= int(digits[:4]) <= 2200:
        return f"{digits[:4]}-{digits[4:6]}"
    parsed = pd.to_datetime(raw, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m")


def _date(value: Any) -> pd.Timestamp | pd.NaT:
    if value is None or pd.isna(value):
        return pd.NaT
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.to_datetime(value, errors="coerce")
    raw = str(value).strip()
    digits = _digits(raw)
    if len(digits) >= 8 and digits[:4].isdigit() and 1900 <= int(digits[:4]) <= 2200:
        return pd.to_datetime(digits[:8], format="%Y%m%d", errors="coerce")
    if re.fullmatch(r"\d+(?:\.0+)?", raw):
        serial = float(raw)
        if 30000 <= serial <= 80000:
            return pd.to_datetime(int(serial), unit="D", origin="1899-12-30", errors="coerce")
    return pd.to_datetime(raw, errors="coerce")


def _number_series(series: pd.Series, label: str) -> tuple[pd.Series, int]:
    raw = series.astype("string").str.replace(",", "", regex=False).str.strip()
    blanks = raw.isna() | raw.eq("")
    numeric = pd.to_numeric(raw.mask(blanks), errors="coerce")
    invalid = (~blanks) & numeric.isna()
    if invalid.any():
        examples = raw[invalid].drop_duplicates().head(3).tolist()
        raise ValueError(f"{label}에 숫자가 아닌 값이 있습니다. 예시: {examples}")
    return numeric.fillna(0), int(blanks.sum())


def _prev_month(month: str, offset: int = 1) -> str:
    return (pd.Period(month, freq="M") - offset).strftime("%Y-%m")


def _percent(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    # When both months have no payments, activity is unchanged (0% change).
    # If the prior month was zero but this month is positive, a percentage change
    # is mathematically undefined; keep None rather than fabricate an infinite rate.
    if previous == 0:
        return 0.0 if current == 0 else None
    return round((current - previous) / previous * 100, 1)


def _json_num(value: Any) -> int | float:
    number = float(value or 0)
    return int(number) if number.is_integer() else round(number, 2)


def _current_month_metrics(
    enterprise_id: str,
    month: str,
    month_map: dict[tuple[str, str], dict[str, float]],
) -> dict[str, Any]:
    current = month_map.get((enterprise_id, month), {})
    previous = month_map.get((enterprise_id, _prev_month(month)))
    current_sales = float(current.get("approval_amount", 0) or 0)
    current_count = float(current.get("approval_count", 0) or 0)
    previous_sales = float(previous.get("approval_amount", 0) or 0) if previous is not None else None
    previous_count = float(previous.get("approval_count", 0) or 0) if previous is not None else None
    return {
        "sales_change_rate": _percent(current_sales, previous_sales),
        "transaction_change_rate": _percent(current_count, previous_count),
        "current_approval_amount": current_sales,
        "current_approval_count": current_count,
        "previous_approval_amount": previous_sales,
        "previous_approval_count": previous_count,
        "has_previous_month": previous is not None,
    }


STATUS_LABELS = {
    "TRADING": "거래 유지 중",
    "DECLINING": "거래 감소 관찰",
    "RECENTLY_INACTIVE": "최근 미거래",
    "LONG_INACTIVE": "장기 미거래",
    "RESTARTED": "거래 재개",
    "FIRST_ACTIVITY": "최근 관측기간 첫 거래",
    "SHORT_HISTORY": "거래 이력 짧음",
    "NO_ACTIVITY": "관측기간 내 거래 없음",
}

STATUS_ORDER = {
    "LONG_INACTIVE": 0,
    "RECENTLY_INACTIVE": 1,
    "DECLINING": 2,
    "NO_ACTIVITY": 3,
    "RESTARTED": 4,
    "FIRST_ACTIVITY": 5,
    "SHORT_HISTORY": 6,
    "TRADING": 7,
}


def _has_activity(row: dict[str, float] | None) -> bool:
    if not row:
        return False
    return float(row.get("approval_amount", 0) or 0) > 0 or float(row.get("approval_count", 0) or 0) > 0


def _transaction_status(
    enterprise_id: str,
    month_map: dict[tuple[str, str], dict[str, float]],
    all_months: list[str],
    latest_month: str,
) -> dict[str, Any]:
    """Classify observed transaction activity with explicit, non-probabilistic rules.

    No model score or uncalibrated churn probability is created. The 30% decline
    threshold is a transparent operational alert, not a validated churn threshold.
    """
    active = [
        _has_activity(month_map.get((enterprise_id, month)))
        for month in all_months
    ]
    if not active:
        return {
            "transaction_status": "NO_ACTIVITY",
            "transaction_status_label": STATUS_LABELS["NO_ACTIVITY"],
            "status_reasons": ["분석기간에 해당하는 월별 데이터가 없습니다."],
            "last_active_month": None,
            "zero_activity_streak": 0,
            "active_month_count_3": 0,
            "active_month_count_6": 0,
            "active_month_count_12": 0,
            "sales_below_3m_pct": None,
            "transactions_below_3m_pct": None,
            "baseline_month_count": 0,
        }

    latest_index = len(all_months) - 1
    is_active_now = active[-1]
    active_indices = [i for i, value in enumerate(active) if value]
    last_active_index = active_indices[-1] if active_indices else None
    first_active_index = active_indices[0] if active_indices else None
    last_active_month = all_months[last_active_index] if last_active_index is not None else None

    zero_streak = 0
    for value in reversed(active):
        if value:
            break
        zero_streak += 1

    prior_zero_streak = 0
    for value in reversed(active[:-1]):
        if value:
            break
        prior_zero_streak += 1

    recent_active_counts = {
        str(window): sum(active[max(0, len(active) - window):])
        for window in (3, 6, 12)
    }
    months_since_first_active = (
        latest_index - first_active_index + 1
        if first_active_index is not None else 0
    )

    sales_below = None
    count_below = None
    baseline_month_count = 0
    if first_active_index is not None:
        # Compare against the three full calendar months preceding the current
        # month, but do not treat pre-first-activity months as a sales baseline.
        prior_indices = list(range(max(first_active_index, latest_index - 3), latest_index))
        baseline_month_count = len(prior_indices)
        if baseline_month_count == 3:
            prior_rows = [month_map.get((enterprise_id, all_months[i]), {}) for i in prior_indices]
            prior_sales = [float(row.get("approval_amount", 0) or 0) for row in prior_rows]
            prior_counts = [float(row.get("approval_count", 0) or 0) for row in prior_rows]
            sales_mean = float(np.mean(prior_sales))
            count_mean = float(np.mean(prior_counts))
            current = month_map.get((enterprise_id, latest_month), {})
            current_sales = float(current.get("approval_amount", 0) or 0)
            current_count = float(current.get("approval_count", 0) or 0)
            if sales_mean > 0:
                sales_below = round(max(0.0, (sales_mean - current_sales) / sales_mean * 100), 1)
            if count_mean > 0:
                count_below = round(max(0.0, (count_mean - current_count) / count_mean * 100), 1)

    reasons: list[str] = []
    if not is_active_now:
        if last_active_index is None:
            status = "NO_ACTIVITY"
            reasons.append("분석기간 전체에서 승인금액과 승인건수가 모두 0입니다.")
        elif zero_streak >= 3:
            status = "LONG_INACTIVE"
            reasons.append(f"최근 {zero_streak}개월 연속으로 승인금액과 승인건수가 모두 0입니다.")
            reasons.append(f"마지막 승인 실적 월: {last_active_month}")
        else:
            status = "RECENTLY_INACTIVE"
            reasons.append(f"최근 {zero_streak}개월 동안 승인 실적이 없습니다.")
            reasons.append(f"마지막 승인 실적 월: {last_active_month}")
    elif len(active_indices) == 1 and active_indices[0] == latest_index:
        status = "FIRST_ACTIVITY"
        current = month_map.get((enterprise_id, latest_month), {})
        reasons.append(f"관측기간 중 이전 승인 실적 없이 {latest_month}에 첫 실적이 발생했습니다.")
        reasons.append(f"최신월 승인금액 {_json_num(current.get('approval_amount', 0)):,}원 / 승인건수 {_json_num(current.get('approval_count', 0)):,}건")
    elif prior_zero_streak > 0 and any(active[:-1]):
        status = "RESTARTED"
        current = month_map.get((enterprise_id, latest_month), {})
        reasons.append(f"이전 승인 실적 이후 {prior_zero_streak}개월 미거래 뒤 {latest_month}에 실적이 다시 발생했습니다.")
        reasons.append(f"최신월 승인금액 {_json_num(current.get('approval_amount', 0)):,}원 / 승인건수 {_json_num(current.get('approval_count', 0)):,}건")
    elif months_since_first_active < 4:
        status = "SHORT_HISTORY"
        reasons.append(f"첫 승인 실적 이후 {months_since_first_active}개월이 관측되어 3개월 평균 추세 비교에 한계가 있습니다.")
    elif (sales_below is not None and sales_below >= 30) or (count_below is not None and count_below >= 30):
        status = "DECLINING"
        if sales_below is not None and sales_below >= 30:
            reasons.append(f"최신월 승인금액이 직전 3개월 평균보다 {sales_below:.1f}% 낮습니다(운영 기준 30% 이상 감소).")
        if count_below is not None and count_below >= 30:
            reasons.append(f"최신월 승인건수가 직전 3개월 평균보다 {count_below:.1f}% 낮습니다(운영 기준 30% 이상 감소).")
    else:
        status = "TRADING"
        current = month_map.get((enterprise_id, latest_month), {})
        reasons.append(f"최신월 {latest_month}에 승인 실적이 발생했습니다.")
        if baseline_month_count < 3:
            reasons.append("직전 3개월 기준 실적이 부족해 매출 감소 여부를 충분히 비교할 수 없습니다.")
        else:
            reasons.append("최신월 승인금액·승인건수는 직전 3개월 평균 대비 30% 이상 감소 기준에 해당하지 않습니다.")

    return {
        "transaction_status": status,
        "transaction_status_label": STATUS_LABELS[status],
        "status_reasons": reasons,
        "last_active_month": last_active_month,
        "zero_activity_streak": zero_streak,
        "active_month_count_3": recent_active_counts["3"],
        "active_month_count_6": recent_active_counts["6"],
        "active_month_count_12": recent_active_counts["12"],
        "sales_below_3m_pct": sales_below,
        "transactions_below_3m_pct": count_below,
        "baseline_month_count": baseline_month_count,
        "months_since_first_active": months_since_first_active,
    }


def _dimension_analysis(
    aggregates: pd.DataFrame,
    enterprise_id: str,
    dimension_column: str,
    output_label: str,
    history_months: list[str],
) -> list[dict[str, Any]]:
    """Create enterprise-only month series for each payment dimension.

    A dimension-month absent from transaction rows is filled with zero because the
    source is a transaction activity extract and absence means no payment activity.
    """
    subset = aggregates.loc[aggregates["enterprise_id"].astype(str).eq(str(enterprise_id))]
    output: list[dict[str, Any]] = []
    if subset.empty:
        return output
    for dimension_value, group in subset.groupby(dimension_column, dropna=False):
        by_month = {str(row["base_month"]): row for _, row in group.iterrows()}
        monthly_sales: list[dict[str, Any]] = []
        for month in history_months:
            row = by_month.get(month)
            item: dict[str, Any] = {"month": month}
            for metric in NUMERIC_COLUMNS:
                item[metric] = _json_num(row[metric]) if row is not None else 0
            monthly_sales.append(item)
        output.append({output_label: str(dimension_value), "monthly_sales": monthly_sales})
    output.sort(
        key=lambda item: (
            -float(item["monthly_sales"][-1]["approval_amount"] if item["monthly_sales"] else 0),
            str(item[output_label]),
        )
    )
    return output


def build_enterprise_dashboard(input_path: str | Path) -> dict[str, Any]:
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"원본 엑셀을 찾을 수 없습니다: {source}")
    df = pd.read_excel(source, sheet_name=0, dtype=object, engine="openpyxl")
    df.columns = [str(col).strip() for col in df.columns]
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(missing)}")
    if df.empty:
        raise ValueError("엑셀 데이터가 비어 있습니다.")

    df["enterprise_id"] = df[COMPANY_ID].map(_id)
    df["enterprise_name"] = df[COMPANY_NAME].astype("string").fillna("").str.strip()
    if df["enterprise_id"].eq("").any() or df["enterprise_name"].eq("").any():
        raise ValueError("기업관리번호 또는 기업명이 비어 있는 행이 있습니다.")
    if (df.groupby("enterprise_id")["enterprise_name"].nunique() > 1).any():
        raise ValueError("하나의 기업관리번호에 여러 기업명이 연결됩니다. 원본 매핑을 확인하세요.")
    df["base_month"] = df[MONTH_COL].map(_month)
    df["base_date"] = df[DATE_COL].map(_date)
    if df["base_month"].isna().any() or df["base_date"].isna().any():
        raise ValueError("집계년월 또는 집계년월일을 표준 날짜로 해석하지 못한 행이 있습니다.")

    blank_counts: dict[str, int] = {}
    for target, source_col in NUMERIC_COLUMNS.items():
        if source_col not in df.columns:
            if target in ("approval_count", "approval_amount"):
                raise ValueError(f"필수 컬럼 누락: {source_col}")
            df[target] = 0
            blank_counts[source_col] = len(df)
        else:
            df[target], blank_counts[source_col] = _number_series(df[source_col], source_col)

    # Only aggregated dimensions are emitted to the website: no merchant-level rows.
    # Blank dimension labels are made explicit so amounts are not silently omitted.
    df["payment_method"] = (
        df[PAYMENT_METHOD_COL].astype("string").fillna("").str.strip().replace("", "미분류")
    )
    df["financial_company"] = (
        df[FINANCIAL_COMPANY_COL].astype("string").fillna("").str.strip().replace("", "미분류 금융회사")
    )
    payment_method_aggregates = df.groupby(
        ["enterprise_id", "payment_method", "base_month"], as_index=False
    )[list(NUMERIC_COLUMNS)].sum()
    financial_company_aggregates = df.groupby(
        ["enterprise_id", "financial_company", "base_month"], as_index=False
    )[list(NUMERIC_COLUMNS)].sum()

    observed_monthly = df.groupby(
        ["enterprise_id", "enterprise_name", "base_month"], as_index=False
    )[list(NUMERIC_COLUMNS)].sum()
    first_month = str(observed_monthly["base_month"].min())
    latest_month = str(observed_monthly["base_month"].max())

    # The source is a transaction activity extract, not a completeness-limited
    # snapshot: an absent enterprise-month row means no approvals took place.
    # Build a dense enterprise x calendar-month grid and explicitly zero-fill it
    # before calculating comparisons, transaction states, and history.
    all_months = pd.period_range(
        start=pd.Period(first_month, freq="M"),
        end=pd.Period(latest_month, freq="M"),
        freq="M",
    ).astype(str).tolist()
    companies = df[["enterprise_id", "enterprise_name"]].drop_duplicates()
    month_frame = pd.DataFrame({"base_month": all_months})
    companies = companies.assign(_grid_key=1)
    month_frame = month_frame.assign(_grid_key=1)
    grid = companies.merge(month_frame, on="_grid_key", how="inner").drop(columns="_grid_key")
    monthly = grid.merge(
        observed_monthly,
        on=["enterprise_id", "enterprise_name", "base_month"],
        how="left",
        validate="one_to_one",
    )
    monthly[list(NUMERIC_COLUMNS)] = monthly[list(NUMERIC_COLUMNS)].fillna(0)
    monthly = monthly.sort_values(["enterprise_id", "base_month"]).reset_index(drop=True)

    month_map: dict[tuple[str, str], dict[str, float]] = {}
    for _, row in monthly.iterrows():
        month_map[(str(row["enterprise_id"]), str(row["base_month"]))] = {metric: float(row[metric]) for metric in NUMERIC_COLUMNS}

    # Statuses are derived from observed monthly approval activity, using simple
    # auditable rules instead of a statistical anomaly score or churn probability.
    current_records: list[dict[str, Any]] = []
    for enterprise_id, enterprise_name in monthly[["enterprise_id", "enterprise_name"]].drop_duplicates().itertuples(index=False, name=None):
        enterprise_id, enterprise_name = str(enterprise_id), str(enterprise_name)
        current = month_map.get((enterprise_id, latest_month), {})
        info = _current_month_metrics(enterprise_id, latest_month, month_map)
        status_info = _transaction_status(enterprise_id, month_map, all_months, latest_month)
        info.update(status_info)
        current_records.append({
            "enterprise_id": enterprise_id,
            "enterprise_name": enterprise_name,
            "info": info,
        })

    names = monthly.drop_duplicates("enterprise_id").set_index("enterprise_id")["enterprise_name"].astype(str).to_dict()
    enterprise_rows: list[dict[str, Any]] = []
    for enterprise_id in sorted(names):
        enterprise_id = str(enterprise_id)
        history = monthly.loc[monthly["enterprise_id"].astype(str).eq(enterprise_id)].sort_values("base_month").tail(12)
        record = next(r for r in current_records if r["enterprise_id"] == enterprise_id)
        info = record.get("info") or {}
        status = info.get("transaction_status", "NO_ACTIVITY")
        reasons = info.get("status_reasons", [])

        history_json = [
            {"month": f"{str(row['base_month'])[:4]}-{str(row['base_month'])[5:7]}",
             "approval_amount": _json_num(row["approval_amount"]),
             "approval_count": _json_num(row["approval_count"]),
             "cancel_amount": _json_num(row["cancel_amount"]),
             "cancel_count": _json_num(row["cancel_count"]),
             "partial_cancel_amount": _json_num(row["partial_cancel_amount"]),
             "partial_cancel_count": _json_num(row["partial_cancel_count"])}
            for _, row in history.iterrows()
        ]
        history_months = [str(value) for value in history["base_month"].tolist()]
        payment_method_analysis = _dimension_analysis(
            payment_method_aggregates, enterprise_id, "payment_method", "payment_method", history_months
        )
        financial_company_analysis = _dimension_analysis(
            financial_company_aggregates, enterprise_id, "financial_company", "financial_company", history_months
        )
        enterprise_rows.append({
            "enterprise_id": enterprise_id,
            "enterprise_name": names[enterprise_id],
            "display_name": names[enterprise_id],
            "transaction_status": status,
            "transaction_status_label": info.get("transaction_status_label", STATUS_LABELS[status]),
            "sales_change_rate": info.get("sales_change_rate"),
            "transaction_change_rate": info.get("transaction_change_rate"),
            "sales_below_3m_pct": info.get("sales_below_3m_pct"),
            "transactions_below_3m_pct": info.get("transactions_below_3m_pct"),
            "baseline_month_count": info.get("baseline_month_count", 0),
            "last_active_month": info.get("last_active_month"),
            "zero_activity_streak": info.get("zero_activity_streak", 0),
            "active_month_count_3": info.get("active_month_count_3", 0),
            "active_month_count_6": info.get("active_month_count_6", 0),
            "active_month_count_12": info.get("active_month_count_12", 0),
            "months_since_first_active": info.get("months_since_first_active", 0),
            "current_approval_amount": _json_num(info.get("current_approval_amount", 0)),
            "current_approval_count": _json_num(info.get("current_approval_count", 0)),
            "previous_approval_amount": _json_num(info.get("previous_approval_amount", 0) or 0),
            "previous_approval_count": _json_num(info.get("previous_approval_count", 0) or 0),
            "status_reasons": reasons,
            "monthly_sales": history_json,
            "payment_method_analysis": payment_method_analysis,
            "financial_company_analysis": financial_company_analysis,
        })

    enterprise_rows.sort(key=lambda e: (STATUS_ORDER.get(e["transaction_status"], 99), e["enterprise_name"], e["enterprise_id"]))

    monthly_totals = monthly.groupby("base_month", as_index=False)[list(NUMERIC_COLUMNS)].sum().sort_values("base_month").tail(12)
    status_counts = {
        status: sum(e["transaction_status"] == status for e in enterprise_rows)
        for status in STATUS_LABELS
    }
    current_trading_count = sum(
        _has_activity(month_map.get((str(e["enterprise_id"]), latest_month)))
        for e in enterprise_rows
    )
    summary = {
        "enterprise_count": len(enterprise_rows),
        "currently_trading_enterprise_count": current_trading_count,
        "declining_enterprise_count": status_counts["DECLINING"],
        "recently_inactive_enterprise_count": status_counts["RECENTLY_INACTIVE"],
        "long_inactive_enterprise_count": status_counts["LONG_INACTIVE"],
        "restarted_enterprise_count": status_counts["RESTARTED"],
        "first_activity_enterprise_count": status_counts["FIRST_ACTIVITY"],
        "short_history_enterprise_count": status_counts["SHORT_HISTORY"],
        "no_activity_enterprise_count": status_counts["NO_ACTIVITY"],
        "transaction_status_counts": status_counts,
    }
    latest_date = pd.Timestamp(df.loc[df["base_month"].eq(latest_month), "base_date"].max()).normalize()
    return {
        "analysis_month": latest_month,
        "as_of_date": latest_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": source.name,
        "source_row_count": int(len(df)),
        "source_date_from": pd.Timestamp(df["base_date"].min()).strftime("%Y-%m-%d"),
        "source_date_to": latest_date.strftime("%Y-%m-%d"),
        "method": "enterprise_month_transaction_status_v1_zero_fill",
        "dimension_aggregation_policy": "Monthly enterprise aggregates are additionally grouped by payment method and financial company; absent enterprise-dimension-month rows are zero-filled.",
        "zero_fill_policy": "An absent enterprise-month record is interpreted as 0 approval amount and 0 approval count (no payments), not missing data.",
        "status_policy": {
            "TRADING": "최신월 승인금액 또는 승인건수가 양수이고, 직전 3개월 평균 대비 30% 이상 감소 기준에 해당하지 않음",
            "DECLINING": "최신월 승인금액 또는 승인건수가 해당 지표의 직전 3개월 평균보다 30% 이상 낮음(3개월 비교 기준이 있는 경우)",
            "RECENTLY_INACTIVE": "최신월 거래가 없고, 과거 거래가 있으며 연속 미거래가 1~2개월",
            "LONG_INACTIVE": "최신월 거래가 없고 과거 거래가 있으며 연속 미거래가 3개월 이상",
            "RESTARTED": "과거 거래가 있고 1개월 이상 미거래 후 최신월 거래가 재발생",
            "FIRST_ACTIVITY": "분석기간에 최신월 이전 승인 실적이 없고 최신월에 첫 승인 실적 발생",
            "SHORT_HISTORY": "최초 거래 이후 4개월 미만이어서 직전 3개월 비교 이력이 부족",
            "NO_ACTIVITY": "분석기간 전체에서 승인금액과 승인건수가 모두 0",
        },
        "notice": "위험·주의·낮은 위험 점수 등급과 Isolation Forest 점수를 제거했습니다. 표시되는 거래 상태는 기업별 월간 승인금액·승인건수의 관측 사실과 명시된 규칙으로 산출한 현재/과거 거래활동 상태이며, 재무제표 기반 기업 건전성이나 미래 이탈 확률을 뜻하지 않습니다. 거래가 없던 월은 승인금액·승인건수를 0으로 처리했습니다. 직전 3개월 평균보다 30% 이상 낮은 경우 거래 감소 관찰로 표시합니다. 개별 가맹점 내역은 출력하지 않습니다.",
        "numeric_blank_counts": blank_counts,
        "summary": summary,
        "enterprises": enterprise_rows,
        "monthly_sales": [
            {"month": f"{str(row['base_month'])[:4]}-{str(row['base_month'])[5:7]}",
             "approval_amount": _json_num(row["approval_amount"]),
             "approval_count": _json_num(row["approval_count"])}
            for _, row in monthly_totals.iterrows()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build enterprise-only monthly retention analysis")
    parser.add_argument("--input", default="스타플랫폼거래집계.xlsx")
    parser.add_argument("--output", default="public-data/dashboard_summary.json")
    args = parser.parse_args()
    result = build_enterprise_dashboard(args.input)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    summary = result["summary"]
    print(f"기업 단위 분석 완료 | 원본행={result['source_row_count']:,} | 기업={summary['enterprise_count']:,} | 현재 거래={summary['currently_trading_enterprise_count']:,} | 장기 미거래={summary['long_inactive_enterprise_count']:,} | 거래 감소 관찰={summary['declining_enterprise_count']:,}")


if __name__ == "__main__":
    main()
