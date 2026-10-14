# AI 가맹점 이탈 징후 대시보드

GitHub Actions가 저장소 루트의 `스타플랫폼거래집계.xlsx`를 읽기 전용으로 처리하고, 가맹점별 월간 분석 결과를 생성한 뒤 GitHub Pages에 배포합니다. 원본 엑셀 파일은 수정하거나 덮어쓰지 않습니다.

## AI 분석 방식

- 비지도 머신러닝 `IsolationForest`를 사용해 가맹점 월별 이력에서 드문 하락·거래 공백 패턴을 탐지합니다.
- 입력 특징: 승인금액 감소율, 승인건수 감소율, 직전 3개월 평균 대비 하락률, 마지막 관측 거래 이후 일수, 취소금액/승인금액 비율, 부분취소 비율, 연속 하락 횟수.
- AI 이상징후 점수는 모델 참조 분포에 대한 상대적 순위(0~100)입니다. **이탈 확률이 아닙니다.** 실제 이탈/해지 여부의 라벨이 없으므로 결과는 위험 신호 후보로 보고 사람이 검토해야 합니다.
- HIGH / MEDIUM 등급은 초기 운영 기준이며, 과거 이탈 결과와 담당자 피드백을 축적해 검증·보정해야 합니다.
- 최신 집계월에 행이 없는 가맹점은 실제 미거래인지 데이터 누락인지 단정하지 않고 `UNKNOWN/확인 필요`로 표시합니다.

## 원본 데이터

- 파일: `스타플랫폼거래집계.xlsx` (저장소 루트)
- 스크립트는 원본을 읽기만 합니다. 마스킹, 컬럼명 변경, 원본 재저장은 수행하지 않습니다.
- 승인금액, 취소금액, 부분취소금액은 각각 별도 지표로 보존합니다. 순매출/정산금액을 임의로 계산하지 않습니다.
- 공개 저장소와 GitHub Pages에는 마스킹 데이터 및 가맹점별 AI 분석 결과가 공개됩니다. 사용자가 공개 가능한 데이터임을 확인한 경우에만 이 구성을 사용하세요.

## GitHub 설정

1. Repository > **Settings > Pages > Build and deployment > Source**를 `GitHub Actions`로 설정합니다.
2. `requirements.txt`, `src/analysis/build_dashboard.py`, `tests/test_ai_scoring.py`, `.github/workflows/analyze.yml`, `index.html`, `assets/js/dashboard.js`를 커밋합니다.
3. **Actions**에서 `AI Merchant Retention Analysis and Deploy`의 성공 여부를 확인합니다.
4. 사이트: https://snowymountain98.github.io/merchant-retention-dashboard/

워크플로는 Pages 산출물용 `site/` 디렉터리만 업로드하며 원본 엑셀 파일을 산출물에 복사하지 않습니다. 다만 저장소 자체가 Public이면 원본 파일은 저장소에서 공개 접근할 수 있습니다.

## 개발 한계

- 현재 모델은 이탈 라벨을 이용해 학습한 분류 모델이 아닙니다. 이탈 확률이나 확정 판정으로 해석하지 마세요.
- 제대로 된 이탈 예측을 원하면 해지일/이탈 여부와 예측 기준일을 포함한 과거 라벨 데이터가 필요합니다. 그 경우 시간 순서에 따른 학습·검증과 정밀도/재현율 평가를 추가합니다.
- 관리 이력 중앙 저장과 사용자 인증은 GitHub Pages만으로 제공되지 않습니다. 여러 담당자가 실제 이력을 관리하려면 승인된 백엔드/DB를 연결해야 합니다.


## 기업 → 가맹점 화면 구성

- `index.html`: 기업관리번호별 요약 화면. 기업명은 유일하지 않을 수 있어 식별 및 선택 키는 `기업관리번호`로 사용합니다.
- `company.html?enterprise_id=...`: 선택한 기업의 소속 가맹점 목록, 위험 등급 분포 및 기업 월별 승인금액 추이.
- `merchant-detail.html?enterprise_id=...&merchant_id=...`: 가맹점의 AI 이상징후 점수, 위험 근거와 최근 12개월 월별 승인/취소/부분취소 실적.
- `public-data/dashboard_summary.json`: Actions가 원본 엑셀을 읽어 생성하며 기업별 집계와 가맹점별 결과/월별 이력을 포함합니다.

## 화면 업데이트 반영

웹 UI 파일을 수정한 뒤 `.github/workflows/analyze.yml`이 `company.html`, `merchant-detail.html`, `index.html`, `assets/` 및 생성한 분석 JSON을 Pages 산출물에 복사하도록 되어 있어야 합니다. 원본 Excel은 사이트 산출물에 복사하지 않습니다.

## AI 해석 범위

현재 모델은 Isolation Forest 기반 비지도 이상징후 탐지입니다. 점수는 학습된 거래 패턴 대비 상대 이상치 점수이며, 검증된 이탈 확률이 아닙니다. 실제 해지/이탈 라벨이 확보되면 별도의 지도학습 및 시간 기준 검증을 설계할 수 있습니다.

## OpenAI API 인사이트

- 현재 배치 분석은 포트폴리오 1회, 기업별 20개 묶음, 가맹점별 25개 묶음으로 OpenAI API를 호출합니다. 약 103개 기업과 375개 가맹점 기준으로 총 22회 호출합니다. `main` 브랜치에 변경을 커밋할 때마다 분석과 API 사용량이 발생할 수 있습니다.
- 기존 Isolation Forest 이상징후 점수는 유지합니다. OpenAI는 별도로 기업별 브리핑, 가맹점별 수치 설명, 확인할 신호, 권고 조치 및 상담 문구 초안을 생성합니다.
- 저장소의 `Settings > Secrets and variables > Actions`에서 `OPENAI_API_KEY`를 등록해야 합니다. 키를 코드, JSON, HTML, Pages 산출물에 넣지 마세요.
- 선택적으로 `OPENAI_MODEL` Actions Variable을 설정할 수 있습니다. 기본 모델은 비용 효율적인 `gpt-5.6-luna`입니다. 모델 카탈로그와 요금은 바뀔 수 있으니 필요 시 `OPENAI_MODEL` Actions Variable로 조정하세요.
- OpenAI 요청에는 기업관리번호, 기업명, 가맹점 번호, 가맹점명 대신 임시 참조값과 최소한의 집계 지표만 보냅니다. 실제 데이터 처리 승인을 별도로 확인하세요.
- AI 인사이트는 자동 관리 조치를 실행하지 않습니다. 담당자가 내용을 검토해야 하며 이탈 확률 또는 확정 판정으로 사용해서는 안 됩니다.
- OpenAI API 호출 실패 시 분석·배포 단계가 실패하도록 되어 있어 이전/불완전한 결과를 새 결과인 것처럼 배포하지 않습니다. Actions 로그에서 키 이름, 모델 접근 권한, API 사용량/결제 상태를 확인하세요. 오류 로그에는 요청 데이터나 키를 기록하지 않습니다.
