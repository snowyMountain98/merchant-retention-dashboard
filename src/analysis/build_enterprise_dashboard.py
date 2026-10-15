"""Build company-level monthly analysis from the source workbook, without merchant-level output.

The workbook is read only. The model operates on enterprise-month aggregates, not
individual merchant records. The output contains enterprise-level summary/history
only and OpenAI-generated company-level text.
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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

COMPANY_ID = "기업관리번호"
COMPANY_NAME = "기업명"
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
REQUIRED_COLUMNS = {COMPANY_ID, COMPANY_NAME, MONTH_COL, DATE_COL, "승인건수", "승인금액"}
FEATURES = [
    "sales_drop_pct", "transaction_drop_pct", "sales_below_3m_pct",
    "transactions_below_3m_pct", "cancel_ratio", "partial_cancel_ratio",
    "consecutive_declines", "sales_volatility_6m_pct",
]


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
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _json_num(value: Any) -> int | float:
    number = float(value or 0)
    return int(number) if number.is_integer() else round(number, 2)


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return float(np.clip(numerator, 0, 5))
    return float(np.clip(numerator / denominator, 0, 5))


def _feature_for_month(
    enterprise_id: str,
    month: str,
    month_map: dict[tuple[str, str], dict[str, float]],
) -> tuple[dict[str, float], dict[str, Any]]:
    current = month_map.get((enterprise_id, month), {})
    prev_month = _prev_month(month)
    previous = month_map.get((enterprise_id, prev_month), {})
    cur_sales = float(current.get("approval_amount", 0))
    cur_count = float(current.get("approval_count", 0))
    prev_sales = float(previous.get("approval_amount", 0)) if previous else None
    prev_count = float(previous.get("approval_count", 0)) if previous else None
    sales_change = _percent(cur_sales, prev_sales)
    count_change = _percent(cur_count, prev_count)

    prior = [month_map.get((enterprise_id, _prev_month(month, offset)), {}) for offset in (1, 2, 3)]
    prior_sales = [float(row.get("approval_amount", 0)) for row in prior if row]
    prior_counts = [float(row.get("approval_count", 0)) for row in prior if row]
    sales_mean = float(np.mean(prior_sales)) if prior_sales else 0.0
    count_mean = float(np.mean(prior_counts)) if prior_counts else 0.0
    sales_below = max(0.0, (sales_mean - cur_sales) / sales_mean * 100) if sales_mean > 0 else 0.0
    count_below = max(0.0, (count_mean - cur_count) / count_mean * 100) if count_mean > 0 else 0.0

    series: list[float] = []
    for offset in (3, 2, 1, 0):
        row = month_map.get((enterprise_id, _prev_month(month, offset))) if offset else current
        series.append(float(row.get("approval_amount", 0)) if row else float("nan"))
    decline_count = sum(1 for left, right in zip(series, series[1:]) if math.isfinite(left) and math.isfinite(right) and left > right)
    recent_sales = [float(month_map[(enterprise_id, _prev_month(month, offset))]["approval_amount"]) for offset in range(1, 7) if (enterprise_id, _prev_month(month, offset)) in month_map]
    volatility = (float(np.std(recent_sales) / np.mean(recent_sales) * 100) if len(recent_sales) > 1 and np.mean(recent_sales) > 0 else 0.0)

    features = {
        "sales_drop_pct": float(np.clip(-(sales_change or 0), 0, 300)),
        "transaction_drop_pct": float(np.clip(-(count_change or 0), 0, 300)),
        "sales_below_3m_pct": float(np.clip(sales_below, 0, 300)),
        "transactions_below_3m_pct": float(np.clip(count_below, 0, 300)),
        "cancel_ratio": _ratio(float(current.get("cancel_amount", 0)), cur_sales),
        "partial_cancel_ratio": _ratio(float(current.get("partial_cancel_amount", 0)), cur_sales),
        "consecutive_declines": float(decline_count),
        "sales_volatility_6m_pct": float(np.clip(volatility, 0, 500)),
    }
    info = {
        "sales_change_rate": sales_change,
        "transaction_change_rate": count_change,
        "sales_below_3m_pct": round(sales_below, 1),
        "transactions_below_3m_pct": round(count_below, 1),
        "cancel_ratio": round(features["cancel_ratio"], 3),
        "partial_cancel_ratio": round(features["partial_cancel_ratio"], 3),
        "consecutive_declines": int(decline_count),
        "sales_volatility_6m_pct": round(volatility, 1),
        "current_approval_amount": cur_sales,
        "current_approval_count": cur_count,
        "previous_approval_amount": prev_sales,
        "previous_approval_count": prev_count,
        "has_previous_month": bool(previous),
        "baseline_month_count": len(prior_sales),
    }
    return features, info


def _reasons(info: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if info.get("sales_change_rate") is not None and info["sales_change_rate"] <= -10:
        reasons.append("기업 승인금액이 전월 대비 10% 이상 감소")
    if info.get("transaction_change_rate") is not None and info["transaction_change_rate"] <= -10:
        reasons.append("기업 승인건수가 전월 대비 10% 이상 감소")
    if info.get("sales_below_3m_pct", 0) >= 20:
        reasons.append("기업 승인금액이 직전 3개월 평균보다 20% 이상 낮음")
    if info.get("transactions_below_3m_pct", 0) >= 20:
        reasons.append("기업 승인건수가 직전 3개월 평균보다 20% 이상 낮음")
    if info.get("consecutive_declines", 0) >= 2:
        reasons.append("기업 월별 승인금액이 연속 하락")
    if info.get("cancel_ratio", 0) >= 0.2:
        reasons.append("기업 취소금액/승인금액 비율이 20% 이상")
    if info.get("partial_cancel_ratio", 0) >= 0.2:
        reasons.append("기업 부분취소금액/승인금액 비율이 20% 이상")
    return reasons


def _score_enterprises(training: pd.DataFrame, current: pd.DataFrame) -> tuple[list[float | None], dict[str, Any]]:
    scores: list[float | None] = [None] * len(current)
    if current.empty:
        return scores, {"algorithm": "IsolationForest", "status": "NO_COMPANIES_WITH_COMPARABLE_MONTH"}
    train = training[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0)
    now = current[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0)
    if len(train) >= 30 and len(train.drop_duplicates()) >= 2:
        fit_data = train
        source = "historical_enterprise_months"
    elif len(now) >= 30 and len(now.drop_duplicates()) >= 2:
        fit_data = now
        source = "current_enterprise_cross_section_short_history"
    else:
        return [50.0] * len(current), {"algorithm": "IsolationForest", "status": "INSUFFICIENT_ENTERPRISE_DATA", "fit_source": "none", "training_rows": len(train)}

    pipeline = Pipeline([
        ("scale", RobustScaler()),
        ("model", IsolationForest(n_estimators=300, contamination="auto", random_state=42, n_jobs=-1)),
    ])
    pipeline.fit(fit_data)
    reference = -pipeline.decision_function(fit_data)
    current_scores = -pipeline.decision_function(now)
    for i, value in enumerate(current_scores):
        scores[i] = round(float(np.mean(reference <= value) * 100), 1)
    return scores, {
        "algorithm": "IsolationForest",
        "status": "OK",
        "fit_source": source,
        "training_rows": int(len(train)),
        "scored_enterprises": int(len(current)),
        "feature_names": FEATURES,
        "score_definition": "기업별 월간 지표에 대한 이상징후 상대 백분위이며 이탈 확률이 아님",
        "risk_thresholds": {"HIGH": 98, "MEDIUM": 90, "LOW": 0},
        "random_state": 42,
    }


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

    monthly = df.groupby(["enterprise_id", "enterprise_name", "base_month"], as_index=False)[list(NUMERIC_COLUMNS)].sum()
    latest_month = str(monthly["base_month"].max())
    month_map: dict[tuple[str, str], dict[str, float]] = {}
    for _, row in monthly.iterrows():
        month_map[(str(row["enterprise_id"]), str(row["base_month"]))] = {metric: float(row[metric]) for metric in NUMERIC_COLUMNS}

    training_rows: list[dict[str, float]] = []
    for enterprise_id, month in monthly[["enterprise_id", "base_month"]].itertuples(index=False, name=None):
        enterprise_id, month = str(enterprise_id), str(month)
        if month >= latest_month or (enterprise_id, _prev_month(month)) not in month_map:
            continue
        features, _ = _feature_for_month(enterprise_id, month, month_map)
        training_rows.append(features)
    training = pd.DataFrame(training_rows, columns=FEATURES)

    current_records: list[dict[str, Any]] = []
    current_features: list[dict[str, float]] = []
    for enterprise_id, enterprise_name in monthly[["enterprise_id", "enterprise_name"]].drop_duplicates().itertuples(index=False, name=None):
        enterprise_id, enterprise_name = str(enterprise_id), str(enterprise_name)
        current = month_map.get((enterprise_id, latest_month))
        if current is None:
            current_records.append({"enterprise_id": enterprise_id, "enterprise_name": enterprise_name, "has_current": False, "features": None, "info": None})
            continue
        features, info = _feature_for_month(enterprise_id, latest_month, month_map)
        if not info["has_previous_month"]:
            current_records.append({"enterprise_id": enterprise_id, "enterprise_name": enterprise_name, "has_current": True, "features": None, "info": info})
            continue
        current_records.append({"enterprise_id": enterprise_id, "enterprise_name": enterprise_name, "has_current": True, "features": features, "info": info})
        current_features.append({"enterprise_id": enterprise_id, **features})

    current_frame = pd.DataFrame(current_features)
    if not current_frame.empty:
        scores, model_info = _score_enterprises(training, current_frame)
        score_by_id = {str(row["enterprise_id"]): scores[i] for i, (_, row) in enumerate(current_frame.iterrows())}
    else:
        model_info = {"algorithm": "IsolationForest", "status": "NO_COMPARABLE_COMPANY_HISTORY", "training_rows": int(len(training))}
        score_by_id = {}

    names = monthly.drop_duplicates("enterprise_id").set_index("enterprise_id")["enterprise_name"].astype(str).to_dict()
    enterprise_rows: list[dict[str, Any]] = []
    for enterprise_id in sorted(names):
        enterprise_id = str(enterprise_id)
        history = monthly.loc[monthly["enterprise_id"].astype(str).eq(enterprise_id)].sort_values("base_month").tail(12)
        record = next(r for r in current_records if r["enterprise_id"] == enterprise_id)
        info = record.get("info") or {}
        score = score_by_id.get(enterprise_id)
        if not record.get("has_current") or score is None:
            risk_level = "UNKNOWN"
        elif score >= 98:
            risk_level = "HIGH"
        elif score >= 90:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"
        reasons = _reasons(info) if info else []
        if risk_level == "HIGH":
            reasons.insert(0, "기업 단위 월간 패턴이 기준 분포에서 매우 이례적")
        elif risk_level == "MEDIUM":
            reasons.insert(0, "기업 단위 월간 패턴에 확인이 필요한 이상징후가 있음")
        if risk_level == "UNKNOWN":
            reasons.append("최신월 또는 전월 비교 데이터가 부족하여 점수를 계산할 수 없음")

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
        enterprise_rows.append({
            "enterprise_id": enterprise_id,
            "enterprise_name": names[enterprise_id],
            "display_name": names[enterprise_id],
            "risk_level": risk_level,
            "risk_score": score,
            "sales_change_rate": info.get("sales_change_rate"),
            "transaction_change_rate": info.get("transaction_change_rate"),
            "sales_below_3m_pct": info.get("sales_below_3m_pct"),
            "transactions_below_3m_pct": info.get("transactions_below_3m_pct"),
            "baseline_month_count": info.get("baseline_month_count", 0),
            "current_approval_amount": _json_num(info.get("current_approval_amount", 0)),
            "current_approval_count": _json_num(info.get("current_approval_count", 0)),
            "previous_approval_amount": _json_num(info.get("previous_approval_amount", 0) or 0),
            "previous_approval_count": _json_num(info.get("previous_approval_count", 0) or 0),
            "risk_reasons": reasons,
            "monthly_sales": history_json,
        })

    ordering = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    enterprise_rows.sort(key=lambda e: (ordering.get(e["risk_level"], 9), -(e["risk_score"] or 0), e["enterprise_name"], e["enterprise_id"]))

    monthly_totals = monthly.groupby("base_month", as_index=False)[list(NUMERIC_COLUMNS)].sum().sort_values("base_month").tail(12)
    summary = {
        "enterprise_count": len(enterprise_rows),
        "high_risk_enterprise_count": sum(e["risk_level"] == "HIGH" for e in enterprise_rows),
        "medium_risk_enterprise_count": sum(e["risk_level"] == "MEDIUM" for e in enterprise_rows),
        "low_risk_enterprise_count": sum(e["risk_level"] == "LOW" for e in enterprise_rows),
        "unknown_enterprise_count": sum(e["risk_level"] == "UNKNOWN" for e in enterprise_rows),
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
        "method": "enterprise_month_isolation_forest_v1",
        "model_info": model_info,
        "notice": "기업별 월간 집계만 분석했습니다. 개별 가맹점 분석은 수행하거나 공개하지 않습니다. Isolation Forest 점수는 기업 거래 패턴 대비 이상징후 상대 점수이며 이탈 확률이 아닙니다. 외부 원인은 별도 근거가 있는 경우에만 추가 검토해야 합니다.",
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
    print(f"기업 단위 분석 완료 | 원본행={result['source_row_count']:,} | 기업={summary['enterprise_count']:,} | 고위험={summary['high_risk_enterprise_count']:,} | 주의={summary['medium_risk_enterprise_count']:,}")


if __name__ == "__main__":
    main()
