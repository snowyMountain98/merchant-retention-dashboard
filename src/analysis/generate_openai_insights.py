"""Generate enterprise-level OpenAI insights without external search evidence.

Only enterprise-level aggregates are sent to OpenAI. Enterprise names and IDs are
replaced with opaque references in the prompts. The workbook is never read or
modified here; individual merchant records are not included in prompts.
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
                },
                "required": ["ref", "headline", "summary", "observations", "recommended_actions", "limitations"],
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


def build_portfolio_payload(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    enterprises = data.get("enterprises", [])
    status_counts = {
        status: sum(1 for e in enterprises if e.get("transaction_status") == status)
        for status in (
            "TRADING", "DECLINING", "RECENTLY_INACTIVE", "LONG_INACTIVE",
            "RESTARTED", "FIRST_ACTIVITY", "SHORT_HISTORY", "NO_ACTIVITY",
        )
    }
    return {
        "analysis_month": data.get("analysis_month"),
        "as_of_date": data.get("as_of_date"),
        "enterprise_metrics": {
            "enterprise_count": _num(summary.get("enterprise_count")),
            "currently_trading_enterprise_count": sum(
                1 for e in enterprises
                if float(e.get("current_approval_amount") or 0) > 0 or float(e.get("current_approval_count") or 0) > 0
            ),
            "declining_enterprise_count": status_counts["DECLINING"],
            "recently_inactive_enterprise_count": status_counts["RECENTLY_INACTIVE"],
            "long_inactive_enterprise_count": status_counts["LONG_INACTIVE"],
            "restarted_enterprise_count": status_counts["RESTARTED"],
            "total_current_approval_amount": sum(float(e.get("current_approval_amount") or 0) for e in enterprises),
            "enterprises_with_declining_sales": sum(1 for e in enterprises if e.get("sales_change_rate") is not None and float(e["sales_change_rate"]) < 0),
        },
        "portfolio_monthly_sales": _sales_history(data.get("monthly_sales", []), limit=12),
        "transaction_status_distribution": status_counts,
    }


def build_company_payloads(data: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], dict[str, str]]:
    """Return enterprise-only batches with opaque references and aggregate metrics only."""
    payloads: list[dict[str, Any]] = []
    ref_to_id: dict[str, str] = {}
    for index, company in enumerate(data.get("enterprises", []), start=1):
        enterprise_id = str(company.get("enterprise_id", "")).strip()
        ref = f"company_{index:04d}"
        ref_to_id[ref] = enterprise_id
        metrics = {
            "transaction_status": company.get("transaction_status"),
            "transaction_status_label": company.get("transaction_status_label"),
            "status_reasons": company.get("status_reasons", []),
            "current_approval_amount": _num(company.get("current_approval_amount")),
            "current_approval_count": _num(company.get("current_approval_count")),
            "previous_approval_amount": _num(company.get("previous_approval_amount")),
            "previous_approval_count": _num(company.get("previous_approval_count")),
            "sales_change_rate": _num(company.get("sales_change_rate")),
            "transaction_change_rate": _num(company.get("transaction_change_rate")),
            "last_active_month": company.get("last_active_month"),
            "zero_activity_streak": _num(company.get("zero_activity_streak")),
            "active_month_count_3": _num(company.get("active_month_count_3")),
            "active_month_count_6": _num(company.get("active_month_count_6")),
            "active_month_count_12": _num(company.get("active_month_count_12")),
            "sales_below_3m_pct": _num(company.get("sales_below_3m_pct")),
            "transactions_below_3m_pct": _num(company.get("transactions_below_3m_pct")),
            "monthly_sales": _sales_history(company.get("monthly_sales"), limit=12),
        }
        payloads.append({"ref": ref, "metrics": metrics})
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


def generate_insights(
    data: dict[str, Any], client: Any, model: str,
    call: Callable[..., dict[str, Any]] = call_structured,
) -> dict[str, Any]:
    portfolio_instructions = (
        "당신은 결제사업자의 기업 거래 유지 현황 분석가입니다. 한국어로 짧은 요약, 집중 영역, 권장 조치를 작성하세요. "
        "입력 집계 수치만 근거로 말하고, 상관관계를 원인으로 단정하거나 입력에 없는 사실을 만들지 마세요. "
        "위험 등급과 통계 점수는 사용하지 않습니다. 거래 상태는 승인 실적의 관측 사실을 요약한 것이며 기업 재무 건전성이나 미래 이탈 확률을 뜻하지 않습니다. "
        "장기 미거래·최근 미거래·거래 감소 관찰 상태를 우선 검토 대상으로 구분하고, 실제 수치와 기간을 인용해 행동을 권고하세요. "
        "데이터로 확인할 수 없는 기업의 재무 상태나 향후 거래 가능성을 확정적으로 서술하지 말고 한계를 명시하세요."
    )
    portfolio = call(client, model, portfolio_instructions, build_portfolio_payload(data), "portfolio_insight", PORTFOLIO_SCHEMA, 1800)
    data["openai_portfolio_insight"] = portfolio

    batches, ref_to_id = build_company_payloads(data)
    company_instructions = (
        "당신은 기업 단위 거래 유지 현황 분석가입니다. 한국어로 작성하세요. "
        "기업명이나 식별번호를 추정하지 말고 제공된 ref를 그대로 반환하세요. "
        "입력에 있는 기업 단위 월간 승인금액·승인건수와 거래 상태만 근거로 쓰고 원인을 단정하거나 출처 없는 사실을 만들지 마세요. "
        "기존 위험 등급, 이상점수, 위험 점수, 이탈 확률 표현을 사용하지 마세요. 거래 상태는 관측된 과거/현재 결제 실적이지 미래 예측이 아닙니다. "
        "관측 결과는 최대 3개, 권장 조치는 최대 3개로 작성하세요. "
        "입력에서 승인금액이나 승인건수가 증가하면 해당 증가 지표와 기간을 정확히 설명하고, 감소하면 감소 지표와 기간을 정확히 설명하세요. "
        "월별 승인금액과 승인건수가 모두 0인 경우는 데이터 누락이 아니라 해당 월 결제 없음으로 해석하세요. "
        "전월에 승인 실적이 있었는데 이번 달 금액과 건수가 모두 0이면 전월 대비 100% 감소로 설명하세요. "
        "전월과 이번 달 모두 0이면 연속 미거래 기간을 언급하세요. 전월이 0이고 이번 달에 양의 실적이 발생한 경우 증가율 백분율은 계산 불가하므로 임의의 비율을 만들지 말고 거래 재개/첫 거래 실적으로 설명하세요. "
        "거래 감소 관찰은 입력된 상태와 직전 3개월 평균 대비 수치를 근거로 설명하세요. "
        "승인 거래 데이터만으로 기업 재무 건전성, 지급능력, 폐업 여부, 또는 미래 거래 유지 가능성을 확정하지 마세요. "
        "집계 데이터만으로 외부 원인을 확인할 수 없으면 추측하지 말고 제한사항에 근거 부족을 명시하세요. "
        "다른 기업명, 브랜드명 또는 식별정보를 추정하지 마세요."
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
            target["openai_insight"] = insight

    data.pop("merchants", None)
    summary = data.get("summary", {})
    for key in ("high_risk_enterprise_count", "medium_risk_enterprise_count", "low_risk_enterprise_count", "unknown_enterprise_count"):
        summary.pop(key, None)
    data["openai_metadata"] = {
        "provider": "OpenAI API",
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "success",
        "scope": "enterprise_only",
        "note": "기업 단위 월간 승인금액·승인건수와 규칙 기반 거래 상태만을 사용한 요약입니다. 월별 승인금액과 승인건수가 모두 0이면 결제 없음으로 처리합니다. 외부 검색 결과는 사용하지 않으며, 거래 상태를 재무 건전성 판정이나 미래 이탈 확률로 해석하지 않습니다.",
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate enterprise-only OpenAI insights from monthly transaction activity")
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
