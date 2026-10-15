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
                "결제수단": "카드",
                "금융회사": "신한카드",
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
    assert target["transaction_status"] == "RECENTLY_INACTIVE"
    assert target["transaction_status_label"] == "최근 미거래"
    assert any("최근 1개월" in reason for reason in target["status_reasons"])
    assert "risk_level" not in target and "risk_score" not in target and "risk_reasons" not in target
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
                "결제수단": "카드",
                "금융회사": "신한카드",
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
    assert target["transaction_status"] == "RECENTLY_INACTIVE"
    assert target["zero_activity_streak"] == 2
    assert target["sales_change_rate"] == 0.0
    assert target["transaction_change_rate"] == 0.0


def test_payment_method_and_financial_company_analyses_are_aggregated_and_zero_filled(tmp_path):
    rows = [
        {"기업관리번호": "A1", "기업명": "마스킹기업 A", "집계년월": "202605", "집계년월일": "20260515", "결제수단": "카드", "금융회사": "신한카드", "승인건수": 6, "승인금액": 600, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
        {"기업관리번호": "A1", "기업명": "마스킹기업 A", "집계년월": "202605", "집계년월일": "20260515", "결제수단": "계좌", "금융회사": "KB국민은행", "승인건수": 4, "승인금액": 400, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
        {"기업관리번호": "A1", "기업명": "마스킹기업 A", "집계년월": "202606", "집계년월일": "20260615", "결제수단": "카드", "금융회사": "신한카드", "승인건수": 2, "승인금액": 200, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
        {"기업관리번호": "A1", "기업명": "마스킹기업 A", "집계년월": "202606", "집계년월일": "20260615", "결제수단": "계좌", "금융회사": "KB국민은행", "승인건수": 8, "승인금액": 800, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
        {"기업관리번호": "B2", "기업명": "마스킹기업 B", "집계년월": "202605", "집계년월일": "20260515", "결제수단": "카드", "금융회사": "현대카드", "승인건수": 10, "승인금액": 1200, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
        {"기업관리번호": "B2", "기업명": "마스킹기업 B", "집계년월": "202606", "집계년월일": "20260615", "결제수단": "카드", "금융회사": "현대카드", "승인건수": 11, "승인금액": 1300, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
        {"기업관리번호": "B2", "기업명": "마스킹기업 B", "집계년월": "202607", "집계년월일": "20260715", "결제수단": "카드", "금융회사": "현대카드", "승인건수": 12, "승인금액": 1400, "취소건수": 0, "취소금액": 0, "부분취소건수": 0, "부분취소금액": 0},
    ]
    source = tmp_path / "synthetic_dimensions.xlsx"
    pd.DataFrame(rows).to_excel(source, index=False)

    result = build_enterprise_dashboard(source)
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "A1")
    methods = {x["payment_method"]: x["monthly_sales"] for x in target["payment_method_analysis"]}
    finance = {x["financial_company"]: x["monthly_sales"] for x in target["financial_company_analysis"]}
    assert set(methods) == {"카드", "계좌"}
    assert methods["카드"][0]["approval_amount"] == 600
    assert methods["계좌"][1]["approval_amount"] == 800
    assert methods["카드"][2]["approval_amount"] == 0
    assert methods["계좌"][2]["approval_count"] == 0
    assert finance["신한카드"][1]["approval_amount"] == 200
    assert finance["KB국민은행"][2]["approval_amount"] == 0
    for month in target["monthly_sales"]:
        month_id = month["month"]
        method_amount = sum(next(row["approval_amount"] for row in item["monthly_sales"] if row["month"] == month_id) for item in target["payment_method_analysis"])
        finance_amount = sum(next(row["approval_amount"] for row in item["monthly_sales"] if row["month"] == month_id) for item in target["financial_company_analysis"])
        assert method_amount == month["approval_amount"]
        assert finance_amount == month["approval_amount"]
    payload = __import__("json").dumps(result, ensure_ascii=False)
    assert "SECRET_MERCHANT" not in payload and "merchant_id" not in payload


def test_transaction_statuses_describe_observed_activity_not_anomaly_scores(tmp_path):
    rows = []
    monthly_values = {
        "2026-01": [("A", "장기 미거래", 1000, 10), ("B", "최근 미거래", 500, 5), ("C", "거래 유지", 1000, 10), ("D", "감소 관찰", 1000, 10), ("E", "거래 재개", 500, 5)],
        "2026-02": [("A", "장기 미거래", 1000, 10), ("B", "최근 미거래", 500, 5), ("C", "거래 유지", 1000, 10), ("D", "감소 관찰", 1000, 10), ("E", "거래 재개", 500, 5)],
        "2026-03": [("A", "장기 미거래", 1000, 10), ("B", "최근 미거래", 500, 5), ("C", "거래 유지", 1000, 10), ("D", "감소 관찰", 1000, 10), ("E", "거래 재개", 500, 5)],
        "2026-04": [("B", "최근 미거래", 500, 5), ("C", "거래 유지", 1000, 10), ("D", "감소 관찰", 1000, 10), ("E", "거래 재개", 500, 5)],
        "2026-05": [("B", "최근 미거래", 500, 5), ("C", "거래 유지", 1000, 10), ("D", "감소 관찰", 1000, 10)],
        "2026-06": [("C", "거래 유지", 1000, 10), ("D", "감소 관찰", 600, 6), ("E", "거래 재개", 650, 7), ("F", "첫 거래", 700, 7)],
    }
    for month, entries in monthly_values.items():
        for enterprise_id, enterprise_name, amount, count in entries:
            rows.append({
                "기업관리번호": enterprise_id, "기업명": enterprise_name,
                "집계년월": month.replace("-", ""), "집계년월일": month.replace("-", "") + "15",
                "결제수단": "카드", "금융회사": "신한카드",
                "승인건수": count, "승인금액": amount, "취소건수": 0, "취소금액": 0,
                "부분취소건수": 0, "부분취소금액": 0,
            })
    source = tmp_path / "synthetic_transaction_states.xlsx"
    pd.DataFrame(rows).to_excel(source, index=False)

    result = build_enterprise_dashboard(source)
    by_id = {company["enterprise_id"]: company for company in result["enterprises"]}
    assert by_id["A"]["transaction_status"] == "LONG_INACTIVE"
    assert by_id["B"]["transaction_status"] == "RECENTLY_INACTIVE"
    assert by_id["C"]["transaction_status"] == "TRADING"
    assert by_id["D"]["transaction_status"] == "DECLINING"
    assert by_id["E"]["transaction_status"] == "RESTARTED"
    assert by_id["F"]["transaction_status"] == "FIRST_ACTIVITY"
    assert result["summary"]["long_inactive_enterprise_count"] == 1
    assert all("risk_level" not in company and "risk_score" not in company for company in result["enterprises"])
    assert result["method"] == "enterprise_month_transaction_status_v1_zero_fill"
