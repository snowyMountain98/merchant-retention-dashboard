"""Search Naver News for month-specific sales-change evidence for enterprise 1999 only.

This module works on aggregated enterprise-month figures only. It never reads the
source workbook and never sends masked enterprise or merchant identifiers to Naver.
Only the configured exception (enterprise ID 1999, known by the owner as Starbucks)
uses a public search query.
"""
from __future__ import annotations

import argparse
import calendar
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
SEARCH_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
DISPLAY_COUNT = 10
MAX_EVIDENCE_PER_MONTH = 5


class NaverSearchError(RuntimeError):
    """Safe error message without request headers or secret values."""


def _norm_id(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\.0+$", "", text) if re.fullmatch(r"\d+\.0+", text) else text


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _iso_pub_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
        return parsed.isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError):
        return _clean_text(value) or None


def _previous_month(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    if number == 1:
        return f"{year - 1}-12"
    return f"{year}-{number - 1:02d}"


def build_queries(month: str) -> list[str]:
    """Build two month-specific queries only for the 1999 enterprise exception."""
    year, month_number = month.split("-")
    month_number_int = int(month_number)
    return [
        f"{SEARCH_SUBJECT} {year}년 {month_number_int}월 매출 결제액 감소",
        f"{SEARCH_SUBJECT} {year}년 {month_number_int}월 논란 이슈 행사",
    ]


def search_naver_news(
    query: str,
    client_id: str,
    client_secret: str,
    sort: str = "sim",
    display: int = DISPLAY_COUNT,
) -> list[dict[str, Any]]:
    """Call Naver Search API news endpoint and normalize small source snippets."""
    params = urllib.parse.urlencode({"query": query, "display": display, "start": 1, "sort": sort})
    request = urllib.request.Request(f"{SEARCH_ENDPOINT}?{params}", method="GET")
    request.add_header("X-Naver-Client-Id", client_id)
    request.add_header("X-Naver-Client-Secret", client_secret)
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Print only Naver's structured error code/message to the Actions log.
        # Never print request headers, query parameters, or credential values.
        diagnostic_parts: list[str] = []
        try:
            raw_error = exc.read(4096).decode("utf-8", errors="replace")
            error_payload = json.loads(raw_error)
            if isinstance(error_payload, dict):
                error_code = str(error_payload.get("errorCode") or "").strip()
                error_message = _clean_text(error_payload.get("errorMessage") or "")[:300]
                for secret in (client_id, client_secret):
                    if secret:
                        error_message = error_message.replace(secret, "[REDACTED]")
                if error_code:
                    diagnostic_parts.append(f"code={error_code}")
                if error_message:
                    diagnostic_parts.append(f"message={error_message}")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass

        if diagnostic_parts:
            print(f"Naver API diagnostic | HTTP {exc.code} | {'; '.join(diagnostic_parts)}")
        else:
            print(f"Naver API diagnostic | HTTP {exc.code} | response had no readable error details")

        # Keep the persisted error generic; detailed diagnostics are only in workflow logs.
        raise NaverSearchError(f"Naver News API HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise NaverSearchError("Naver News API network error") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NaverSearchError("Naver News API returned invalid JSON") from None

    items = payload.get("items", []) if isinstance(payload, dict) else []
    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = str(item.get("originallink") or item.get("link") or "").strip()
        if not link.startswith(("https://", "http://")):
            continue
        results.append({
            "title": _clean_text(item.get("title")),
            "snippet": _clean_text(item.get("description"))[:350],
            "published_at": _iso_pub_date(item.get("pubDate")),
            "url": link,
            "query": query,
            "source": "NAVER News Search API",
        })
    return results


def _monthly_changes(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return month-to-exact-prior-calendar-month changes; don't compare across gaps."""
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
    search_fn: Callable[..., list[dict[str, Any]]] = search_naver_news,
) -> dict[str, Any]:
    """Attach month-by-month evidence to enterprise 1999, and only that enterprise."""
    target = None
    for enterprise in data.get("enterprises", []):
        if _norm_id(enterprise.get("enterprise_id")) == TARGET_ENTERPRISE_ID:
            target = enterprise
        else:
            # Guarantee search evidence cannot accidentally propagate to masked companies.
            enterprise.pop("monthly_change_evidence", None)

    if target is None:
        data["naver_search_metadata"] = {
            "provider": "NAVER Search API / News",
            "status": "target_enterprise_not_found",
            "target_enterprise_id": TARGET_ENTERPRISE_ID,
            "searched_enterprise_count": 0,
        }
        return data

    credentials_ready = bool((client_id or "").strip() and (client_secret or "").strip())
    records = _monthly_changes(target.get("monthly_sales", []))
    months: list[dict[str, Any]] = []
    for change in records:
        record = {
            **change,
            "search_subject": SEARCH_SUBJECT,
            "queries": [],
            "results": [],
            "status": "unchanged" if change["direction"] == "UNCHANGED" else "pending",
            "error": None,
        }
        if change["direction"] == "UNCHANGED":
            months.append(record)
            continue
        if not credentials_ready:
            record["status"] = "credentials_missing"
            months.append(record)
            continue

        query_results: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, query in enumerate(build_queries(change["month"])):
            record["queries"].append(query)
            try:
                query_results.extend(search_fn(
                    query,
                    (client_id or "").strip(),
                    (client_secret or "").strip(),
                    sort="sim" if index == 0 else "date",
                ))
            except NaverSearchError as exc:
                errors.append(str(exc))
            except Exception:
                errors.append("Naver News API unexpected error")

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for result in query_results:
            url = str(result.get("url", "")).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(result)
        for index, result in enumerate(unique[:MAX_EVIDENCE_PER_MONTH], start=1):
            result["evidence_id"] = f"{change['month']}-E{index:02d}"
        record["results"] = unique[:MAX_EVIDENCE_PER_MONTH]
        if record["results"] and errors:
            record["status"] = "partial"
        elif record["results"]:
            record["status"] = "found"
        elif errors:
            record["status"] = "api_error"
            record["error"] = "; ".join(dict.fromkeys(errors))
        else:
            record["status"] = "no_results"
        months.append(record)

    if not credentials_ready:
        overall_status = "credentials_missing"
    elif any(m["status"] in ("found", "partial") for m in months):
        overall_status = "completed"
    elif any(m["status"] == "api_error" for m in months):
        overall_status = "api_error"
    else:
        overall_status = "no_results"

    target["monthly_change_evidence"] = {
        "provider": "NAVER Search API / News",
        "search_subject": SEARCH_SUBJECT,
        "status": overall_status,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "policy": "월별 승인금액 변화와 관련된 공개 뉴스 검색 결과입니다. 검색 결과는 원인에 대한 정황 증거이며 인과관계를 입증하지 않습니다.",
        "months": months,
    }
    data["naver_search_metadata"] = {
        "provider": "NAVER Search API / News",
        "status": overall_status,
        "target_enterprise_id": TARGET_ENTERPRISE_ID,
        "searched_enterprise_count": 1 if credentials_ready else 0,
        "query_months": sum(1 for m in months if m.get("queries")),
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Search monthly sales-change evidence for enterprise 1999 only")
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
        "Naver monthly evidence | target=1999 | "
        f"status={metadata.get('status')} | query_months={metadata.get('query_months', 0)}"
    )
    if metadata.get("status") == "credentials_missing":
        print("Naver evidence was not searched: add NAVER_CLIENT_ID and NAVER_CLIENT_SECRET as GitHub Actions secrets.")


if __name__ == "__main__":
    main()
