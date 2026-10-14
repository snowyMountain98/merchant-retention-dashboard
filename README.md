# 기업 단위 AI 리텐션 대시보드

본 배포 버전은 개별 가맹점 분석 화면을 제거하고, 기업관리번호별 분석만 표시합니다. 저장소의 정산 엑셀은 Python 스크립트가 읽기 전용으로 사용합니다.

## 대시보드 범위

- 기업별 목록과 위험 등급, 승인금액/건수 변동
- 기업별 OpenAI 진단과 권장 조치
- 기업별 월별 승인금액 추이 및 변동 표
- 기업관리번호 `1999`에 한해 스타벅스 관련 월별 매출 변동을 네이버 뉴스 검색 API로 검색
- 기업관리번호 1999의 검색 결과만 OpenAI에 근거로 제공하여 월별 원인 가능성을 추정
- 원본 엑셀과 개별 가맹점 레코드는 GitHub Pages 배포 산출물에 포함하지 않음

## 데이터 분석 흐름

1. `src/analysis/build_enterprise_dashboard.py`가 XLSX의 원본 컬럼을 읽고 `기업관리번호` + `집계년월`로 월별 집계합니다.
2. 기업 단위 월별 승인금액/승인건수 패턴에 Isolation Forest를 적용합니다.
3. `src/analysis/generate_naver_evidence.py`는 `enterprise_id == "1999"`인 경우에만 네이버 뉴스 검색을 실행합니다. 그 외 기업은 검색하지 않습니다.
4. `src/analysis/generate_openai_insights.py`는 기업별 집계와 1999의 검색 결과만 OpenAI로 전달합니다. merchant records를 OpenAI에 전달하지 않고, 최종 공개 JSON에서도 제거합니다.
5. GitHub Actions는 기업별 화면 파일과 기업 집계 JSON만 GitHub Pages에 배포합니다.

## GitHub Secrets 설정

`Settings > Secrets and variables > Actions`에서 아래를 등록하세요.

- `OPENAI_API_KEY` — OpenAI API Key
- `NAVER_CLIENT_ID` — 네이버 개발자센터에서 발급한 Client ID
- `NAVER_CLIENT_SECRET` — 네이버 개발자센터에서 발급한 Client Secret

네이버 애플리케이션 등록: https://developers.naver.com/apps/#/register
네이버 뉴스 검색 API 공식 가이드: https://developers.naver.com/docs/serviceapi/search/news/news.md

NAVER 키가 없으면 Actions는 이를 로그에 알리고 기업 대시보드는 계속 생성합니다. 이 경우 1999의 월별 근거 영역에는 키 미설정 메시지가 나오므로 두 Secrets를 추가한 뒤 Actions를 다시 실행해야 합니다.

## 배포 설정

Repository `Settings > Pages > Build and deployment > Source`를 `GitHub Actions`로 설정합니다. `.github/workflows/analyze.yml`이 성공하면 사이트는 다음 주소에 게시됩니다.

https://snowymountain98.github.io/merchant-retention-dashboard/

## 검색/AI 결과 해석 주의

- 월별 승인금액 변화는 사용자 데이터에서 계산한 수치입니다.
- 네이버 검색 결과의 기사 제목, 발행일, 링크, 짧은 요약이 근거로 표시됩니다. 검색 결과는 정황 증거이지 인과관계의 증명은 아닙니다.
- OpenAI는 근거에 기반해 가능한 설명을 제안하지만, 매출 변화 원인을 확정하지 않습니다. 근거가 없거나 관련성이 낮으면 가설을 표시하지 않도록 설정했습니다.
- 매출 데이터와 뉴스의 집계 기준은 서로 다를 수 있습니다. 공개 카드 결제 추정액이 내부 승인금액과 일치한다고 간주하지 마세요.
- Isolation Forest 점수는 기업 월별 패턴 대비 상대 이상징후 점수이지 이탈 확률이 아닙니다.
- 검색 결과는 사용자가 확인할 수 있도록 기사 링크와 발행일을 보여줍니다.

## 로컬 테스트(선택)

사용자는 GitHub에서만 배포할 수 있지만, CI가 실행하는 테스트는 다음과 같습니다.

`python -m pytest -q`
