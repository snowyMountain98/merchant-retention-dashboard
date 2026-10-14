"""Generate enterprise-level OpenAI insights with same-month community evidence.

Only enterprise-level aggregates are sent to OpenAI. Only enterprise ID 1999
receives its permitted Starbucks-related NAVER evidence. The workbook is never
read or modified here; individual merchant records are not included in prompts.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_MODEL = "gpt-5.6-luna"
COMPANY_BATCH_SIZE = 8
MIN_ABS_CHANGE_PCT_FOR_CAUSE_ANALYSIS = 10.0

PORTFOLIO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "focus_areas": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "string"},
    },
    "required": ["headline", "summary", "focus_areas", "recommended_actions", "limitations"],
}

MONTH_REASON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "month": {"type": "string"},
        "change_rate_pct": {"type": ["number", "null"]},
        "direction": {"type": "string", "enum": ["DOWN", "UP", "UP_FROM_ZERO", "UNCHANGED", "UNKNOWN"]},
        "candidate_reasons": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "reason": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                    "relationship": {"type": "string"},
                },
                "required": ["reason", "evidence_ids", "confidence", "relationship"],
            },
        },
        "limitations": {"type": "string"},
    },
    "required": ["month", "change_rate_pct", "direction", "candidate_reasons", "limitations"],
}

COMPANY_BATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "ref": {"type": "string"},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "observations": {"type": "array", "items": {"type": "string"}},
                    "recommended_actions": {"type": "array", "items": {"type": "string"}},
                    "limitations": {"type": "string"},
                    "monthly_cause_hypotheses": {"type": "array", "items": MONTH_REASON_SCHEMA},
                },
                "required": ["ref", "headline", "summary", "observations", "recommended_actions", "limitations", "monthly_cause_hypotheses"],
            },
        }
    },
    "required": ["items"],
}


def _num(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _sales_history(rows: Any, limit: int = 12) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        result.append({
            "month": str(row.get("month", "")),
            "approval_amount": _num(row.get("approval_amount")),
            "approval_count": _num(row.get("approval_count")),
        })
    return result


def _clean_evidence(evidence: Any) -> list[dict[str, Any]]:
    """Pass only >=10%-change months and preserve source labels for grounded analysis."""
    if not isinstance(evidence, dict):
        return []
    result = []
    for month in evidence.get("months", []):
        if not isinstance(month, dict):
            continue
        if month.get("direction") == "UNCHANGED":
            continue
        if month.get("analysis_eligible") is False:
            continue
        change_pct = _num(month.get("change_rate_pct"))
        if change_pct is None or abs(float(change_pct)) < MIN_ABS_CHANGE_PCT_FOR_CAUSE_ANALYSIS:
            continue

        sources = []
        for item in month.get("results", [])[:12]:
            if not isinstance(item, dict):
                continue
            sources.append({
                "evidence_id": str(item.get("evidence_id", "")),
                "source_type": str(item.get("source_type", "unknown")),
                "source_name": str(item.get("source", ""))[:160],
                "title": str(item.get("title", ""))[:250],
                "snippet": str(item.get("snippet", ""))[:500],
                "published_at": str(item.get("published_at", "")),
                "query": str(item.get("query", ""))[:250],
                "url": str(item.get("url", "")),
            })
        result.append({
            "month": str(month.get("month", "")),
            "previous_month": str(month.get("previous_month", "")),
            "change_rate_pct": change_pct,
            "current_approval_amount": _num(month.get("current_approval_amount")),
            "previous_approval_amount": _num(month.get("previous_approval_amount")),
            "direction": str(month.get("direction", "UNKNOWN")),
            "status": str(month.get("status", "unknown")),
            "analysis_eligible": bool(month.get("analysis_eligible", True)),
            "excluded_undated_results": month.get("excluded_undated_results", {}),
            "source_result_counts": month.get("source_result_counts", {}),
            "search_subject": str(month.get("search_subject", "")),
            "results": sources,
        })
    return result


def build_portfolio_payload(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    enterprises = data.get("enterprises", [])
    return {
        "analysis_month": data.get("analysis_month"),
        "as_of_date": data.get("as_of_date"),
        "enterprise_metrics": {
            "enterprise_count": _num(summary.get("enterprise_count")),
            "high_risk_enterprise_count": _num(summary.get("high_risk_enterprise_count")),
            "medium_risk_enterprise_count": _num(summary.get("medium_risk_enterprise_count")),
            "total_current_approval_amount": sum(float(e.get("current_approval_amount") or 0) for e in enterprises),
            "enterprises_with_declining_sales": sum(1 for e in enterprises if e.get("sales_change_rate") is not None and float(e["sales_change_rate"]) < 0),
        },
        "portfolio_monthly_sales": _sales_history(data.get("monthly_sales", []), limit=12),
        "risk_distribution": {
            level: sum(1 for e in enterprises if e.get("risk_level") == level)
            for level in ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
        },
    }


def build_company_payloads(data: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], dict[str, str]]:
    """Return enterprise-only batches; only ID 1999 receives Starbucks community evidence."""
    payloads: list[dict[str, Any]] = []
    ref_to_id: dict[str, str] = {}
    for index, company in enumerate(data.get("enterprises", []), start=1):
        enterprise_id = str(company.get("enterprise_id", "")).strip()
        ref = f"company_{index:04d}"
        ref_to_id[ref] = enterprise_id
        metrics = {
            "risk_level": company.get("risk_level"),
            "risk_score": _num(company.get("risk_score")),
            "current_approval_amount": _num(company.get("current_approval_amount")),
            "previous_approval_amount": _num(company.get("previous_approval_amount")),
            "sales_change_rate": _num(company.get("sales_change_rate")),
            "transaction_change_rate": _num(company.get("transaction_change_rate")),
            "monthly_sales": _sales_history(company.get("monthly_sales"), limit=12),
        }
        item: dict[str, Any] = {"ref": ref, "metrics": metrics}
        if enterprise_id == "1999":
            item["known_business_context"] = "스타벅스"
            item["monthly_change_evidence"] = _clean_evidence(company.get("monthly_change_evidence"))
        payloads.append(item)
    return [payloads[i:i + COMPANY_BATCH_SIZE] for i in range(0, len(payloads), COMPANY_BATCH_SIZE)], ref_to_id


def call_structured(
    client: Any, model: str, instructions: str, payload: Any, schema_name: str,
    schema: dict[str, Any], max_output_tokens: int = 5000,
) -> dict[str, Any]:
    """Use strict JSON output and retry one incomplete/empty output with a larger budget."""
    last_diagnostic = "unknown response state"
    for attempt in range(2):
        token_limit = max_output_tokens * (2 if attempt else 1)
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            reasoning={"effort": "low"},
            text={"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}, "verbosity": "low"},
            max_output_tokens=token_limit,
        )
        raw = getattr(response, "output_text", None)
        if raw:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                last_diagnostic = "output_text_not_valid_json"
            else:
                if isinstance(value, dict):
                    return value
                last_diagnostic = "output_not_json_object"
        else:
            output = getattr(response, "output", None) or []
            if any(
                getattr(content, "type", None) == "refusal"
                for item in output for content in (getattr(item, "content", None) or [])
            ):
                raise RuntimeError(f"OpenAI returned refusal (schema={schema_name})")
            status = getattr(response, "status", "unknown")
            incomplete = getattr(response, "incomplete_details", None)
            last_diagnostic = f"status={status}; incomplete_details={incomplete}"
        if attempt == 0:
            continue
    raise RuntimeError(f"OpenAI structured response unavailable (schema={schema_name}; {last_diagnostic})")


def _monthly_evidence_ids(evidence: Any) -> dict[str, set[str]]:
    valid: dict[str, set[str]] = {}
    if not isinstance(evidence, dict):
        return valid
    for month in evidence.get("months", []):
        if not isinstance(month, dict):
            continue
        if month.get("analysis_eligible") is False:
            continue
        pct = _num(month.get("change_rate_pct"))
        if pct is None or abs(float(pct)) < MIN_ABS_CHANGE_PCT_FOR_CAUSE_ANALYSIS:
            continue
        valid[str(month.get("month", ""))] = {
            str(source.get("evidence_id")) for source in month.get("results", [])
            if isinstance(source, dict) and source.get("evidence_id")
        }
    return valid


def _validate_month_hypotheses(hypotheses: Any, evidence: Any) -> list[dict[str, Any]]:
    """Drop ineligible months and reasons not grounded in valid same-month evidence IDs."""
    if not isinstance(evidence, dict) or not isinstance(hypotheses, list):
        return []
    valid_ids = _monthly_evidence_ids(evidence)
    result = []
    for row in hypotheses:
        if not isinstance(row, dict):
            continue
        month = str(row.get("month", ""))
        if month not in valid_ids:
            continue
        candidates = []
        for candidate in row.get("candidate_reasons", []):
            if not isinstance(candidate, dict):
                continue
            refs = [str(ref) for ref in candidate.get("evidence_ids", []) if str(ref) in valid_ids[month]]
            if not refs:
                continue
            candidates.append({
                "reason": str(candidate.get("reason", ""))[:600],
                "evidence_ids": refs,
                "confidence": candidate.get("confidence") if candidate.get("confidence") in ("LOW", "MEDIUM", "HIGH") else "LOW",
                "relationship": str(candidate.get("relationship", ""))[:600],
            })
        result.append({
            "month": month,
            "change_rate_pct": _num(row.get("change_rate_pct")),
            "direction": str(row.get("direction", "UNKNOWN")),
            "candidate_reasons": candidates,
            "limitations": str(row.get("limitations", ""))[:800],
        })
    return result


def generate_insights(
    data: dict[str, Any], client: Any, model: str,
    call: Callable[..., dict[str, Any]] = call_structured,
) -> dict[str, Any]:
    portfolio_instructions = (
        "당신은 결제사업자의 기업 포트폴리오 분석가입니다. 한국어로 짧은 요약, 집중 영역, 권장 조치를 작성하세요. "
        "입력 집계 수치만 근거로 말하고, 상관관계를 원인으로 단정하거나 입력에 없는 사실을 만들지 마세요. "
        "이상징후 점수는 이탈 확률이 아닙니다. 담당자가 확인할 수 있는 행동을 권고하고 한계를 명시하세요."
    )
    portfolio = call(client, model, portfolio_instructions, build_portfolio_payload(data), "portfolio_insight", PORTFOLIO_SCHEMA, 1800)
    data["openai_portfolio_insight"] = portfolio

    batches, ref_to_id = build_company_payloads(data)
    company_instructions = (
        "당신은 기업 단위 승인금액 변화의 원인을 조사하는 분석가입니다. 한국어로 답하세요. "
        "기업명이나 식별번호를 추정하지 말고 제공한 ref를 그대로 반환하세요. 위험 점수는 이탈 확률이 아닙니다. "
        "권고 조치는 최대 3개, 관측 결과는 최대 3개로 작성하세요. "
        "스타벅스(known_business_context=스타벅스)와 월별 검색 근거가 제공된 회사 1999만 월별 원인 가설을 작성할 수 있습니다. "
        "분석 대상은 스타벅스의 전체 회사 매출이 아니라 입력 데이터의 국민은행 관련 승인금액/거래 변화일 수 있습니다. 전체 브랜드 매출처럼 표현하지 마세요. "
        "매출변화율 절대값이 10% 이상인 달만 원인 분석 대상입니다. 각 가설의 month는 제공된 대상 월과 정확히 일치해야 합니다. "
        "자료 우선순위는 네이버 카페 이용자 반응, 네이버 블로그, 뉴스 보도 순입니다. 카페/블로그의 개별 글은 대표성 있는 설문이 아니므로 여론 전체로 일반화하지 마세요. "
        "스타벅스 자체 이슈(가격, 제품 품질, 서비스, 매장 운영, 불매/논란, 신제품·시즌 행사 등)와 국민은행 연관 이슈(KB국민카드, KB Pay, 국민은행 프로모션·할인·포인트·결제 혜택)를 함께 살피세요. "
        "각 후보 이유에서 국민은행과의 관련성이 직접인지 간접인지 반드시 설명하세요. 직접 관련은 해당 자료에 국민은행/KB국민카드/KB Pay 또는 관련 프로모션·결제 혜택이 명시된 경우입니다. "
        "스타벅스 브랜드 이슈만 있고 KB 언급이 없으면 가능한 간접 영향으로만 서술하고, KB 승인금액 변화와 연결된다는 근거가 약함을 밝히세요. "
        "방향을 반드시 반영하세요: DOWN이면 감소를 설명할 수 있는 근거(부정 반응, 혜택 종료/변경, 결제 불편 등)를 찾고, UP이면 증가를 설명할 수 있는 근거(프로모션, 혜택, 시즌 수요, 긍정 반응 등)를 찾으세요. "
        "이 예시는 분석 항목일 뿐 사실로 가정하지 마세요. 해당 월에 확인된 자료가 지지하지 않으면 이유로 제시하지 마세요. "
        "각 candidate_reasons는 실제 제공된 evidence_id를 최소 1개 포함해야 합니다. 출처, 날짜, 인과관계를 꾸며내지 마세요. "
        "매출 변화 월과 같은 달에 게시된 자료만 제공됩니다. 해당 월 자료가 없거나 KB 관계를 뒷받침할 근거가 없으면 candidate_reasons를 빈 배열로 하고 limitations에 명시하세요. "
        "reason에는 그럴듯한 설명을 간결하게, relationship에는 실제 자료와 KB 승인금액 변화 사이의 연결 및 확신 수준을 적으세요. "
        "다른 기업의 monthly_cause_hypotheses는 항상 빈 배열입니다."
    )
    for batch in batches:
        result = call(client, model, company_instructions, {"companies": batch}, "company_insights", COMPANY_BATCH_SCHEMA, 8000)
        items = result.get("items", [])
        returned = {str(item.get("ref")): item for item in items if isinstance(item, dict) and item.get("ref")}
        for item in batch:
            ref = str(item["ref"])
            enterprise_id = ref_to_id[ref]
            generated = returned.get(ref)
            if generated is None:
                raise RuntimeError(f"OpenAI company response missing ref={ref}")
            target = next((company for company in data.get("enterprises", []) if str(company.get("enterprise_id", "")).strip() == enterprise_id), None)
            if target is None:
                raise RuntimeError(f"Dashboard enterprise mapping failed for ref={ref}")
            insight = {k: generated.get(k) for k in ("headline", "summary", "observations", "recommended_actions", "limitations")}
            if enterprise_id == "1999":
                insight["monthly_cause_hypotheses"] = _validate_month_hypotheses(
                    generated.get("monthly_cause_hypotheses", []), target.get("monthly_change_evidence")
                )
            else:
                insight["monthly_cause_hypotheses"] = []
            target["openai_insight"] = insight

    data.pop("merchants", None)
    summary = data.get("summary", {})
    for key in ("latest_month_observed_count", "high_risk_count", "medium_risk_count", "low_risk_count", "unknown_count"):
        summary.pop(key, None)
    data["openai_metadata"] = {
        "provider": "OpenAI API",
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "success",
        "scope": "enterprise_only",
        "note": (
            "기업 승인금액 변화의 월별 분석. 기업 1999에 한해 같은 달의 카페 반응을 우선, "
            "블로그·뉴스를 보조 근거로 분석합니다. 국민은행 연관성은 직접 언급과 간접 추정을 구분하며 인과관계를 확정하지 않습니다."
        ),
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate enterprise-only OpenAI insights using monthly cafe/blog/news evidence")
    parser.add_argument("--input", default="public-data/dashboard_summary.json")
    parser.add_argument("--output", default="public-data/dashboard_summary.json")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY가 없습니다. GitHub Actions Secrets를 확인하세요.")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("openai SDK가 없습니다. requirements.txt를 확인하세요.") from None

    input_path, output_path = Path(args.input), Path(args.output)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    client = OpenAI(api_key=api_key, timeout=90.0, max_retries=2)
    try:
        enriched = generate_insights(data, client, model)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        code = getattr(exc, "code", None)
        request_id = getattr(exc, "request_id", None)
        diagnostics = [f"HTTP {status}" if status is not None else "", f"code={str(code)[:80]}" if code else "", f"request_id={str(request_id)[:100]}" if request_id else ""]
        diagnostics = [x for x in diagnostics if x]
        own_detail = str(exc)[:800] if isinstance(exc, RuntimeError) else ""
        extra = (" | " + ", ".join(diagnostics)) if diagnostics else ""
        detail = (" | " + own_detail) if own_detail else ""
        raise SystemExit(f"OpenAI 기업 인사이트 생성 실패 ({type(exc).__name__}){extra}{detail}") from None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output_path)
    print(f"OpenAI 기업 인사이트 완료 | model={model} | enterprises={len(enriched.get('enterprises', []))} | scope=enterprise_only")


if __name__ == "__main__":
    main()
