"""증권사 체결내역 파일(CSV·엑셀) → 정규화된 체결 목록."""

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd
from pydantic import BaseModel, Field

ENCODINGS = ("utf-8-sig", "cp949")
TICKER_IN_PARENS = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9})\)")
TICKER_PLAIN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class ColumnMapping(BaseModel):
    date: str
    date_format: str | None = None  # 예: "%Y.%m.%d". 없으면 자동 추론
    ticker: str
    side: str
    buy_values: list[str] = Field(default_factory=lambda: ["매수", "buy", "BUY", "Buy"])
    sell_values: list[str] = Field(default_factory=lambda: ["매도", "sell", "SELL", "Sell"])
    price: str
    quantity: str
    fee_columns: list[str] = Field(default_factory=list)  # 수수료·제세금 등 합산


@dataclass(frozen=True)
class ParsedFill:
    ticker: str
    side: str
    trade_date: date
    seq: int
    price: Decimal
    quantity: Decimal
    fee: Decimal
    dedup_key: str


@dataclass
class ParseResult:
    fills: list[ParsedFill]
    skipped: list[tuple[int, str]]  # (CSV 행 번호, 사유)


XLSX_MAGIC = b"PK\x03\x04"  # .xlsx는 zip 파일
HEADER_SCAN_ROWS = 15
# 헤더 줄을 알아보는 단어. 증권사 파일은 위에 '해외주식 거래내역' 같은 제목 줄이 붙곤 한다
HEADER_HINTS = (
    "일자",
    "일",
    "date",
    "종목",
    "티커",
    "ticker",
    "symbol",
    "구분",
    "side",
    "type",
    "단가",
    "가격",
    "price",
    "수량",
    "quantity",
    "qty",
)
MIN_HEADER_HITS = 3


def _header_row(rows: list[list[str]]) -> int:
    """제목 줄을 건너뛰고 컬럼 이름이 있는 첫 줄의 위치. 못 찾으면 0."""
    for i, cells in enumerate(rows[:HEADER_SCAN_ROWS]):
        names = [c.strip().lower() for c in cells if c and c.strip()]
        hits = sum(1 for c in names if any(h in c for h in HEADER_HINTS))
        if hits >= MIN_HEADER_HITS:
            return i
    return 0


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.fillna("").astype(str)
    df.columns = [str(c).strip() for c in df.columns]
    # 엑셀 합계 줄·빈 줄처럼 값이 하나도 없는 행은 버린다
    return df[(df.apply(lambda col: col.str.strip()) != "").any(axis=1)].reset_index(drop=True)


def read_table(data: bytes) -> pd.DataFrame:
    """체결내역 파일(CSV 또는 .xlsx) → 모든 값이 문자열인 표.

    국내 증권사 CSV는 cp949가 흔하므로 인코딩을 순서대로 시도한다.
    """
    if data.startswith(XLSX_MAGIC):
        raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str).fillna("")
        i = _header_row(raw.astype(str).values.tolist())
        df = raw.iloc[i + 1 :]
        df.columns = list(raw.iloc[i])
        return _clean(df)
    for enc in ENCODINGS:
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        # 제목 줄은 칸 수가 달라 pandas가 못 읽으므로, 헤더 줄 위치를 먼저 찾는다
        lines = text.splitlines()[:HEADER_SCAN_ROWS]
        skip = _header_row(list(csv.reader(lines)))
        df = pd.read_csv(io.StringIO(text), skiprows=skip, dtype=str, keep_default_na=False)
        return _clean(df)
    raise ValueError("파일 인코딩을 인식하지 못했습니다 (UTF-8 / CP949 CSV 또는 .xlsx 지원)")


def to_decimal(raw: str) -> Decimal | None:
    cleaned = re.sub(r"[,\s$₩원]", "", raw)
    if cleaned in ("", "-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_ticker(raw: str) -> str | None:
    """'AAPL', 'aapl', '애플(AAPL)' 형태를 지원한다."""
    value = raw.strip()
    if m := TICKER_IN_PARENS.search(value.upper()):
        return m.group(1)
    if TICKER_PLAIN.match(value.upper()):
        return value.upper()
    return None


def classify_side(raw: str, mapping: ColumnMapping) -> str | None:
    value = raw.strip()
    # '매도'·'매수'가 둘 다 들어간 값(예: '매수취소')은 없다고 가정하고, 정확 일치를 먼저 본다
    if value in mapping.buy_values:
        return "buy"
    if value in mapping.sell_values:
        return "sell"
    if any(v in value for v in mapping.buy_values):
        return "buy"
    if any(v in value for v in mapping.sell_values):
        return "sell"
    return None


def parse_fills(df: pd.DataFrame, mapping: ColumnMapping) -> ParseResult:
    required = [mapping.date, mapping.ticker, mapping.side, mapping.price, mapping.quantity]
    missing = [c for c in required + mapping.fee_columns if c not in df.columns]
    if missing:
        raise ValueError(f"CSV에 없는 컬럼: {', '.join(missing)}")

    dates = pd.to_datetime(
        df[mapping.date].str.strip(), format=mapping.date_format, errors="coerce"
    )
    rows: list[tuple[int, date, str, str, Decimal, Decimal, Decimal]] = []
    skipped: list[tuple[int, str]] = []

    for i, (_, row) in enumerate(df.iterrows()):
        line = i + 2  # 헤더가 1행
        side = classify_side(row[mapping.side], mapping)
        if side is None:
            skipped.append((line, f"매수/매도가 아님: {row[mapping.side]!r}"))
            continue
        ticker = extract_ticker(row[mapping.ticker])
        if ticker is None:
            skipped.append((line, f"티커를 찾을 수 없음: {row[mapping.ticker]!r}"))
            continue
        trade_dt = dates.iloc[i]
        if pd.isna(trade_dt):
            skipped.append((line, f"날짜 형식 오류: {row[mapping.date]!r}"))
            continue
        price = to_decimal(row[mapping.price])
        qty = to_decimal(row[mapping.quantity])
        if not price or not qty or price <= 0 or qty <= 0:
            skipped.append((line, "가격 또는 수량이 비어 있거나 0"))
            continue
        fee = sum((to_decimal(row[c]) or Decimal(0) for c in mapping.fee_columns), Decimal(0))
        rows.append((line, trade_dt.date(), ticker, side, price, qty, fee))

    # 최신순으로 내보내는 증권사가 많다. 시간순으로 뒤집어 같은 날 체결 순서(seq)를 보존한다
    if len(rows) > 1 and rows[0][1] > rows[-1][1]:
        rows.reverse()

    seen: Counter[str] = Counter()
    fills: list[ParsedFill] = []
    for seq, (_, d, ticker, side, price, qty, fee) in enumerate(rows):
        nums = "|".join(str(n.normalize()) for n in (price, qty, fee))
        base = f"{ticker}|{side}|{d.isoformat()}|{nums}"
        seen[base] += 1  # 같은 파일 내 완전히 동일한 체결이 여러 번 있을 수 있다
        key = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        fills.append(ParsedFill(ticker, side, d, seq, price, qty, fee, key))

    return ParseResult(fills=fills, skipped=skipped)
