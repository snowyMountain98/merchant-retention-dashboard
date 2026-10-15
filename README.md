# Enterprise Retention AI Dashboard

기업별 월간 승인금액·승인건수와 이상징후 지표를 집계하고, OpenAI API를 사용해 기업 단위 요약과 권장 조치를 생성하는 대시보드입니다.

## Analysis flow
1. `src/analysis/build_enterprise_dashboard.py` reads the workbook read-only and aggregates records by enterprise and month.
2. The calendar month range in the workbook is expanded to a complete enterprise × month grid. An absent enterprise-month row is interpreted as **0 approval amount and 0 approval count (no payments)**, not missing data.
3. If both current and prior month amounts/counts are zero, the percentage change is 0%. If a previous-month value is zero and the current value is positive, the percentage is mathematically undefined and is kept as `null`; the AI prompt describes it as payments resuming rather than inventing a rate.
4. Enterprise names are kept exactly as provided in the source data. If the source value is masked, that masked name remains the dashboard display name.
5. `src/analysis/generate_openai_insights.py` sends enterprise-level aggregate metrics using opaque company references. Individual merchant rows, merchant names and enterprise names/IDs are not included in the OpenAI prompts.
6. The deployment step removes any merchant records and verifies that display names match original enterprise names.

## Configure
Add `OPENAI_API_KEY` under GitHub repository Settings → Secrets and variables → Actions. `OPENAI_MODEL` is optional; default is `gpt-5.6-luna`.

## Run locally
```bash
python -m pip install -r requirements.txt
python -m pytest -q
python src/analysis/build_enterprise_dashboard.py --input "스타플랫폼거래집계.xlsx" --output public-data/dashboard_summary.json
python src/analysis/generate_openai_insights.py --input public-data/dashboard_summary.json --output public-data/dashboard_summary.json
```

The dashboard does not perform public web search or external-issue analysis. AI output interprets aggregated transaction metrics; it is not proof of causation or a probability of customer churn.
