"""Basic validation helpers for local settlement workbooks.
Only read real workbooks in an approved execution environment.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

REQUIRED_COLUMNS = {"스타플랫폼가맹점번호", "집계년월", "집계년월일", "승인건수", "승인금액"}


def validate_dataframe(df: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        return [f"필수 컬럼 누락: {', '.join(missing)}"]
    if df.empty:
        return ["데이터가 비어 있습니다."]
    merchant = df["스타플랫폼가맹점번호"].astype("string").str.strip()
    if merchant.isna().any() or merchant.eq("").any():
        issues.append("가맹점 번호에 결측값 또는 빈 값이 있습니다.")
    for col in ("승인건수", "승인금액"):
        raw = df[col].astype("string").str.replace(",", "", regex=False)
        if pd.to_numeric(raw, errors="coerce").isna().any():
            issues.append(f"{col}에 숫자로 변환할 수 없는 값이 있습니다.")
    month = df["집계년월"].astype("string").str.replace(r"[^0-9]", "", regex=True)
    if not month.str.len().eq(6).all():
        issues.append("집계년월이 YYYYMM 형식이 아닙니다.")
    date = df["집계년월일"].astype("string").str.replace(r"[^0-9]", "", regex=True)
    if not date.str.len().eq(8).all():
        issues.append("집계년월일이 YYYYMMDD 형식이 아닙니다.")
    return issues


def load_excel(path: str | Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {source}")
    return pd.read_excel(source, sheet_name=sheet_name, dtype="string")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="정산 데이터 기본 컬럼 검증")
    parser.add_argument("--input", required=True, help="승인된 환경의 로컬 XLSX 파일 경로")
    parser.add_argument("--sheet", default="Sheet1")
    args = parser.parse_args()
    df = load_excel(args.input, args.sheet)
    issues = validate_dataframe(df)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        raise SystemExit(1)
    print(f"기본 검증 통과: {len(df):,}행, {len(df.columns)}개 컬럼")

if __name__ == "__main__":
    main()
