import pandas as pd

from src.analysis.build_dashboard import build_dashboard


def test_enterprise_summary_and_merchant_drilldown(tmp_path):
    rows = []
    for month, date_suffix, values in [
        ("202608", "20260810", [(100, 10), (200, 20), (300, 30)]),
        ("202609", "20260910", [(90, 9), (210, 21), (250, 25)]),
    ]:
        for idx, (amount, count) in enumerate(values, start=1):
            enterprise_id = "E1" if idx in (1, 2) else "E2"
            enterprise_name = "기업 A" if enterprise_id == "E1" else "기업 B"
            rows.append({
                "기업관리번호": enterprise_id,
                "기업명": enterprise_name,
                "스타플랫폼가맹점번호": f"M{idx:03d}",
                "가맹점명": f"가맹점 {idx}",
                "집계년월": month,
                "집계년월일": date_suffix,
                "승인건수": count,
                "승인금액": amount,
                "취소건수": 0,
                "취소금액": 0,
                "부분취소건수": 0,
                "부분취소금액": 0,
            })
    source = tmp_path / "synthetic.xlsx"
    pd.DataFrame(rows).to_excel(source, index=False)

    result = build_dashboard(source)

    assert result["summary"]["enterprise_count"] == 2
    assert result["summary"]["merchant_count"] == 3
    assert sum(e["merchant_count"] for e in result["enterprises"]) == 3
    company_1 = next(e for e in result["enterprises"] if e["enterprise_id"] == "E1")
    assert company_1["enterprise_name"] == "기업 A"
    assert company_1["merchant_count"] == 2
    company_merchants = [m for m in result["merchants"] if m["enterprise_id"] == "E1"]
    assert len(company_merchants) == 2
    assert all(m["enterprise_name"] == "기업 A" for m in company_merchants)
    assert all(isinstance(m["monthly_sales"], list) and len(m["monthly_sales"]) == 2 for m in company_merchants)
