import json

from src.analysis.generate_openai_insights import (
    build_company_payloads,
    build_portfolio_payload,
    generate_insights,
)


def sample_data():
    return {
        "analysis_month": "2026-09",
        "as_of_date": "2026-09-30",
        "summary": {"enterprise_count": 2, "high_risk_enterprise_count": 1, "medium_risk_enterprise_count": 1},
        "monthly_sales": [{"month": "2026-08", "approval_amount": 2000, "approval_count": 20}, {"month": "2026-09", "approval_amount": 1800, "approval_count": 18}],
        "enterprises": [
            {
                "enterprise_id": "ORG_SECRET_1", "enterprise_name": "PRIVATE COMPANY NAME", "risk_level": "HIGH", "risk_score": 99.1,
                "merchant_count": 3, "high_risk_count": 2, "medium_risk_count": 0, "low_risk_count": 1, "unknown_count": 0,
                "current_approval_amount": 800, "previous_approval_amount": 1500, "sales_change_rate": -46.7,
                "transaction_change_rate": -35, "monthly_sales": [{"month": "2026-09", "approval_amount": 800, "approval_count": 8}],
            },
            {
                "enterprise_id": "1999", "enterprise_name": "MASKED_NAME", "risk_level": "MEDIUM", "risk_score": 92.5,
                "merchant_count": 2, "high_risk_count": 1, "medium_risk_count": 1, "low_risk_count": 0, "unknown_count": 0,
                "current_approval_amount": 900, "previous_approval_amount": 1000, "sales_change_rate": -10.0,
                "transaction_change_rate": -8.0, "monthly_sales": [{"month": "2026-05", "approval_amount": 1000, "approval_count": 10}, {"month": "2026-06", "approval_amount": 800, "approval_count": 8}],
                "monthly_change_evidence": {"status": "completed", "months": [{"month": "2026-06", "change_rate_pct": -20.0, "direction": "DOWN", "status": "found", "results": [{"evidence_id": "2026-06-E01", "title": "스타벅스 기사", "snippet": "공개 기사 요약", "published_at": "2026-07-04", "url": "https://example.org/news"}]}]},
            },
        ],
        "merchants": [{"enterprise_id": "ORG_SECRET_1", "enterprise_name": "PRIVATE COMPANY NAME", "merchant_id": "MERCHANT_SECRET_1", "merchant_name": "PRIVATE MERCHANT NAME"}],
    }


def test_openai_payloads_use_opaque_refs_and_only_enterprise_metrics():
    data = sample_data()
    portfolio = json.dumps(build_portfolio_payload(data), ensure_ascii=False)
    company_batches, _ = build_company_payloads(data)
    prompt_data = portfolio + json.dumps(company_batches, ensure_ascii=False)
    for private_value in ["ORG_SECRET_1", "PRIVATE COMPANY NAME", "MERCHANT_SECRET_1", "PRIVATE MERCHANT NAME"]:
        assert private_value not in prompt_data
    assert "monthly_change_evidence" in json.dumps(company_batches, ensure_ascii=False)
    assert "merchant_id" not in prompt_data


def test_generate_insights_outputs_company_only_and_grounded_month_causes():
    data = sample_data()

    def fake_call(client, model, instructions, payload, schema_name, schema, max_output_tokens=5000):
        if schema_name == "portfolio_insight":
            return {"headline": "기업 포트폴리오 요약", "summary": "전반 실적을 확인하세요.", "focus_areas": ["매출 변화"], "recommended_actions": ["변화 기업을 검토"], "limitations": "원인은 단정할 수 없습니다."}
        items = payload["companies"]
        return {"items": [{
            "ref": item["ref"], "headline": "기업별 점검", "summary": "월별 추이를 검토하세요.",
            "observations": ["승인금액 변화"], "recommended_actions": ["관련 근거를 확인"],
            "limitations": "검색 증거는 인과관계를 입증하지 않습니다.",
            "monthly_cause_hypotheses": ([{
                "month": "2026-06", "change_rate_pct": -20.0, "direction": "DOWN",
                "candidate_reasons": [{"reason": "공개 이슈와 시기상 연관 가능성", "evidence_ids": ["2026-06-E01"], "confidence": "LOW", "relationship": "기사 발행 시점과 승인금액 변화의 시간적 연관성만 관찰됩니다."}],
                "limitations": "인과관계는 확정되지 않습니다.",
            }] if "monthly_change_evidence" in item else []),
        } for item in items]}

    result = generate_insights(data, client=object(), model="test-model", call=fake_call)
    assert result["openai_portfolio_insight"]["headline"] == "기업 포트폴리오 요약"
    assert "merchants" not in result
    assert "openai_insight" in result["enterprises"][0]
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    assert target["openai_insight"]["monthly_cause_hypotheses"][0]["candidate_reasons"][0]["evidence_ids"] == ["2026-06-E01"]
    other = next(e for e in result["enterprises"] if e["enterprise_id"] == "ORG_SECRET_1")
    assert other["openai_insight"]["monthly_cause_hypotheses"] == []
    output = json.dumps(result, ensure_ascii=False)
    assert "MERCHANT_SECRET_1" not in output
    assert result["openai_metadata"]["scope"] == "enterprise_only"
