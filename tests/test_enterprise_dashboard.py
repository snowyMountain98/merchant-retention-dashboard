import pandas as pd

from src.analysis.build_enterprise_dashboard import build_enterprise_dashboard


def test_builds_only_enterprise_level_output(tmp_path):
    rows = []
    values = {
        "2026-05": [("1999", "Starbucks", 1000, 10), ("2000", "Masked Corp", 2000, 20)],
        "2026-06": [("1999", "Starbucks", 800, 8), ("2000", "Masked Corp", 2100, 21)],
        "2026-07": [("1999", "Starbucks", 700, 7), ("2000", "Masked Corp", 2000, 20)],
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
    assert target["display_name"] == "스타벅스"
    assert target["monthly_sales"][-1]["approval_amount"] == 700
    assert target["sales_change_rate"] == -12.5
    assert all("merchant_id" not in row and "merchant_name" not in row for row in result["enterprises"])
