"""Collect month-specific NAVER Cafe reactions first, then blog and news context.

Search is restricted to enterprise 1999 (Starbucks), and runs only when the
month-over-month approval-amount change has an absolute value >= 10%.
Because NAVER Cafe Search API documents no publication-date field, undated cafe
results are deliberately excluded to preserve the strict same-calendar-month rule.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

TARGET_ENTERPRISE_ID = "1999"
SEARCH_SUBJECT = "스타벅스"
SEARCH_ENDPOINTS = {
    "cafe": "https://naverapihub.apigw.ntruss.com/search/v1/cafearticle",
    "blog": "https://naverapihub.apigw.ntruss.com/search/v1/blog",
    "news": "https://naverapihub.apigw.ntruss.com/search/v1/news",
}
DISPLAY_COUNT = 10
MIN_ABS_CHANGE_PCT = 10.0
MAX_RESULTS_PER_SOURCE = {"cafe": 5, "blog": 3, "news": 2}
SOURCE_ORDER = ("cafe", "blog", "news")


class NaverSearchError(RuntimeError):
    """Safe error message without request headers or secret values."""


def _norm_id(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\.0+$", "", text) if re.fullmatch(r"\d+\.0+", text) else text


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: Any):
    """Parse API HUB postdate (YYYYMMDD), ISO date, or RFC822 news date."""
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_date(value: Any) -> str | None:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return parsed.isoformat(timespec="minutes") if (parsed.hour or parsed.minute or parsed.tzinfo) else parsed.date().isoformat()


def _published_month(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.strftime("%Y-%m") if parsed else None


def _previous_month(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def build_queries(month: str) -> dict[str, list[str]]:
    """Build exact-month brand-issue and KB-related search queries."""
    year, month_number = month.split("-")
    month_label = int(month_number)
    prefix = f"{SEARCH_SUBJECT} {year}년 {month_label}월"
    return {
        # Café reactions are intentionally queried first and receive the most result slots.
        "cafe": [
            f"{prefix} KB국민카드 KB Pay 혜택",
            f"{prefix} 국민은행 이벤트 할인",
            f"{prefix} 이용 후기 불만 가격 품질",
            f"{prefix} 이슈 논란 불매 서비스",
        ],
        "blog": [
            f"{prefix} KB국민카드 국민은행 프로모션",
            f"{prefix} 가격 품질 서비스 이슈 행사",
        ],
        # News is supporting context, not the primary sentiment source.
        "news": [
            f"{prefix} KB국민카드 국민은행 이벤트",
            f"{prefix} 기업 이슈 행사 매출",
        ],
    }


def _safe_error_details(raw_error: bytes, client_id: str, client_secret: str) -> str:
    try:
        payload = json.loads(raw_error.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "response had no readable JSON error details"
    if not isinstance(payload, dict):
        return "response had no structured error details"
    detail = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    code = str(detail.get("errorCode") or "").strip()
    message = _clean_text(detail.get("message") or detail.get("errorMessage") or "")[:300]
    details = _clean_text(detail.get("details") or "")[:300]
    for secret in (client_id, client_secret):
        if secret:
            message = message.replace(secret, "[REDACTED]")
            details = details.replace(secret, "[REDACTED]")
    parts = []
    if code:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")
    if details:
        parts.append(f"details={details}")
    return "; ".join(parts) if parts else "response had no structured error details"


def search_naver_source(
    query: str,
    client_id: str,
    client_secret: str,
    source_type: str,
    sort: str = "date",
    display: int = DISPLAY_COUNT,
) -> list[dict[str, Any]]:
    """Search one NAVER API HUB source and normalize results without inventing dates."""
    if source_type not in SEARCH_ENDPOINTS:
        raise ValueError(f"Unsupported NAVER source_type: {source_type}")
    params = urllib.parse.urlencode({
        "query": query,
        "display": display,
        "start": 1,
        "sort": sort,
        "format": "json",
    })
    request = urllib.request.Request(f"{SEARCH_ENDPOINTS[source_type]}?{params}", method="GET")
    request.add_header("X-NCP-APIGW-API-KEY-ID", client_id)
    request.add_header("X-NCP-APIGW-API-KEY", client_secret)
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        diagnostic = _safe_error_details(exc.read(4096), client_id, client_secret)
        print(f"NAVER API HUB diagnostic | source={source_type} | HTTP {exc.code} | {diagnostic}")
        raise NaverSearchError(f"NAVER API HUB {source_type} HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise NaverSearchError(f"NAVER API HUB {source_type} network error") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NaverSearchError(f"NAVER API HUB {source_type} returned invalid JSON") from None

    items = payload.get("items", []) if isinstance(payload, dict) else []
    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("postdate") or item.get("published_at") or item.get("pubDate")
        if source_type == "news":
            link = str(item.get("originallink") or item.get("link") or "").strip()
            source_name = "NAVER News Search"
        elif source_type == "blog":
            link = str(item.get("link") or "").strip()
            source_name = _clean_text(item.get("bloggername")) or "NAVER Blog"
        else:
            link = str(item.get("link") or "").strip()
            source_name = _clean_text(item.get("cafename")) or "NAVER Cafe"

        if not link.startswith(("https://", "http://")):
            continue
        results.append({
            "title": _clean_text(item.get("title")),
            "snippet": _clean_text(item.get("description"))[:500],
            "published_at": _iso_date(raw_date),
            "url": link,
            "query": query,
            "source_type": source_type,
            "source": source_name,
            "cafe_url": str(item.get("cafeurl") or "") if source_type == "cafe" else "",
            "blogger_url": str(item.get("bloggerlink") or "") if source_type == "blog" else "",
        })
    return results


def _monthly_changes(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_month: dict[str, dict[str, Any]] = {}
    for item in history:
        month = str(item.get("month", ""))[:7]
        if re.fullmatch(r"\d{4}-\d{2}", month):
            by_month[month] = item
    output = []
    for month in sorted(by_month):
        previous_month = _previous_month(month)
        if previous_month not in by_month:
            continue
        current_amount = float(by_month[month].get("approval_amount") or 0)
        previous_amount = float(by_month[previous_month].get("approval_amount") or 0)
        if previous_amount == 0:
            change_pct = None
            direction = "UP_FROM_ZERO" if current_amount > 0 else "UNCHANGED"
        else:
            change_pct = round((current_amount - previous_amount) / previous_amount * 100, 1)
            direction = "DOWN" if change_pct < 0 else "UP" if change_pct > 0 else "UNCHANGED"
        output.append({
            "month": month,
            "previous_month": previous_month,
            "current_approval_amount": round(current_amount, 2),
            "previous_approval_amount": round(previous_amount, 2),
            "change_rate_pct": change_pct,
            "direction": direction,
        })
    return output


def attach_naver_evidence(
    data: dict[str, Any],
    client_id: str | None,
    client_secret: str | None,
    search_fn: Callable[..., list[dict[str, Any]]] = search_naver_source,
) -> dict[str, Any]:
    """Attach same-month community, blog, and secondary news evidence to enterprise 1999 only."""
    target = None
    for enterprise in data.get("enterprises", []):
        if _norm_id(enterprise.get("enterprise_id")) == TARGET_ENTERPRISE_ID:
            target = enterprise
        else:
            enterprise.pop("monthly_change_evidence", None)

    if target is None:
        data["naver_search_metadata"] = {
            "provider": "NAVER API HUB / Cafe + Blog + News Search",
            "status": "target_enterprise_not_found",
            "target_enterprise_id": TARGET_ENTERPRISE_ID,
            "searched_enterprise_count": 0,
        }
        return data

    credentials_ready = bool((client_id or "").strip() and (client_secret or "").strip())
    months: list[dict[str, Any]] = []

    for change in _monthly_changes(target.get("monthly_sales", [])):
        month_record = {
            **change,
            "search_subject": SEARCH_SUBJECT,
            "queries": [],
            "results": [],
            "source_result_counts": {"cafe": 0, "blog": 0, "news": 0},
            "excluded_undated_results": {"cafe": 0, "blog": 0, "news": 0},
            "analysis_eligible": False,
            "status": "unchanged" if change["direction"] == "UNCHANGED" else "pending",
            "skip_reason": None,
            "error": None,
        }
        if change["direction"] == "UNCHANGED":
            month_record["skip_reason"] = "no_monthly_change"
            months.append(month_record)
            continue
        change_pct = change.get("change_rate_pct")
        if change_pct is None:
            month_record["status"] = "change_rate_unavailable"
            month_record["skip_reason"] = "percentage_change_unavailable"
            months.append(month_record)
            continue
        if abs(float(change_pct)) < MIN_ABS_CHANGE_PCT:
            month_record["status"] = "below_threshold"
            month_record["skip_reason"] = f"absolute_change_below_{MIN_ABS_CHANGE_PCT:g}_percent"
            months.append(month_record)
            continue

        month_record["analysis_eligible"] = True
        if not credentials_ready:
            month_record["status"] = "credentials_missing"
            months.append(month_record)
            continue

        all_results: list[dict[str, Any]] = []
        errors: list[str] = []
        for source_type in SOURCE_ORDER:
            source_queries = build_queries(change["month"])[source_type]
            for query in source_queries:
                month_record["queries"].append({"source_type": source_type, "query": query})
                try:
                    found = search_fn(
                        query,
                        (client_id or "").strip(),
                        (client_secret or "").strip(),
                        source_type=source_type,
                        sort="date",
                    )
                    for result in found:
                        if not isinstance(result, dict):
                            continue
                        result = dict(result)
                        result.setdefault("source_type", source_type)
                        all_results.append(result)
                except NaverSearchError as exc:
                    errors.append(str(exc))
                except Exception:
                    errors.append(f"NAVER API HUB {source_type} unexpected error")

        # Strict requirement: only keep sources explicitly dated inside the sales-change month.
        exact_month_results: list[dict[str, Any]] = []
        for result in all_results:
            published_at = result.get("published_at") or result.get("postdate") or result.get("pubDate")
            source_type = str(result.get("source_type") or "news")
            if not _published_month(published_at):
                month_record["excluded_undated_results"][source_type] = month_record["excluded_undated_results"].get(source_type, 0) + 1
                continue
            if _published_month(published_at) != change["month"]:
                continue
            result["published_at"] = _iso_date(published_at)
            exact_month_results.append(result)

        # Source priority controls which sources survive URL deduplication and the per-source cap.
        seen_urls: set[str] = set()
        counts = {"cafe": 0, "blog": 0, "news": 0}
        accepted: list[dict[str, Any]] = []
        for source_type in SOURCE_ORDER:
            candidates = [r for r in exact_month_results if str(r.get("source_type", "news")) == source_type]
            for result in candidates:
                url = str(result.get("url", "")).strip()
                if not url or url in seen_urls or counts[source_type] >= MAX_RESULTS_PER_SOURCE[source_type]:
                    continue
                seen_urls.add(url)
                result["evidence_id"] = f"{change['month']}-E{len(accepted) + 1:02d}"
                accepted.append(result)
                counts[source_type] += 1

        month_record["results"] = accepted
        month_record["source_result_counts"] = counts
        if accepted and errors:
            month_record["status"] = "partial"
        elif accepted:
            month_record["status"] = "found"
        elif errors:
            month_record["status"] = "api_error"
            month_record["error"] = "; ".join(dict.fromkeys(errors))
        elif any(month_record["excluded_undated_results"].values()):
            month_record["status"] = "no_dated_results"
        else:
            month_record["status"] = "no_results"
        months.append(month_record)

    eligible_months = [m for m in months if m.get("analysis_eligible")]
    if eligible_months and not credentials_ready:
        overall_status = "credentials_missing"
    elif any(m["status"] in ("found", "partial") for m in months):
        overall_status = "completed"
    elif any(m["status"] == "api_error" for m in months):
        overall_status = "api_error"
    elif not eligible_months:
        overall_status = "no_eligible_months"
    elif any(m["status"] == "no_dated_results" for m in months):
        overall_status = "no_dated_results"
    else:
        overall_status = "no_results"

    target["monthly_change_evidence"] = {
        "provider": "NAVER API HUB / Cafe + Blog + News Search",
        "search_subject": SEARCH_SUBJECT,
        "status": overall_status,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "minimum_absolute_change_pct": MIN_ABS_CHANGE_PCT,
        "publication_month_filter": "exact_calendar_month; undated items excluded",
        "source_priority": ["cafe", "blog", "news"],
        "source_limits": MAX_RESULTS_PER_SOURCE,
        "analysis_scope": (
            "매출변화율 절대값 10% 이상인 월만 검색합니다. 카페 반응을 우선 수집하고 블로그·뉴스는 보조 자료로 사용합니다. "
            "스타벅스 자체 이슈와 국민은행·KB국민카드·KB Pay 관련 언급을 함께 검색합니다. "
            "게시일이 해당 월로 확인되는 자료만 보관하며, 날짜를 확인할 수 없는 카페글은 제외합니다. "
            "자료는 매출 변화의 정황이지 인과관계를 증명하지 않습니다."
        ),
        "months": months,
    }
    data["naver_search_metadata"] = {
        "provider": "NAVER API HUB / Cafe + Blog + News Search",
        "status": overall_status,
        "target_enterprise_id": TARGET_ENTERPRISE_ID,
        "searched_enterprise_count": 1 if credentials_ready and eligible_months else 0,
        "query_months": sum(1 for m in months if m.get("analysis_eligible")),
        "minimum_absolute_change_pct": MIN_ABS_CHANGE_PCT,
        "source_priority": ["cafe", "blog", "news"],
        "publication_month_filter": "exact_calendar_month; undated items excluded",
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search same-month cafe/blog/news evidence for enterprise 1999 via NAVER API HUB"
    )
    parser.add_argument("--input", default="public-data/dashboard_summary.json")
    parser.add_argument("--output", default="public-data/dashboard_summary.json")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    updated = attach_naver_evidence(
        data,
        os.environ.get("NAVER_CLIENT_ID"),
        os.environ.get("NAVER_CLIENT_SECRET"),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output)
    metadata = updated.get("naver_search_metadata", {})
    print(
        "NAVER community evidence | target=1999 | "
        f"status={metadata.get('status')} | eligible_months={metadata.get('query_months', 0)}"
    )
    if metadata.get("status") == "credentials_missing":
        print("Configure NAVER_CLIENT_ID and NAVER_CLIENT_SECRET from NAVER API HUB.")
    if metadata.get("status") == "no_dated_results":
        print("Search responses contained no usable same-month publication dates; undated items were excluded.")


if __name__ == "__main__":
    main()
