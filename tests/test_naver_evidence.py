import re

from src.analysis.generate_naver_evidence import attach_naver_evidence, build_queries
from src.analysis.generate_openai_insights import _clean_evidence, build_company_payloads, _validate_month_hypotheses


def sample_data():
    return {
        "enterprises": [
            {"enterprise_id": "MASKED-A", "enterprise_name": "masked",
             "monthly_sales": [{"month": "2026-05", "approval_amount": 1000},
                               {"month": "2026-06", "approval_amount": 500}]},
            {"enterprise_id": "1999", "enterprise_name": "masked",
             "monthly_sales": [{"month": "2026-05", "approval_amount": 1000},
                               {"month": "2026-06", "approval_amount": 800},
                               {"month": "2026-07", "approval_amount": 900},
                               {"month": "2026-08", "approval_amount": 940}]}
        ]
    }


def fake_result(query, source_type, *, month_override=None, undated=False):
    match = re.search(r"(20\d{2})년\s*(\d{1,2})월", query)
    assert match, query
    year, month = int(match.group(1)), int(match.group(2))
    if month_override:
        year, month = map(int, month_override.split("-"))
    published_at = None if undated else f"{year}-{month:02d}-04T10:00:00+09:00"
    return {
        "title": f"{source_type} example",
        "snippet": "Sample customer reaction or KB-related issue.",
        "published_at": published_at,
        "url": f"https://example.org/{source_type}/{year}-{month:02d}",
        "source_type": source_type,
        "source": source_type,
        "query": query,
    }


def test_cafe_first_exact_month_only_and_threshold():
    data = sample_data()
    calls = []

    def fake_search(query, client_id, client_secret, source_type="news", sort="date"):
        calls.append((source_type, query))
        # Deliberately return wrong-month result for the first cafe query of June.
        if source_type == "cafe" and "KB국민카드" in query and "2026년 6월" in query:
            return [fake_result(query, source_type, month_override="2026-07")]
        return [fake_result(query, source_type)]

    result = attach_naver_evidence(data, "test-id", "test-secret", search_fn=fake_search)
    assert len(calls) == 16  # June (-20%) and July (+12.5%) only; 8 queries per eligible month.
    target = next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
    other = next(e for e in result["enterprises"] if e["enterprise_id"] == "MASKED-A")
    assert "monthly_change_evidence" not in other
    evidence = target["monthly_change_evidence"]
    june = next(m for m in evidence["months"] if m["month"] == "2026-06")
    july = next(m for m in evidence["months"] if m["month"] == "2026-07")
    august = next(m for m in evidence["months"] if m["month"] == "2026-08")
    assert june["analysis_eligible"] is True
    assert june["change_rate_pct"] == -20.0
    assert all(x["published_at"].startswith("2026-06") for x in june["results"])
    assert all(x["source_type"] in {"cafe", "blog", "news"} for x in june["results"])
    assert june["results"][0]["source_type"] == "cafe"  # cafe-first evidence priority
    assert july["change_rate_pct"] == 12.5
    assert all(x["published_at"].startswith("2026-07") for x in july["results"])
    assert august["status"] == "below_threshold"
    assert august["analysis_eligible"] is False
    assert all("2026년 8월" not in q for _, q in calls)


def test_undated_cafe_results_excluded_and_recorded():
    data = sample_data()

    def fake_search(query, client_id, client_secret, source_type="news", sort="date"):
        if source_type == "cafe":
            return [fake_result(query, source_type, undated=True)]
        return [fake_result(query, source_type)]

    result = attach_naver_evidence(data, "id", "secret", search_fn=fake_search)
    june = next(m for m in next(e for e in result["enterprises"] if e["enterprise_id"] == "1999")
                ["monthly_change_evidence"]["months"] if m["month"] == "2026-06")
    assert june["excluded_undated_results"]["cafe"] == 4
    assert all(r["source_type"] != "cafe" for r in june["results"])


def test_query_sources_include_kb_and_company_issue_topics():
    queries = build_queries("2026-06")
    assert list(queries) == ["cafe", "blog", "news"]
    assert len(queries["cafe"]) > len(queries["news"])
    all_queries = " ".join(q for group in queries.values() for q in group)
    assert "스타벅스" in all_queries
    assert "KB국민카드" in all_queries
    assert "국민은행" in all_queries
    assert "가격" in all_queries or "논란" in all_queries


def test_ai_payload_only_includes_eligible_months_and_preserves_source_type():
    data = sample_data()
    evidence = {
        "months": [
            {"month": "2026-06", "previous_month": "2026-05", "change_rate_pct": -20,
             "direction": "DOWN", "analysis_eligible": True, "status": "found",
             "results": [{"evidence_id": "2026-06-E01", "source_type": "cafe", "source": "Cafe X",
                          "title": "KB Pay benefit", "snippet": "Customer reaction", "published_at": "2026-06-20",
                          "query": "스타벅스 2026년 6월 KB국민카드", "url": "https://example.org"}]},
            {"month": "2026-08", "previous_month": "2026-07", "change_rate_pct": 4.4,
             "direction": "UP", "analysis_eligible": False, "status": "below_threshold", "results": []},
        ]
    }
    data["enterprises"][1]["monthly_change_evidence"] = evidence
    batches, mapping = build_company_payloads(data)
    target = next(item for batch in batches for item in batch if item["ref"] == "company_0002")
    assert len(target["monthly_change_evidence"]) == 1
    src = target["monthly_change_evidence"][0]["results"][0]
    assert src["source_type"] == "cafe"
    assert src["query"].startswith("스타벅스")
    assert _clean_evidence(evidence)[0]["results"][0]["source_type"] == "cafe"
    assert "2026-08" not in _clean_evidence(evidence)[0]["month"]


def test_ai_validation_drops_hypotheses_without_same_month_evidence_ids():
    evidence = {
        "months": [{
            "month": "2026-06", "change_rate_pct": -20, "analysis_eligible": True,
            "results": [{"evidence_id": "2026-06-E01"}],
        }, {
            "month": "2026-08", "change_rate_pct": 4.4, "analysis_eligible": False,
            "results": [{"evidence_id": "2026-08-E01"}],
        }]
    }
    rows = [
        {"month": "2026-06", "change_rate_pct": -20, "direction": "DOWN",
         "candidate_reasons": [{"reason": "grounded", "evidence_ids": ["2026-06-E01"],
                                "confidence": "LOW", "relationship": "indirect"}], "limitations": ""},
        {"month": "2026-08", "change_rate_pct": 4.4, "direction": "UP",
         "candidate_reasons": [{"reason": "ineligible", "evidence_ids": ["2026-08-E01"],
                                "confidence": "HIGH", "relationship": "direct"}], "limitations": ""},
    ]
    validated = _validate_month_hypotheses(rows, evidence)
    assert len(validated) == 1
    assert validated[0]["month"] == "2026-06"
