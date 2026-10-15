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
        "monthly_sales": [
            {"month": "2026-08", "approval_amount": 2000, "approval_count": 20},
            {"month": "2026-09", "approval_amount": 1800, "approval_count": 18},
        ],
        "enterprises": [
            {
                "enterprise_id": "MASKED_ID_1", "enterprise_name": "마스킹기업 A", "display_name": "마스킹기업 A",
                "risk_level": "HIGH", "risk_score": 99.1,
                "merchant_count": 3, "current_approval_amount": 800, "previous_approval_amount": 1500,
                "sales_change_rate": -46.7, "transaction_change_rate": -35,
                "monthly_sales": [{"month": "2026-09", "approval_amount": 800, "approval_count": 8}],
            },
            {
                "enterprise_id": "MASKED_ID_2", "enterprise_name": "마스킹기업 B", "display_name": "마스킹기업 B",
                "risk_level": "MEDIUM", "risk_score": 92.5,
                "merchant_count": 2, "current_approval_amount": 900, "previous_approval_amount": 1000,
                "sales_change_rate": -10.0, "transaction_change_rate": -8.0,
                "monthly_sales": [
                    {"month": "2026-08", "approval_amount": 1000, "approval_count": 10},
                    {"month": "2026-09", "approval_amount": 900, "approval_count": 9},
                ],
            },
        ],
        "merchants": [{"merchant_id": "SECRET_MERCHANT", "merchant_name": "개별 가맹점"}],
    }


def test_openai_payloads_contain_only_opaque_enterprise_refs_and_aggregates():
    data = sample_data()
    batches, ref_to_id = build_company_payloads(data)
    payload_json = json.dumps(batches, ensure_ascii=False)
    assert ref_to_id == {"company_0001": "MASKED_ID_1", "company_0002": "MASKED_ID_2"}
    assert "enterprise_name" not in payload_json
    assert "display_name" not in payload_json
    assert "merchant_id" not in payload_json
    assert "SECRET_MERCHANT" not in payload_json


def test_portfolio_payload_is_aggregate_only():
    payload = build_portfolio_payload(sample_data())
    payload_json = json.dumps(payload, ensure_ascii=False)
    assert payload["enterprise_metrics"]["enterprise_count"] == 2
    assert "merchant_id" not in payload_json
    assert "SECRET_MERCHANT" not in payload_json


def test_generate_insights_keeps_enterprise_only_and_no_search_analysis():
    data = sample_data()
    calls = []

    def fake_call(client, model, instructions, payload, schema_name, schema, max_output_tokens=5000):
        calls.append((schema_name, instructions, payload))
        if schema_name == "portfolio_insight":
            return {
                "headline": "기업 포트폴리오 점검",
                "summary": "일부 기업의 승인금액 감소가 관측되었습니다.",
                "focus_areas": ["승인금액 감소 기업"],
                "recommended_actions": ["원천 집계와 거래 추이를 확인"],
                "limitations": "집계 지표만으로 원인을 확정할 수 없습니다.",
            }
        return {
            "items": [
                {
                    "ref": company["ref"],
                    "headline": "기업 지표 검토",
                    "summary": "승인금액 감소가 관측됩니다.",
                    "observations": ["전월 대비 승인금액이 감소했습니다."],
                    "recommended_actions": ["월별 거래 추이를 확인하세요."],
                    "limitations": "외부 원인은 분석하지 않았습니다.",
                }
                for company in payload["companies"]
            ]
        }

    result = generate_insights(data, object(), "test-model", call=fake_call)
    assert len(calls) == 2
    assert "merchants" not in result
    assert "openai_portfolio_insight" in result
    assert result["openai_metadata"]["status"] == "success"
    assert result["openai_metadata"]["scope"] == "enterprise_only"
    serialized = json.dumps(result, ensure_ascii=False)
