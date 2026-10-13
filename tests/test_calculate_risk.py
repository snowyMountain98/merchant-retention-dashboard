from src.analysis.calculate_risk import calculate_risk_score
from src.analysis.aggregate_monthly import aggregate_monthly
import pandas as pd


def test_high_risk_has_reasons():
    result = calculate_risk_score(-35, -40, 20, True)
    assert result["risk_score"] == 100
    assert result["risk_level"] == "HIGH"
    assert len(result["risk_reasons"]) == 4


def test_missing_metrics_are_marked_partial():
    result = calculate_risk_score(None, -35, None, None)
    assert result["risk_score"] == 25
    assert result["data_quality"] == "PARTIAL"


def test_monthly_aggregation_sums_all_breakdown_rows():
    df = pd.DataFrame([
        {"스타플랫폼가맹점번호":"S1", "집계년월":"202609", "승인건수":"2", "승인금액":"100"},
        {"스타플랫폼가맹점번호":"S1", "집계년월":"202609", "승인건수":"3", "승인금액":"200"},
    ])
    result = aggregate_monthly(df)
    assert len(result) == 1
    assert result.loc[0, "approval_count"] == 5
    assert result.loc[0, "approval_amount"] == 300
