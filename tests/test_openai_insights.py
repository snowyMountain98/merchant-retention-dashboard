import json

from src.analysis.generate_openai_insights import (
    build_company_payloads,
    build_merchant_payloads,
    build_portfolio_payload,
    generate_insights,
)


def sample_data():
    return {
        "analysis_month": "2026-09",
        "as_of_date": "2026-09-30",
        "summary": {
            "enterprise_count": 1,
            "merchant_count": 1,
            "high_risk_enterprise_count": 1,
            "medium_risk_enterprise_count": 0,
            "high_risk_count": 1,
            "medium_risk_count": 0,
            "unknown_count": 0,
        },
        "monthly_sales": [{"month": "2026-09", "approval_amount": 800, "approval_count": 8}],
        "enterprises": [{
            "enterprise_id": "ORG_SECRET_1",
            "enterprise_name": "PRIVATE COMPANY NAME",
            "risk_level": "HIGH", "risk_score": 99.1,
            "merchant_count": 1, "high_risk_count": 1, "medium_risk_count": 0,
            "low_risk_count": 0, "unknown_count": 0,
            "current_approval_amount": 800, "previous_approval_amount": 1500,
            "sales_change_rate": -46.7, "transaction_change_rate": -35,
            "monthly_sales": [{"month": "2026-09", "approval_amount": 800, "approval_count": 8}],
        }],
        "merchants": [{
            "enterprise_id": "ORG_SECRET_1", "enterprise_name": "PRIVATE COMPANY NAME",
            "merchant_id": "MERCHANT_SECRET_1", "merchant_name": "PRIVATE MERCHANT NAME",
            "risk_level": "HIGH", "risk_score": 99.3, "data_quality": "SUFFICIENT",
            "sales_change_rate": -46.7, "transaction_change_rate": -35,
            "sales_below_3m_pct": 30, "transactions_below_3m_pct": 22,
            "inactive_days": 8, "last_transaction_date": "2026-09-22",
            "current_approval_amount": 800, "current_approval_count": 8,
            "risk_reasons": ["승인금액 감소"],
            "monthly_sales": [{"month": "2026-09", "approval_amount": 800, "approval_count": 8}],
        }],
    }


def test_openai_payloads_omit_names_and_real_identifiers():
    data = sample_data()
    portfolio = json.dumps(build_portfolio_payload(data), ensure_ascii=False)
    company_batches, _ = build_company_payloads(data)
    merchant_batches, _ = build_merchant_payloads(data)
    prompts = portfolio + json.dumps(company_batches, ensure_ascii=False) + json.dumps(merchant_batches, ensure_ascii=False)
    for private_value in ["ORG_SECRET_1", "PRIVATE COMPANY NAME", "MERCHANT_SECRET_1", "PRIVATE MERCHANT NAME"]:
        assert private_value not in prompts


def test_generate_insights_maps_opaque_refs_to_dashboard_records():
    data = sample_data()

    def fake_call(client, model, instructions, payload, schema_name, schema, max_output_tokens=5000):
        if schema_name == "portfolio_insight":
            return {
                "headline": "매출 하락 집중 점검",
                "summary": "승인금액이 감소했습니다.",
                "focus_areas": ["거래 감소 추이"],
                "recommended_actions": ["최근 거래 내역 확인"],
                "limitations": "원인 확정은 추가 확인이 필요합니다.",
            }
        items = payload.get("companies") or payload.get("merchants") or []
        if schema_name == "company_insights":
            return {"items": [{
                "ref": item["ref"], "headline": "우선 점검", "summary": "집계 지표를 확인하세요.",
                "observations": ["승인금액 감소"], "recommended_actions": ["추이 확인"],
                "limitations": "원인은 확정할 수 없습니다.",
            } for item in items]}
        return {"items": [{
            "ref": item["ref"], "summary": "변화 추이를 확인하세요.",
            "signals": ["승인금액 감소"], "recommended_actions": ["거래 흐름 확인"],
            "contact_script": "최근 이용 상황을 확인드리고자 연락드렸습니다.",
            "limitations": "이탈을 확정하는 진단은 아닙니다.",
        } for item in items]}

    result = generate_insights(data, client=object(), model="test-model", call=fake_call)
    assert result["openai_portfolio_insight"]["headline"] == "매출 하락 집중 점검"
    assert result["enterprises"][0]["openai_insight"]["headline"] == "우선 점검"
    assert result["merchants"][0]["openai_insight"]["contact_script"].startswith("최근 이용")
    assert result["openai_metadata"]["status"] == "success"
