# Enterprise Retention AI Dashboard

기업별 월간 승인금액·승인건수와 이상징후 지표를 집계하고, OpenAI API를 사용해 기업 단위 요약과 권장 조치를 생성하는 대시보드입니다.

## Analysis flow
1. `src/analysis/build_enterprise_dashboard.py` reads the workbook read-only and aggregates records by enterprise and month.
2. Enterprise names are kept exactly as provided in the source data. If the source value is masked, that masked name remains the dashboard display name.
3. `src/analysis/generate_openai_insights.py` sends enterprise-level aggregate metrics using opaque company references. Individual merchant rows, merchant names and enterprise names/IDs are not included in the OpenAI prompts.
4. The deployment step removes any merchant records and verifies that display names match the original enterprise names.

## Configure
Add `OPENAI_API_KEY` under GitHub repository Settings → Secrets and variables → Actions. `OPENAI_MODEL` is optional; default is `gpt-5.6-luna`.

## Run locally
```bash
python -m pip install -r requirements.txt
python -m pytest -q
python src/analysis/build_enterprise_dashboard.py --input "스타플랫폼거래집계.xlsx" --output public-data/dashboard_summary.json
python src/analysis/generate_openai_insights.py --input public-data/dashboard_summary.json --output public-data/dashboard_summary.json
```

The dashboard does not perform public web search or external-issue analysis. AI output is an interpretation of aggregated transaction metrics, not proof of a causal explanation or a probability of customer churn.
