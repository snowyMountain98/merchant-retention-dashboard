from src.analysis.generate_naver_evidence import attach_naver_evidence, build_queries


def sample_data():
    return {
        "enterprises": [
            {
                "enterprise_id": "MASKED-A",
                "enterprise_name": "가림기업A",
                "monthly_sales": [
                    {"month": "2026-05", "approval_amount": 1000},
                    {"month": "2026-06", "approval_amount": 500},
                ],
            },
            {
                "enterprise_id": "1999",
                "enterprise_name": "마스킹명",
                "monthly_sales": [
                    {"month": "2026-05", "approval_amount": 1000},
                    {"month": "2026-06", "approval_amount": 800},
                    {"month": "2026-07", "approval_amount": 900},
                ],
            },
        ],
        "merchants": [{"merchant_id": "do-not-send-to-search"}],
    }


def test_only_enterprise_1999_gets_naver_search_calls_and_evidence():
    data = sample_data()
    calls = []

    def fake_search(query, client_id, client_secret, sort="sim"):
        calls.append((query, client_id, client_secret, sort))
        return [{
            "title": "스타벅스 월별 결제액 관련 기사",
            "snippet": "월별 결제액 변화와 관련한 공개 보도 요약",
            "published_at": "2026-07-04T23:15+09:00",
            "url": "https://news.example/article",
            "query": query,
            "source": "NAVER News Search API",
        }]

    result = attach_naver_evidence(data, "client-id", "client-secret", search_fn=fake_search)
    assert len(calls) == 4  # Two queries each for June and July, none for the masked enterprise.
    assert all("스타벅스" in query for query, *_ in calls)
    assert all("MASKED-A" not in query and "가림기업A" not in query for query, *_ in calls)
    other = next(e for e in result["enterprises"] if e["enterprise_id"] == "MASKED-A")
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    assert "monthly_change_evidence" not in other
    assert target["monthly_change_evidence"]["status"] == "completed"
    months = target["monthly_change_evidence"]["months"]
    assert [m["month"] for m in months] == ["2026-06", "2026-07"]
    assert months[0]["change_rate_pct"] == -20.0
    assert months[1]["change_rate_pct"] == 12.5
    assert months[0]["results"][0]["evidence_id"] == "2026-06-E01"


def test_missing_naver_secrets_does_not_call_api_and_reports_configuration_state():
    data = sample_data()
    calls = []
    result = attach_naver_evidence(data, None, None, search_fn=lambda *args, **kwargs: calls.append(args) or [])
    assert calls == []
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    assert target["monthly_change_evidence"]["status"] == "credentials_missing"
    assert all(m["status"] == "credentials_missing" for m in target["monthly_change_evidence"]["months"])


def test_queries_are_month_specific():
    assert build_queries("2026-06") == [
        "스타벅스 2026년 6월 매출 결제액 감소",
        "스타벅스 2026년 6월 논란 이슈 행사",
    ]
