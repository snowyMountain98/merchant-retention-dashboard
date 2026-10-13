# 가맹점 이탈 방지 대시보드 (MVP)

정산 집계 데이터를 이용해 가맹점별 거래 추이와 이탈 위험 신호를 살펴보는 초기 프로젝트입니다. 현재 포함된 CSV/JSON은 모두 가상의 시연 데이터입니다.

## 보안 주의사항

- 실제 정산 파일, 기업명·가맹점명·식별번호 및 실제 분석 결과를 저장소에 커밋하지 마세요.
- 이 저장소가 Public이면 실제 정산 데이터가 절대 올라가면 안 됩니다. 공개 저장소는 샘플/가상 데이터와 코드만 포함하세요.
- 비공개 저장소도 회사의 데이터 반출 및 외부 처리 승인을 대신하지 않습니다.
- 승인금액·취소금액·부분취소금액을 임의로 순매출/정산금액으로 정의하지 마세요. 업무 산정 기준을 데이터 소유 부서에 확인해야 합니다.
- 현재 위험 점수는 개발용 규칙 기반 휴리스틱이며 검증된 이탈 확률이 아닙니다.

## 로컬 실행

Python 3.12 권장.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m http.server 8000
```

브라우저에서 `http://localhost:8000`을 엽니다. 정적 화면은 `public-data/sample_summary.json`의 합성 데이터만 사용합니다.

## 엑셀 검증 (승인된 로컬 환경에서만)

원본 파일은 저장소 밖의 승인된 경로에 두고 실행합니다.

```bash
python -m src.analysis.validate_data --input "C:/approved-path/settlement.xlsx"
```

## 초기 컬럼 가정

`config/column_mapping.example.json`은 대화에서 확인한 컬럼명을 토대로 한 예시입니다. 실제 엑셀의 시트명, 컬럼 정의, 날짜 포맷 및 금액 단위는 데이터 소유 부서와 확인한 뒤 사용하세요.

## 배포

GitHub Pages에는 합성 데이터만 배포하세요. 실제 가맹점별 위험 결과와 관리 이력에는 승인된 내부 웹 환경, 인증, 접근통제, 감사로그 및 중앙 저장소가 필요합니다.
