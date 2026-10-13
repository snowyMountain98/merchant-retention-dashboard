"""Initial transparent heuristic; score is not a calibrated churn probability."""
from __future__ import annotations
from typing import Any


def calculate_risk_score(sales_change_rate: float | None, transaction_change_rate: float | None,
                         inactive_days: int | None, consecutive_decline: bool | None) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    available: list[str] = []
    if sales_change_rate is not None:
        available.append("sales_change_rate")
        if sales_change_rate <= -30:
            score += 35; reasons.append("매출 30% 이상 감소")
    if transaction_change_rate is not None:
        available.append("transaction_change_rate")
        if transaction_change_rate <= -30:
            score += 25; reasons.append("거래 건수 30% 이상 감소")
    if inactive_days is not None:
        available.append("inactive_days")
        if inactive_days >= 14:
            score += 25; reasons.append("14일 이상 거래 공백")
    if consecutive_decline is not None:
        available.append("consecutive_decline")
        if consecutive_decline:
            score += 15; reasons.append("3개월 연속 매출 감소")
    level = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
    return {"risk_score":min(score, 100), "risk_level":level, "risk_reasons":reasons,
            "available_metrics":available, "data_quality":"SUFFICIENT" if len(available) == 4 else "PARTIAL",
            "method":"rule_based_v1"}
