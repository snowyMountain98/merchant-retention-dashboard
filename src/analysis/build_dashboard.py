"""Build a dashboard JSON using unsupervised ML anomaly detection.

The uploaded workbook is read-only input and is never modified. The model uses
merchant-month history to rank unusual deterioration patterns. Scores are NOT
calibrated churn probabilities; validated churn labels would be needed for that.
"""
from __future__ import annotations

import argparse
import calendar
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler
from sklearn.pipeline import Pipeline

COMPANY_ID = "기업관리번호"
COMPANY_NAME = "기업명"
MERCHANT_ID = "스타플랫폼가맹점번호"
MERCHANT_NAME = "가맹점명"
MONTH_COL = "집계년월"
DATE_COL = "집계년월일"
NUMERIC_COLUMNS = {
    "approval_count": "승인건수",
    "approval_amount": "승인금액",
    "cancel_count": "취소건수",
    "cancel_amount": "취소금액",
    "partial_cancel_count": "부분취소건수",
    "partial_cancel_amount": "부분취소금액",
}
REQUIRED_COLUMNS = {COMPANY_ID, COMPANY_NAME, MERCHANT_ID, MERCHANT_NAME, MONTH_COL, DATE_COL, "승인건수", "승인금액"}
FEATURES = [
    "sales_drop_pct",
    "transaction_drop_pct",
    "sales_below_3m_pct",
    "transactions_below_3m_pct",
    "inactive_days",
    "cancel_ratio",
    "partial_cancel_ratio",
    "consecutive_declines",
]


