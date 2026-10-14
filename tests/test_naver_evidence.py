import re

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
                    {"month": "2026-08", "approval_amount": 940},
                ],
            },
        ],
        "merchants": [{"merchant_id": "do-not-send-to-search"}],
    }


def fake_result_for_query(query):
    match = re.search(r"(20\d{2})년\s*(\d{1,2})월", query)
    assert match, query
    year, month = int(match.group(1)), int(match.group(2))
    published = f"{year}-{month:02d}-04T23:15:00+09:00"
    return {
        "title": "스타벅스 월별 결제액 관련 기사",
        "snippet": "월별 결제액 변화와 관련한 공개 보도 요약",
        "published_at": published,
        "url": f"https://news.example/article/{year}-{month:02d}",
        "query": query,
        "source": "NAVER API HUB News Search",
    }


def test_only_enterprise_1999_gets_naver_search_calls_and_evidence():
    data = sample_data()
    calls = []

    def fake_search(query, client_id, client_secret, sort="sim"):
        calls.append((query, client_id, client_secret, sort))
        return [fake_result_for_query(query)]

    result = attach_naver_evidence(data, "client-id", "client-secret", search_fn=fake_search)
    assert len(calls) == 4  # Only June and July qualify; two queries per month.
    assert all("스타벅스" in query for query, *_ in calls)
    assert all("MASKED-A" not in query and "가림기업A" not in query for query, *_ in calls)
    other = next(e for e in result["enterprises"] if e["enterprise_id"] == "MASKED-A")
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    assert "monthly_change_evidence" not in other
    assert target["monthly_change_evidence"]["status"] == "completed"
    months = target["monthly_change_evidence"]["months"]
    assert [m["month"] for m in months] == ["2026-06", "2026-07", "2026-08"]
    assert months[0]["change_rate_pct"] == -20.0
    assert months[1]["change_rate_pct"] == 12.5
    assert months[0]["results"][0]["evidence_id"] == "2026-06-E01"
    assert months[2]["status"] == "below_threshold"
    assert months[2]["queries"] == []
    assert months[2]["results"] == []
    assert all(
        article["published_at"][:7] == month["month"]
        for month in months for article in month["results"]
    )


def test_off_month_articles_are_discarded():
    data = sample_data()

    def always_july(query, client_id, client_secret, sort="sim"):
        result = fake_result_for_query("스타벅스 2026년 7월 매출 결제액")
        # Search may return this July result for a June query; it must be excluded there.
        result["url"] = "https://news.example/july-only"
        return [result]

    result = attach_naver_evidence(data, "client-id", "client-secret", search_fn=always_july)
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    by_month = {m["month"]: m for m in target["monthly_change_evidence"]["months"]}
    assert by_month["2026-06"]["results"] == []
    assert by_month["2026-06"]["status"] == "no_results"
    assert by_month["2026-07"]["results"]
    assert all(r["published_at"].startswith("2026-07") for r in by_month["2026-07"]["results"])


def test_missing_naver_secrets_does_not_call_api_and_reports_configuration_state():
    data = sample_data()
    calls = []
    result = attach_naver_evidence(data, None, None, search_fn=lambda *args, **kwargs: calls.append(args) or [])
    assert calls == []
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    months = target["monthly_change_evidence"]["months"]
    assert [m["status"] for m in months] == ["credentials_missing", "credentials_missing", "below_threshold"]


def test_exactly_ten_percent_change_is_eligible():
    data = sample_data()
    target = next(e for e in data["enterprises"] if e["enterprise_id"] == "1999")
    target["monthly_sales"] = [
        {"month": "2026-05", "approval_amount": 1000},
        {"month": "2026-06", "approval_amount": 900},
    ]
    calls = []

    def fake_search(query, client_id, client_secret, sort="sim"):
        calls.append(query)
        return [fake_result_for_query(query)]

    result = attach_naver_evidence(data, "client-id", "client-secret", search_fn=fake_search)
    month = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")["monthly_change_evidence"]["months"][0]
    assert month["change_rate_pct"] == -10.0
    assert month["analysis_eligible"] is True
    assert len(calls) == 2


def test_queries_are_month_specific():
    assert build_queries("2026-06") == [
        "스타벅스 2026년 6월 매출 결제액 감소",
        "스타벅스 2026년 6월 논란 이슈 행사",
    ]
