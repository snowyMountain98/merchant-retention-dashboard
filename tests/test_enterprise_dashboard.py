import pandas as pd

from src.analysis.build_enterprise_dashboard import build_enterprise_dashboard


def test_builds_only_enterprise_level_output_and_preserves_masked_names(tmp_path):
    rows = []
    values = {
        "2026-05": [("1999", "마스킹기업 A", 1000, 10), ("2000", "마스킹기업 B", 2000, 20)],
        "2026-06": [("1999", "마스킹기업 A", 800, 8), ("2000", "마스킹기업 B", 2100, 21)],
        # Enterprise 1999 has no row in July: interpret that month as zero payments.
        "2026-07": [("2000", "마스킹기업 B", 2000, 20)],
    }
    for month, entries in values.items():
        date = month.replace("-", "") + "15"
        for enterprise_id, enterprise_name, amount, count in entries:
            rows.append({
                "기업관리번호": enterprise_id,
                "기업명": enterprise_name,
                "집계년월": month.replace("-", ""),
                "집계년월일": date,
                "승인건수": count,
                "승인금액": amount,
                "취소건수": 0,
                "취소금액": 0,
                "부분취소건수": 0,
                "부분취소금액": 0,
            })
    source = tmp_path / "synthetic_enterprise.xlsx"
    pd.DataFrame(rows).to_excel(source, index=False)

    result = build_enterprise_dashboard(source)
    assert result["summary"]["enterprise_count"] == 2
    assert len(result["enterprises"]) == 2
    assert "merchants" not in result
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    assert target["enterprise_name"] == "마스킹기업 A"
    assert target["display_name"] == "마스킹기업 A"
    assert target["monthly_sales"][-1]["approval_amount"] == 0
    assert target["monthly_sales"][-1]["approval_count"] == 0
    assert target["sales_change_rate"] == -100.0
    assert target["transaction_change_rate"] == -100.0
    assert any("10% 이상 감소" in reason for reason in target["risk_reasons"])
    assert all("merchant_id" not in row and "merchant_name" not in row for row in result["enterprises"])


def test_zero_payments_in_two_consecutive_months_are_not_marked_unknown(tmp_path):
    rows = []
    values = {
        "2026-05": [("1999", "마스킹기업 A", 1000, 10), ("2000", "마스킹기업 B", 2000, 20)],
        "2026-06": [("2000", "마스킹기업 B", 2100, 21)],
        "2026-07": [("2000", "마스킹기업 B", 2000, 20)],
    }
    for month, entries in values.items():
        date = month.replace("-", "") + "15"
        for enterprise_id, enterprise_name, amount, count in entries:
            rows.append({
                "기업관리번호": enterprise_id,
                "기업명": enterprise_name,
                "집계년월": month.replace("-", ""),
                "집계년월일": date,
                "승인건수": count,
                "승인금액": amount,
                "취소건수": 0,
                "취소금액": 0,
                "부분취소건수": 0,
                "부분취소금액": 0,
            })
    source = tmp_path / "synthetic_zero_activity.xlsx"
    pd.DataFrame(rows).to_excel(source, index=False)

    result = build_enterprise_dashboard(source)
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    assert [m["month"] for m in target["monthly_sales"]] == ["2026-05", "2026-06", "2026-07"]
    assert [(m["approval_amount"], m["approval_count"]) for m in target["monthly_sales"][-2:]] == [(0, 0), (0, 0)]
    assert target["risk_level"] != "UNKNOWN"
    assert target["sales_change_rate"] == 0.0
    assert target["transaction_change_rate"] == 0.0