def digits(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\D", "", str(value))


def month_key(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).strftime("%Y%m")
    text = str(value).strip()
    found = digits(text)
    return found[:6] if len(found) >= 6 else None


def date_value(value: Any) -> pd.Timestamp | pd.NaT:
    if value is None or pd.isna(value):
        return pd.NaT
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.to_datetime(value, errors="coerce")
    text = str(value).strip()
    if not text:
        return pd.NaT
    found = digits(text)
    if len(found) >= 8 and 1900 <= int(found[:4]) <= 2200:
        return pd.to_datetime(found[:8], format="%Y%m%d", errors="coerce")
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        serial = float(text)
        if 30000 <= serial <= 80000:
            return pd.to_datetime(int(serial), unit="D", origin="1899-12-30", errors="coerce")
    return pd.to_datetime(text, errors="coerce")


def merchant_key(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return re.sub(r"\.0+$", "", text) if re.fullmatch(r"\d+\.0+", text) else text


def numeric_values(series: pd.Series, label: str) -> tuple[pd.Series, int]:
    raw = series.astype("string").str.replace(",", "", regex=False).str.strip()
    blank = raw.isna() | raw.eq("")
    values = pd.to_numeric(raw.mask(blank), errors="coerce")
    invalid = (~blank) & values.isna()
    if invalid.any():
        examples = raw[invalid].drop_duplicates().head(3).tolist()
        raise ValueError(f"{label}에 숫자가 아닌 값이 있습니다. 예시: {examples}")
    return values.fillna(0), int(blank.sum())


def previous_month(month: str, offset: int = 1) -> str:
    return (pd.Period(month, freq="M") - offset).strftime("%Y%m")


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def json_number(value: Any) -> int | float:
    number = float(value or 0)
    return int(number) if number.is_integer() else round(number, 2)


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return min(max(numerator, 0.0), 5.0)
    return float(np.clip(numerator / denominator, 0.0, 5.0))


def _feature_row(
    merchant_id: str,
    month: str,
    month_map: dict[tuple[str, str], dict[str, float]],
    active_months: set[tuple[str, str]],
    inactivity_by_month: dict[tuple[str, str], int | None],
    current_override: dict[str, float] | None = None,
    previous_override: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    current = current_override if current_override is not None else month_map.get((merchant_id, month), {})
    previous = previous_override if previous_override is not None else month_map.get((merchant_id, previous_month(month)), {})
    current = current or {}
    previous = previous or {}
    cur_sales = float(current.get("approval_amount", 0))
    cur_count = float(current.get("approval_count", 0))
    prev_sales = float(previous.get("approval_amount", 0))
    prev_count = float(previous.get("approval_count", 0))
    sales_change = percent_change(cur_sales, prev_sales)
    count_change = percent_change(cur_count, prev_count)

    prior_months = [previous_month(month, i) for i in (1, 2, 3)]
    prior_data = [month_map.get((merchant_id, m), {}) for m in prior_months]
    prior_sales = [float(x.get("approval_amount", 0)) for x in prior_data]
    prior_counts = [float(x.get("approval_count", 0)) for x in prior_data]
    sales_baseline = float(np.mean(prior_sales)) if any(prior_sales) else 0.0
    count_baseline = float(np.mean(prior_counts)) if any(prior_counts) else 0.0
    sales_below_3m = max(0.0, (sales_baseline - cur_sales) / sales_baseline * 100) if sales_baseline > 0 else 0.0
    counts_below_3m = max(0.0, (count_baseline - cur_count) / count_baseline * 100) if count_baseline > 0 else 0.0

    # A three-month downward pattern is represented as a count of declines across
    # current month and the previous three monthly amounts (0..3).
    series = [float(month_map.get((merchant_id, m), {}).get("approval_amount", 0)) for m in reversed(prior_months)] + [cur_sales]
    consecutive_declines = sum(1 for left, right in zip(series, series[1:]) if left > right)
    inactive = inactivity_by_month.get((merchant_id, month))
    inactive_days = float(inactive or 0)
    cancel_ratio = _ratio(float(current.get("cancel_amount", 0)), cur_sales)
    partial_ratio = _ratio(float(current.get("partial_cancel_amount", 0)), cur_sales)

    features = {
        "sales_drop_pct": float(np.clip(-sales_change, 0, 300)) if sales_change is not None else 0.0,
        "transaction_drop_pct": float(np.clip(-count_change, 0, 300)) if count_change is not None else 0.0,
        "sales_below_3m_pct": float(np.clip(sales_below_3m, 0, 300)),
        "transactions_below_3m_pct": float(np.clip(counts_below_3m, 0, 300)),
        "inactive_days": float(np.clip(inactive_days, 0, 365)),
        "cancel_ratio": cancel_ratio,
        "partial_cancel_ratio": partial_ratio,
        "consecutive_declines": float(consecutive_declines),
    }
    info = {
        "sales_change_rate": sales_change,
        "transaction_change_rate": count_change,
        "inactive_days": int(inactive) if inactive is not None else None,
        "sales_below_3m_pct": round(sales_below_3m, 1),
        "transactions_below_3m_pct": round(counts_below_3m, 1),
        "cancel_ratio": round(cancel_ratio, 3),
        "partial_cancel_ratio": round(partial_ratio, 3),
        "consecutive_declines": int(consecutive_declines),
        "has_previous_month": (merchant_id, previous_month(month)) in active_months,
        "has_three_month_baseline": sales_baseline > 0 or count_baseline > 0,
        "current_approval_amount": cur_sales,
        "current_approval_count": cur_count,
    }
    return features, info


def _explain(info: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if info.get("sales_change_rate") is not None and info["sales_change_rate"] <= -30:
        reasons.append("승인금액이 전월 비교기간 대비 30% 이상 감소")
    if info.get("transaction_change_rate") is not None and info["transaction_change_rate"] <= -30:
        reasons.append("승인건수가 전월 비교기간 대비 30% 이상 감소")
    if info.get("sales_below_3m_pct", 0) >= 30:
        reasons.append("승인금액이 직전 3개월 평균보다 30% 이상 낮음")
    if info.get("transactions_below_3m_pct", 0) >= 30:
        reasons.append("승인건수가 직전 3개월 평균보다 30% 이상 낮음")
    if info.get("inactive_days") is not None and info["inactive_days"] >= 14:
        reasons.append("마지막 관측 거래 이후 14일 이상 경과")
    if info.get("cancel_ratio", 0) >= 0.2:
        reasons.append("취소금액/승인금액 비율이 20% 이상")
    if info.get("partial_cancel_ratio", 0) >= 0.2:
        reasons.append("부분취소금액/승인금액 비율이 20% 이상")
    if info.get("consecutive_declines", 0) >= 2:
        reasons.append("최근 월별 승인금액이 연속 하락")
    if not reasons:
        reasons.append("모델 점수는 과거 가맹점 월별 패턴 대비 계산됨; 특정 위험 신호 기준은 충족하지 않음")
    return reasons


def score_isolation_forest(
    training_features: pd.DataFrame,
    current_features: pd.DataFrame,
) -> tuple[list[float], dict[str, Any]]:
    """Return historical-reference anomaly percentiles from an unsupervised model."""
    if current_features.empty:
        return [], {"algorithm": "IsolationForest", "status": "NO_SCORABLE_MERCHANTS", "training_rows": int(len(training_features))}

    train = training_features[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0)
    current = current_features[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0)

    if len(train) >= 30 and len(train.drop_duplicates()) >= 2:
        fit_data = train
        fit_source = "historical_merchant_months"
    elif len(current) >= 30 and len(current.drop_duplicates()) >= 2:
        # Honest fallback: cross-sectional pattern detection when history is short.
        fit_data = current
        fit_source = "current_cross_section_short_history"
    else:
        return [50.0] * len(current), {
            "algorithm": "IsolationForest",
            "status": "INSUFFICIENT_DATA",
            "fit_source": "none",
            "training_rows": int(len(train)),
            "scored_merchants": int(len(current)),
        }

    model = Pipeline([
        ("scale", RobustScaler()),
        ("model", IsolationForest(n_estimators=300, contamination="auto", random_state=42, n_jobs=-1)),
    ])
    model.fit(fit_data)
    reference_scores = -model.decision_function(fit_data)
    current_scores = -model.decision_function(current)
    # Score against the learned reference distribution, not a churn probability.
    percentiles = [round(float(np.mean(reference_scores <= score) * 100), 1) for score in current_scores]
    return percentiles, {
        "algorithm": "IsolationForest",
        "status": "OK",
        "fit_source": fit_source,
        "training_rows": int(len(train)),
        "scored_merchants": int(len(current)),
        "feature_names": FEATURES,
        "score_definition": "Percentile of anomaly score against the fitted reference distribution; not a churn probability.",
        "risk_thresholds": {"HIGH": 98, "MEDIUM": 90, "LOW": 0},
        "random_state": 42,
    }


def build_dashboard(input_path: str | Path) -> dict[str, Any]:
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"원본 엑셀을 찾을 수 없습니다: {source}")

    # Read-only input. Never save changes to this workbook.
    df = pd.read_excel(source, sheet_name=0, dtype=object, engine="openpyxl")
    df.columns = [str(column).strip() for column in df.columns]
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(missing)}")
    if df.empty:
        raise ValueError("엑셀 데이터가 비어 있습니다.")

    df["enterprise_id"] = df[COMPANY_ID].map(merchant_key)
    df["enterprise_name"] = df[COMPANY_NAME].astype("string").fillna("").str.strip()
    if df["enterprise_id"].eq("").any():
        raise ValueError(f"기업관리번호가 비어 있는 행이 {int(df['enterprise_id'].eq('').sum())}개 있습니다.")
    if df["enterprise_name"].eq("").any():
        raise ValueError(f"기업명이 비어 있는 행이 {int(df['enterprise_name'].eq('').sum())}개 있습니다.")

    # A merchant must map to one enterprise, and an enterprise ID to one name.
    df["merchant_id"] = df[MERCHANT_ID].map(merchant_key)
    if df["merchant_id"].eq("").any():
        raise ValueError(f"가맹점 번호가 비어 있는 행이 {int(df['merchant_id'].eq('').sum())}개 있습니다.")
    merchant_company_counts = df.groupby("merchant_id")["enterprise_id"].nunique()
    if (merchant_company_counts > 1).any():
        raise ValueError("하나의 가맹점 번호가 여러 기업관리번호에 연결됩니다. 원본 매핑을 확인하세요.")
    company_name_counts = df.groupby("enterprise_id")["enterprise_name"].nunique()
    if (company_name_counts > 1).any():
        raise ValueError("하나의 기업관리번호에 여러 기업명이 연결됩니다. 원본 매핑을 확인하세요.")
    df["base_month"] = df[MONTH_COL].map(month_key)
    df["base_date"] = df[DATE_COL].map(date_value)
    if df["base_month"].isna().any():
        raise ValueError("집계년월을 YYYYMM으로 해석할 수 없는 행이 있습니다.")
    if df["base_date"].isna().any():
        raise ValueError("집계년월일을 날짜로 해석할 수 없는 행이 있습니다.")

    blank_counts: dict[str, int] = {}
    for target, source_col in NUMERIC_COLUMNS.items():
        if source_col not in df.columns:
            if target in ("approval_count", "approval_amount"):
                raise ValueError(f"필수 컬럼 누락: {source_col}")
            df[target] = 0
            blank_counts[source_col] = len(df)
        else:
            df[target], blank_counts[source_col] = numeric_values(df[source_col], source_col)

    if MERCHANT_NAME in df.columns:
        df[MERCHANT_NAME] = df[MERCHANT_NAME].astype("string").fillna("").str.strip()
    else:
        df[MERCHANT_NAME] = ""

    latest_month = str(df["base_month"].max())
    cutoff_date = pd.Timestamp(df.loc[df["base_month"].eq(latest_month), "base_date"].max()).normalize()
    previous = previous_month(latest_month)
    previous_day = min(cutoff_date.day, calendar.monthrange(int(previous[:4]), int(previous[4:]))[1])

    monthly = df.groupby(["merchant_id", "base_month"], as_index=False)[list(NUMERIC_COLUMNS)].sum()
    month_map = {
        (str(row["merchant_id"]), str(row["base_month"])): {name: float(row[name]) for name in NUMERIC_COLUMNS}
        for _, row in monthly.iterrows()
    }
    active_months = set(month_map.keys())
    all_ids = sorted(df["merchant_id"].astype(str).unique().tolist())
    all_months = sorted(df["base_month"].astype(str).unique().tolist())

    names: dict[str, str] = {}
    enterprise_names: dict[str, str] = (
        df[["enterprise_id", "enterprise_name"]]
        .drop_duplicates("enterprise_id")
        .set_index("enterprise_id")["enterprise_name"]
        .astype(str)
        .to_dict()
    )
    enterprise_by_merchant: dict[str, str] = (
        df[["merchant_id", "enterprise_id"]]
        .drop_duplicates("merchant_id")
        .set_index("merchant_id")["enterprise_id"]
        .astype(str)
        .to_dict()
    )
    named = df.loc[df[MERCHANT_NAME].notna() & df[MERCHANT_NAME].ne("")].sort_values("base_date")
    if not named.empty:
        names = named.drop_duplicates("merchant_id", keep="last").set_index("merchant_id")[MERCHANT_NAME].astype(str).to_dict()

    merchant_history_by_id: dict[str, list[dict[str, Any]]] = {}
    for merchant_id in all_ids:
        history = monthly.loc[monthly["merchant_id"].astype(str).eq(str(merchant_id))].sort_values("base_month").tail(12)
        merchant_history_by_id[str(merchant_id)] = [
            {
                "month": f"{str(row['base_month'])[:4]}-{str(row['base_month'])[4:]}",
                "approval_amount": json_number(row["approval_amount"]),
                "approval_count": json_number(row["approval_count"]),
                "cancel_amount": json_number(row["cancel_amount"]),
                "cancel_count": json_number(row["cancel_count"]),
                "partial_cancel_amount": json_number(row["partial_cancel_amount"]),
                "partial_cancel_count": json_number(row["partial_cancel_count"]),
            }
            for _, row in history.iterrows()
        ]

    activity = pd.Series(False, index=df.index)
    for metric in NUMERIC_COLUMNS:
        activity |= df[metric].ne(0)
    event_rows = df.loc[activity, ["merchant_id", "base_date"]]
    last_event_by_month: dict[tuple[str, str], pd.Timestamp] = {}
    month_anchor = df.groupby("base_month")["base_date"].max().to_dict()
    for month in all_months:
        anchor = pd.Timestamp(month_anchor[month]).normalize()
        observed = event_rows.loc[event_rows["base_date"].le(anchor)].groupby("merchant_id")["base_date"].max()
        for merchant_id, event_date in observed.items():
            last_event_by_month[(str(merchant_id), month)] = pd.Timestamp(event_date).normalize()

    inactivity_by_month: dict[tuple[str, str], int | None] = {}
    for month in all_months:
        anchor = pd.Timestamp(month_anchor[month]).normalize()
        for merchant_id in all_ids:
            event_date = last_event_by_month.get((merchant_id, month))
            inactivity_by_month[(merchant_id, month)] = int((anchor - event_date).days) if event_date is not None else None

    # Training examples are historical merchant-month observations only; the latest
    # month is held out and scored as the current period.
    training_rows: list[dict[str, float]] = []
    for merchant_id, month in monthly[["merchant_id", "base_month"]].itertuples(index=False, name=None):
        merchant_id, month = str(merchant_id), str(month)
        if month >= latest_month or month not in all_months:
            continue
        prior_month = previous_month(month)
        if (merchant_id, prior_month) not in active_months:
            continue
        features, _ = _feature_row(merchant_id, month, month_map, active_months, inactivity_by_month)
        training_rows.append(features)
    training_features = pd.DataFrame(training_rows, columns=FEATURES)

    # Current and previous comparison periods end on the same calendar day to reduce
    # partial-month bias. Keep approval, cancellation and partial cancellation separate.
    current_rows = df.loc[df["base_month"].eq(latest_month) & df["base_date"].le(cutoff_date)]
    previous_rows = df.loc[df["base_month"].eq(previous) & df["base_date"].dt.day.le(previous_day)]
    metric_names = list(NUMERIC_COLUMNS)
    current_period = current_rows.groupby("merchant_id")[metric_names].sum().to_dict("index")
    previous_period = previous_rows.groupby("merchant_id")[metric_names].sum().to_dict("index")

    current_ids = set(current_rows["merchant_id"].astype(str).unique())
    scored_ids: list[str] = []
    feature_rows: list[dict[str, float]] = []
    info_by_id: dict[str, dict[str, Any]] = {}
    for merchant_id in all_ids:
        if merchant_id not in current_ids:
            continue
        override = current_period.get(merchant_id, {})
        prev_override = previous_period.get(merchant_id, {})
        features, info = _feature_row(
            merchant_id, latest_month, month_map, active_months, inactivity_by_month,
            current_override=override, previous_override=prev_override,
        )
        if not info["has_previous_month"] and not info["has_three_month_baseline"]:
            continue
        scored_ids.append(merchant_id)
        feature_rows.append(features)
        info_by_id[merchant_id] = info
    current_features = pd.DataFrame(feature_rows, columns=FEATURES)
    scores, model_info = score_isolation_forest(training_features, current_features)
    score_by_id = dict(zip(scored_ids, scores))

    merchants: list[dict[str, Any]] = []
    for merchant_id in all_ids:
        last_event = last_event_by_month.get((merchant_id, latest_month))
        if last_event is not None:
            inactive_days = int((cutoff_date - last_event).days)
            last_date_text = last_event.strftime("%Y-%m-%d")
        else:
            # Last known event from the full source history, even if merchant is inactive now.
            source_events = event_rows.loc[event_rows["merchant_id"].eq(merchant_id), "base_date"]
            inactive_days = int((cutoff_date - source_events.max().normalize()).days) if not source_events.empty else None
            last_date_text = source_events.max().strftime("%Y-%m-%d") if not source_events.empty else None

        if merchant_id not in current_ids:
            enterprise_id = enterprise_by_merchant[merchant_id]
            merchants.append({
                "enterprise_id": enterprise_id,
                "enterprise_name": enterprise_names[enterprise_id],
                "merchant_id": merchant_id,
                "merchant_name": names.get(merchant_id, ""),
                "risk_score": None,
                "ai_anomaly_score": None,
                "risk_level": "UNKNOWN",
                "sales_change_rate": None,
                "transaction_change_rate": None,
                "inactive_days": inactive_days,
                "last_transaction_date": last_date_text,
                "current_approval_amount": None,
                "current_approval_count": None,
                "risk_reasons": ["최신 집계월 실적 없음 — 실제 미거래 여부와 데이터 누락을 확인하세요."],
                "data_quality": "NO_LATEST_MONTH_RECORD",
                "available_metrics": [],
                "monthly_sales": merchant_history_by_id.get(merchant_id, []),
                "model": "IsolationForest",
            })
            continue

        info = info_by_id.get(merchant_id)
        if info is None or merchant_id not in score_by_id or model_info.get("status") != "OK":
            cur = current_period.get(merchant_id, {})
            enterprise_id = enterprise_by_merchant[merchant_id]
            merchants.append({
                "enterprise_id": enterprise_id,
                "enterprise_name": enterprise_names[enterprise_id],
                "merchant_id": merchant_id,
                "merchant_name": names.get(merchant_id, ""),
                "risk_score": None,
                "ai_anomaly_score": None,
                "risk_level": "UNKNOWN",
                "sales_change_rate": percent_change(float(cur.get("approval_amount", 0)), float(previous_period.get(merchant_id, {}).get("approval_amount", 0))) if float(previous_period.get(merchant_id, {}).get("approval_amount", 0)) else None,
                "transaction_change_rate": percent_change(float(cur.get("approval_count", 0)), float(previous_period.get(merchant_id, {}).get("approval_count", 0))) if float(previous_period.get(merchant_id, {}).get("approval_count", 0)) else None,
                "inactive_days": inactive_days,
                "last_transaction_date": last_date_text,
                "current_approval_amount": json_number(cur.get("approval_amount", 0)),
                "current_approval_count": json_number(cur.get("approval_count", 0)),
                "risk_reasons": ["AI 점수 산출에 필요한 비교 이력 부족"],
                "data_quality": "INSUFFICIENT_HISTORY",
                "available_metrics": [],
                "monthly_sales": merchant_history_by_id.get(merchant_id, []),
                "model": "IsolationForest",
            })
            continue

        score = float(score_by_id[merchant_id])
        # Relative anomaly ranks against prior observed behavior, not probabilities.
        level = "HIGH" if score >= 98 else "MEDIUM" if score >= 90 else "LOW"
        reasons = _explain(info)
        cur = current_period.get(merchant_id, {})
        enterprise_id = enterprise_by_merchant[merchant_id]
        merchants.append({
            "enterprise_id": enterprise_id,
            "enterprise_name": enterprise_names[enterprise_id],
            "merchant_id": merchant_id,
            "merchant_name": names.get(merchant_id, ""),
            "risk_score": score,
            "ai_anomaly_score": score,
            "risk_level": level,
            "sales_change_rate": info["sales_change_rate"],
            "transaction_change_rate": info["transaction_change_rate"],
            "sales_below_3m_pct": info["sales_below_3m_pct"],
            "transactions_below_3m_pct": info["transactions_below_3m_pct"],
            "inactive_days": info["inactive_days"],
            "last_transaction_date": last_date_text,
            "current_approval_amount": json_number(cur.get("approval_amount", 0)),
            "current_approval_count": json_number(cur.get("approval_count", 0)),
            "risk_reasons": reasons,
            "data_quality": "SUFFICIENT" if info["has_previous_month"] else "PARTIAL",
            "available_metrics": FEATURES,
            "monthly_sales": merchant_history_by_id.get(merchant_id, []),
            "model": "IsolationForest",
        })

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    merchants.sort(key=lambda row: (order.get(row["risk_level"], 9), -(row["risk_score"] or 0), row["merchant_id"]))
    trend = monthly.groupby("base_month", as_index=False).agg(
        approval_amount=("approval_amount", "sum"), approval_count=("approval_count", "sum"),
        cancel_amount=("cancel_amount", "sum"), cancel_count=("cancel_count", "sum"),
        partial_cancel_amount=("partial_cancel_amount", "sum"), partial_cancel_count=("partial_cancel_count", "sum"),
    ).sort_values("base_month").tail(12)
    # Build company-level cards/table rows while keeping the merchant records
    # available for the company detail page. Use enterprise ID as the stable key
    # because company names are not guaranteed to be unique.
    merchant_output = pd.DataFrame(merchants)
    merchant_counts = df[["enterprise_id", "merchant_id"]].drop_duplicates().groupby("enterprise_id")["merchant_id"].nunique().to_dict()
    current_company = current_rows.groupby("enterprise_id")[list(NUMERIC_COLUMNS)].sum()
    previous_company = previous_rows.groupby("enterprise_id")[list(NUMERIC_COLUMNS)].sum()
    enterprise_monthly = monthly.merge(
        df[["merchant_id", "enterprise_id"]].drop_duplicates(), on="merchant_id", how="left"
    ).groupby(["enterprise_id", "base_month"], as_index=False)[list(NUMERIC_COLUMNS)].sum()

    enterprises: list[dict[str, Any]] = []
    for enterprise_id in sorted(enterprise_names.keys()):
        enterprise_merchants = merchant_output.loc[merchant_output["enterprise_id"].astype(str).eq(str(enterprise_id))]
        counts = {level.lower() + "_risk_count": int(enterprise_merchants["risk_level"].eq(level).sum()) for level in ("HIGH", "MEDIUM", "LOW", "UNKNOWN")}
        if counts["high_risk_count"]:
            level = "HIGH"
        elif counts["medium_risk_count"]:
            level = "MEDIUM"
        elif counts["low_risk_count"]:
            level = "LOW"
        else:
            level = "UNKNOWN"

        current_stats = current_company.loc[enterprise_id] if enterprise_id in current_company.index else None
        previous_stats = previous_company.loc[enterprise_id] if enterprise_id in previous_company.index else None
        current_amount = float(current_stats["approval_amount"]) if current_stats is not None else 0.0
        previous_amount = float(previous_stats["approval_amount"]) if previous_stats is not None else 0.0
        current_count = float(current_stats["approval_count"]) if current_stats is not None else 0.0
        previous_count = float(previous_stats["approval_count"]) if previous_stats is not None else 0.0
        valid_scores = pd.to_numeric(enterprise_merchants["risk_score"], errors="coerce").dropna()
        max_score = float(valid_scores.max()) if not valid_scores.empty else None

        history = enterprise_monthly.loc[enterprise_monthly["enterprise_id"].astype(str).eq(str(enterprise_id))].sort_values("base_month").tail(12)
        enterprises.append({
            "enterprise_id": str(enterprise_id),
            "enterprise_name": enterprise_names[enterprise_id],
            "merchant_count": int(merchant_counts.get(enterprise_id, 0)),
            "risk_level": level,
            "risk_score": round(max_score, 1) if max_score is not None else None,
            "sales_change_rate": percent_change(current_amount, previous_amount),
            "transaction_change_rate": percent_change(current_count, previous_count),
            "current_approval_amount": json_number(current_amount),
            "current_approval_count": json_number(current_count),
            "previous_approval_amount": json_number(previous_amount),
            "high_risk_count": counts["high_risk_count"],
            "medium_risk_count": counts["medium_risk_count"],
            "low_risk_count": counts["low_risk_count"],
            "unknown_count": counts["unknown_risk_count"],
            "monthly_sales": [
                {"month": f"{str(row['base_month'])[:4]}-{str(row['base_month'])[4:]}",
                 "approval_amount": json_number(row["approval_amount"]),
                 "approval_count": json_number(row["approval_count"])}
                for _, row in history.iterrows()
            ],
        })

    enterprise_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    enterprises.sort(key=lambda item: (enterprise_order.get(item["risk_level"], 9), -(item["risk_score"] or 0), item["enterprise_name"], item["enterprise_id"]))

    summary = {
        "enterprise_count": len(enterprises),
        "high_risk_enterprise_count": sum(1 for e in enterprises if e["risk_level"] == "HIGH"),
        "medium_risk_enterprise_count": sum(1 for e in enterprises if e["risk_level"] == "MEDIUM"),
        "merchant_count": len(merchants),
        "latest_month_observed_count": sum(1 for m in merchants if m["data_quality"] != "NO_LATEST_MONTH_RECORD"),
        "high_risk_count": sum(1 for m in merchants if m["risk_level"] == "HIGH"),
        "medium_risk_count": sum(1 for m in merchants if m["risk_level"] == "MEDIUM"),
        "low_risk_count": sum(1 for m in merchants if m["risk_level"] == "LOW"),
        "unknown_count": sum(1 for m in merchants if m["risk_level"] == "UNKNOWN"),
    }
    return {
        "analysis_month": f"{latest_month[:4]}-{latest_month[4:]}",
        "as_of_date": cutoff_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": source.name,
        "source_row_count": int(len(df)),
        "source_date_from": pd.Timestamp(df["base_date"].min()).strftime("%Y-%m-%d"),
        "source_date_to": cutoff_date.strftime("%Y-%m-%d"),
        "method": "unsupervised_isolation_forest_v1",
        "model_info": model_info,
        "notice": "마스킹된 원본 정산 데이터를 읽기 전용으로 분석했습니다. AI 이상징후 점수는 과거 가맹점 월별 패턴 대비 상대 점수이며 이탈 확률이 아닙니다. 실제 이탈 라벨이 없으므로 이상징후 탐지로 사용하고 담당자가 검토해야 합니다.",
        "numeric_blank_counts": blank_counts,
        "summary": summary,
        "enterprises": enterprises,
        "monthly_sales": [
            {"month": f"{str(row['base_month'])[:4]}-{str(row['base_month'])[4:]}",
             "approval_amount": json_number(row["approval_amount"]),
             "approval_count": json_number(row["approval_count"]),
             "cancel_amount": json_number(row["cancel_amount"]),
             "cancel_count": json_number(row["cancel_count"]),
             "partial_cancel_amount": json_number(row["partial_cancel_amount"]),
             "partial_cancel_count": json_number(row["partial_cancel_count"])}
            for _, row in trend.iterrows()
        ],
        "merchants": merchants,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="스타플랫폼거래집계.xlsx")
    parser.add_argument("--output", default="public-data/dashboard_summary.json")
    args = parser.parse_args()
    data = build_dashboard(args.input)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = data["summary"]
    print(
        f"AI 분석 완료 | 알고리즘={data['model_info'].get('algorithm')} | 상태={data['model_info'].get('status')} | "
        f"기준월={data['analysis_month']} | 원본행={data['source_row_count']:,} | "
        f"가맹점={summary['merchant_count']:,} | 고위험={summary['high_risk_count']:,} | "
        f"주의={summary['medium_risk_count']:,} | 확인 필요={summary['unknown_count']:,}"
    )


if __name__ == "__main__":
    main()
