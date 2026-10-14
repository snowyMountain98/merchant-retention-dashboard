"""Generate OpenAI explanations for precomputed merchant-retention metrics.

The OpenAI API receives only aggregate numeric metrics and temporary references.
Enterprise IDs, merchant IDs, company names, merchant names, and source workbook
rows are deliberately excluded from prompts. The source workbook is not read or
modified by this module; it only enriches the analysis JSON generated earlier.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_MODEL = "gpt-5.6-luna"
COMPANY_BATCH_SIZE = 20
MERCHANT_BATCH_SIZE = 25
MAX_RETRIES_FOR_EMPTY_OUTPUT = 1

PORTFOLIO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
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
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
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

MERCHANT_BATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ref": {"type": "string"},
                    "summary": {"type": "string"},
                    "signals": {"type": "array", "items": {"type": "string"}},
                    "recommended_actions": {"type": "array", "items": {"type": "string"}},
                    "contact_script": {"type": "string"},
                    "limitations": {"type": "string"},
                },
                "required": ["ref", "summary", "signals", "recommended_actions", "contact_script", "limitations"],
            },
        }
    },
    "required": ["items"],
}


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _history(rows: Any, limit: int = 6) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        result.append({
            "month": str(row.get("month", "")),
            "approval_amount": _number(row.get("approval_amount")),
            "approval_count": _number(row.get("approval_count")),
            "cancel_amount": _number(row.get("cancel_amount")),
            "cancel_count": _number(row.get("cancel_count")),
            "partial_cancel_amount": _number(row.get("partial_cancel_amount")),
            "partial_cancel_count": _number(row.get("partial_cancel_count")),
        })
    return result


def build_portfolio_payload(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    enterprises = data.get("enterprises", [])
    merchants = data.get("merchants", [])
    merchant_map: dict[str, list[dict[str, Any]]] = {}
    for merchant in merchants:
        merchant_map.setdefault(str(merchant.get("enterprise_id", "")), []).append(merchant)

    company_rows = []
    for index, company in enumerate(enterprises, start=1):
        company_merchants = merchant_map.get(str(company.get("enterprise_id", "")), [])
        top = sorted(
            [m for m in company_merchants if m.get("risk_score") is not None],
            key=lambda m: float(m.get("risk_score") or 0), reverse=True,
        )[:3]
        company_rows.append({
            "ref": f"company_{index:04d}",
            "risk_level": company.get("risk_level"),
            "risk_score": _number(company.get("risk_score")),
            "merchant_count": _number(company.get("merchant_count")),
            "high_risk_count": _number(company.get("high_risk_count")),
            "medium_risk_count": _number(company.get("medium_risk_count")),
            "unknown_count": _number(company.get("unknown_count")),
            "current_approval_amount": _number(company.get("current_approval_amount")),
            "previous_approval_amount": _number(company.get("previous_approval_amount")),
            "sales_change_rate": _number(company.get("sales_change_rate")),
            "transaction_change_rate": _number(company.get("transaction_change_rate")),
            "monthly_sales": _history(company.get("monthly_sales"), limit=6),
            "top_merchant_patterns": [
                {
                    "risk_level": m.get("risk_level"),
                    "risk_score": _number(m.get("risk_score")),
                    "sales_change_rate": _number(m.get("sales_change_rate")),
                    "transaction_change_rate": _number(m.get("transaction_change_rate")),
                    "inactive_days": _number(m.get("inactive_days")),
                    "risk_reasons": [str(x) for x in (m.get("risk_reasons") or [])[:5]],
                }
                for m in top
            ],
        })

    return {
        "analysis_month": data.get("analysis_month"),
        "as_of_date": data.get("as_of_date"),
        "portfolio_metrics": {
            "enterprise_count": _number(summary.get("enterprise_count")),
            "merchant_count": _number(summary.get("merchant_count")),
            "high_risk_enterprise_count": _number(summary.get("high_risk_enterprise_count")),
            "medium_risk_enterprise_count": _number(summary.get("medium_risk_enterprise_count")),
            "high_risk_count": _number(summary.get("high_risk_count")),
            "medium_risk_count": _number(summary.get("medium_risk_count")),
            "unknown_count": _number(summary.get("unknown_count")),
            "monthly_sales": _history(data.get("monthly_sales"), limit=6),
        },
        "companies": company_rows,
    }


def build_company_payloads(data: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], dict[str, str]]:
    merchants_by_enterprise: dict[str, list[dict[str, Any]]] = {}
    for merchant in data.get("merchants", []):
        merchants_by_enterprise.setdefault(str(merchant.get("enterprise_id", "")), []).append(merchant)

    payloads: list[dict[str, Any]] = []
    ref_to_id: dict[str, str] = {}
    for index, company in enumerate(data.get("enterprises", []), start=1):
        ref = f"company_{index:04d}"
        ref_to_id[ref] = str(company.get("enterprise_id", ""))
        company_merchants = merchants_by_enterprise.get(str(company.get("enterprise_id", "")), [])
        top = sorted(
            [m for m in company_merchants if m.get("risk_score") is not None],
            key=lambda m: float(m.get("risk_score") or 0), reverse=True,
        )[:5]
        payloads.append({
            "ref": ref,
            "metrics": {
                "risk_level": company.get("risk_level"),
                "risk_score": _number(company.get("risk_score")),
                "merchant_count": _number(company.get("merchant_count")),
                "high_risk_count": _number(company.get("high_risk_count")),
                "medium_risk_count": _number(company.get("medium_risk_count")),
                "low_risk_count": _number(company.get("low_risk_count")),
                "unknown_count": _number(company.get("unknown_count")),
                "current_approval_amount": _number(company.get("current_approval_amount")),
                "previous_approval_amount": _number(company.get("previous_approval_amount")),
                "sales_change_rate": _number(company.get("sales_change_rate")),
                "transaction_change_rate": _number(company.get("transaction_change_rate")),
                "monthly_sales": _history(company.get("monthly_sales"), limit=6),
            },
            "priority_merchant_patterns": [
                {
                    "risk_level": m.get("risk_level"),
                    "risk_score": _number(m.get("risk_score")),
                    "sales_change_rate": _number(m.get("sales_change_rate")),
                    "transaction_change_rate": _number(m.get("transaction_change_rate")),
                    "inactive_days": _number(m.get("inactive_days")),
                    "risk_reasons": [str(x) for x in (m.get("risk_reasons") or [])[:5]],
                }
                for m in top
            ],
        })
    return [payloads[i:i + COMPANY_BATCH_SIZE] for i in range(0, len(payloads), COMPANY_BATCH_SIZE)], ref_to_id


def build_merchant_payloads(data: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], dict[str, tuple[str, str]]]:
    payloads: list[dict[str, Any]] = []
    ref_to_key: dict[str, tuple[str, str]] = {}
    for index, merchant in enumerate(data.get("merchants", []), start=1):
        ref = f"merchant_{index:04d}"
        ref_to_key[ref] = (str(merchant.get("enterprise_id", "")), str(merchant.get("merchant_id", "")))
        payloads.append({
            "ref": ref,
            "metrics": {
                "risk_level": merchant.get("risk_level"),
                "risk_score": _number(merchant.get("risk_score")),
                "data_quality": merchant.get("data_quality"),
                "sales_change_rate": _number(merchant.get("sales_change_rate")),
                "transaction_change_rate": _number(merchant.get("transaction_change_rate")),
                "sales_below_3m_pct": _number(merchant.get("sales_below_3m_pct")),
                "transactions_below_3m_pct": _number(merchant.get("transactions_below_3m_pct")),
                "inactive_days": _number(merchant.get("inactive_days")),
                "last_transaction_date": merchant.get("last_transaction_date"),
                "current_approval_amount": _number(merchant.get("current_approval_amount")),
                "current_approval_count": _number(merchant.get("current_approval_count")),
                "risk_reasons": [str(x) for x in (merchant.get("risk_reasons") or [])[:7]],
                "monthly_sales": _history(merchant.get("monthly_sales"), limit=6),
            },
        })
    return [payloads[i:i + MERCHANT_BATCH_SIZE] for i in range(0, len(payloads), MERCHANT_BATCH_SIZE)], ref_to_key


def _format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "name": name, "strict": True, "schema": schema}


def _response_diagnostic(response: Any) -> str:
    """Summarize response metadata without logging prompts or response content."""
    status = getattr(response, "status", None) or "unknown"
    incomplete = getattr(response, "incomplete_details", None)
    if incomplete is not None:
        if hasattr(incomplete, "model_dump"):
            incomplete = incomplete.model_dump(exclude_none=True)
        elif not isinstance(incomplete, (str, int, float, bool, dict, list)):
            incomplete = str(incomplete)
    output_types = []
    refusal_seen = False
    for item in (getattr(response, "output", None) or []):
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else "unknown")
        content = getattr(item, "content", None) or (item.get("content", []) if isinstance(item, dict) else [])
        content_types = []
        for block in content or []:
            block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else "unknown")
            content_types.append(block_type)
            if block_type == "refusal":
                refusal_seen = True
        output_types.append({"type": item_type, "content_types": content_types})
    usage = getattr(response, "usage", None)
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    safe = {"status": status, "incomplete_details": incomplete, "output_items": output_types,
            "refusal_present": refusal_seen, "output_tokens": output_tokens}
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def call_structured(client: Any, model: str, instructions: str, payload: Any, schema_name: str, schema: dict[str, Any], max_output_tokens: int = 5000) -> dict[str, Any]:
    """Request a strict JSON object. Use low reasoning for short batch summaries.

    When the model returns an incomplete/empty JSON response, retry once with a
    larger output budget. Diagnostics intentionally omit prompts, source data,
    generated text and credentials.
    """
    serialized_input = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    token_budget = max_output_tokens
    last_error = None
    for attempt in range(MAX_RETRIES_FOR_EMPTY_OUTPUT + 1):
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=serialized_input,
            text={"format": _format(schema_name, schema), "verbosity": "low"},
            reasoning={"effort": "low"},
            max_output_tokens=token_budget,
        )
        raw = getattr(response, "output_text", None)
        diagnostic = _response_diagnostic(response)
        if not raw:
            last_error = RuntimeError(f"OpenAI 응답 텍스트가 비어 있습니다 (schema={schema_name}, attempt={attempt + 1}, response={diagnostic}).")
        else:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                last_error = RuntimeError(f"OpenAI JSON 응답을 파싱하지 못했습니다 (schema={schema_name}, chars={len(raw)}, attempt={attempt + 1}, response={diagnostic}).")
            else:
                if not isinstance(value, dict):
                    last_error = RuntimeError(f"OpenAI 응답이 JSON 객체가 아닙니다 (schema={schema_name}, response={diagnostic}).")
                else:
                    return value
        refusal_present = '"refusal_present":true' in str(diagnostic).replace(" ", "")
        if attempt >= MAX_RETRIES_FOR_EMPTY_OUTPUT or refusal_present:
            raise last_error
        token_budget = min(token_budget * 2, 24000)
        print(f"OpenAI 응답 형식/길이 문제로 1회 재시도 | schema={schema_name} | response={diagnostic}")
    raise last_error or RuntimeError(f"OpenAI 응답 생성 실패 (schema={schema_name}).")


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def generate_insights(data: dict[str, Any], client: Any, model: str, call: Callable[..., dict[str, Any]] = call_structured) -> dict[str, Any]:
    """Enrich data in place; API prompts use opaque refs instead of actual identifiers."""
    portfolio_payload = build_portfolio_payload(data)
    portfolio_instructions = (
        "당신은 결제사업자의 가맹점 유지관리 분석가입니다. 제공된 집계 수치만 근거로 한국어 요약을 작성하세요. "
        "인과관계를 증명할 수 없으면 원인을 단정하지 말고 확인할 가설이라고 밝히세요. 데이터에 없는 사실, 혜택, 계약 조건, 원인을 만들지 마세요. "
        "수치 계산을 새로 하지 말고 입력된 수치만 인용하세요. 위험 점수를 이탈 확률이라고 표현하지 마세요. "
        "권고는 담당자가 확인할 수 있는 구체적이고 현실적인 행동이어야 하며, 고객에게 자동으로 연락했다고 주장하지 마세요."
    )
    print("OpenAI 인사이트 요청 | 범위=전체 포트폴리오")
    portfolio = call(client, model, portfolio_instructions, portfolio_payload, "portfolio_insight", PORTFOLIO_SCHEMA, 3000)
    data["openai_portfolio_insight"] = portfolio

    company_batches, company_ref_to_id = build_company_payloads(data)
    company_instructions = (
        "당신은 기업 단위 가맹점 리텐션 분석가입니다. 각 ref마다 별도의 한국어 인사이트를 작성하세요. "
        "정해진 값 외에는 추정하지 말고, 매출 변화와 가맹점 위험 분포를 근거로 요약하세요. "
        "상관관계를 원인으로 단정하지 마세요. 위험 점수는 이탈 확률이 아닙니다. 데이터 부족은 반드시 언급하세요. "
        "recommended_actions는 최대 3개, observations는 최대 3개로 간결하게 작성하세요."
    )
    company_insights: dict[str, Any] = {}
    for batch_index, batch in enumerate(company_batches, start=1):
        print(f"OpenAI 인사이트 요청 | 범위=기업 | batch={batch_index}/{len(company_batches)} | items={len(batch)}")
        try:
            result = call(client, model, company_instructions, {"companies": batch}, "company_insights", COMPANY_BATCH_SCHEMA, 7000)
        except Exception as exc:
            raise RuntimeError(f"기업 인사이트 배치 {batch_index}/{len(company_batches)} 실패: {exc}") from exc
        for item in result.get("items", []):
            ref = item.get("ref")
            if ref in company_ref_to_id:
                company_insights[company_ref_to_id[ref]] = {k: v for k, v in item.items() if k != "ref"}

    for company in data.get("enterprises", []):
        company_id = str(company.get("enterprise_id", ""))
        insight = company_insights.get(company_id)
        if not insight:
            raise RuntimeError("OpenAI 응답에서 일부 기업의 AI 인사이트가 누락되었습니다. 해당 batch 응답의 ref를 확인하세요.")
        company["openai_insight"] = insight

    merchant_batches, merchant_ref_to_key = build_merchant_payloads(data)
    merchant_instructions = (
        "당신은 가맹점 유지관리 담당자를 지원하는 분석가입니다. 각 ref에 대해 관측된 수치와 위험 근거만 사용해 한국어 설명을 작성하세요. "
        "원인을 확정하지 말고 가능한 해석과 확인 질문을 구분하세요. 데이터가 불충분하면 이를 명확히 표현하세요. "
        "recommended_actions는 최대 3개, signals는 최대 3개로 작성하고, contact_script는 정중하고 짧은 상담 시작 문구로 작성하세요. "
        "수익 회복이나 이탈 방지를 보장하지 말고, 고객에게 연락을 이미 했다고 쓰지 마세요. 위험 점수는 이탈 확률이 아닙니다."
    )
    merchant_insights: dict[tuple[str, str], Any] = {}
    for batch_index, batch in enumerate(merchant_batches, start=1):
        print(f"OpenAI 인사이트 요청 | 범위=가맹점 | batch={batch_index}/{len(merchant_batches)} | items={len(batch)}")
        try:
            result = call(client, model, merchant_instructions, {"merchants": batch}, "merchant_insights", MERCHANT_BATCH_SCHEMA, 8000)
        except Exception as exc:
            raise RuntimeError(f"가맹점 인사이트 배치 {batch_index}/{len(merchant_batches)} 실패: {exc}") from exc
        for item in result.get("items", []):
            ref = item.get("ref")
            if ref in merchant_ref_to_key:
                merchant_insights[merchant_ref_to_key[ref]] = {k: v for k, v in item.items() if k != "ref"}

    for merchant in data.get("merchants", []):
        key = (str(merchant.get("enterprise_id", "")), str(merchant.get("merchant_id", "")))
        insight = merchant_insights.get(key)
        if not insight:
            raise RuntimeError("OpenAI 응답에서 일부 가맹점의 AI 인사이트가 누락되었습니다. 해당 batch 응답의 ref를 확인하세요.")
        merchant["openai_insight"] = insight

    data["openai_metadata"] = {
        "provider": "OpenAI API",
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "success",
        "prompt_data_policy": "Aggregate metrics only; enterprise IDs, merchant IDs, and names are excluded from model prompts.",
        "note": "AI-generated explanations and suggestions require human review and do not constitute a calibrated churn probability.",
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich merchant analysis JSON with OpenAI insights")
    parser.add_argument("--input", default="public-data/dashboard_summary.json")
    parser.add_argument("--output", default="public-data/dashboard_summary.json")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY가 없습니다. GitHub Settings > Secrets and variables > Actions에 OPENAI_API_KEY를 등록하세요.")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("openai SDK가 설치되지 않았습니다. requirements.txt에 openai 패키지가 있는지 확인하세요.") from exc

    input_path = Path(args.input)
    output_path = Path(args.output)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    client = OpenAI(api_key=api_key, timeout=90.0, max_retries=2)
    try:
        enriched = generate_insights(data, client, model)
    except Exception as exc:
        # Log only error class / HTTP status / API error code, never request data or credentials.
        status = getattr(exc, "status_code", None)
        error_code = getattr(exc, "code", None)
        details = []
        if status is not None:
            details.append(f"HTTP {status}")
        if error_code:
            details.append(f"code={str(error_code)[:80]}")
        suffix = f" [{', '.join(details)}]" if details else ""
        # Our own RuntimeError messages contain only schema/batch/response metadata,
        # never prompts, merchant identifiers, generated text, or API credentials.
        safe_reason = str(exc)[:1200] if isinstance(exc, RuntimeError) else ""
        reason_suffix = f" | detail={safe_reason}" if safe_reason else ""
        request_id = getattr(exc, "request_id", None)
        request_suffix = f" | request_id={str(request_id)[:100]}" if request_id else ""
        raise SystemExit(f"OpenAI 인사이트 생성 실패 ({type(exc).__name__}){suffix}{request_suffix}{reason_suffix}. OPENAI_API_KEY, 모델 접근 권한, 응답 상태 및 API 사용량/결제 설정을 확인하세요.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output_path)
    companies = len(enriched.get("enterprises", []))
    merchants = len(enriched.get("merchants", []))
    print(f"OpenAI 인사이트 생성 완료 | model={model} | companies={companies} | merchants={merchants}")


if __name__ == "__main__":
    main()
