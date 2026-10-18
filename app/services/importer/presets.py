"""증권사별 CSV 컬럼 매핑 프리셋. 실제 내보내기 파일을 받으면 여기에 추가한다."""

from app.services.importer.csv_parser import ColumnMapping

PRESETS: dict[str, tuple[str, ColumnMapping]] = {
    "generic": (
        "기본 (date, ticker, side, price, quantity, fee)",
        ColumnMapping(
            date="date",
            ticker="ticker",
            side="side",
            price="price",
            quantity="quantity",
            fee_columns=["fee"],
        ),
    ),
}


KEYWORDS: dict[str, tuple[str, ...]] = {
    "date": ("거래일", "체결일", "일자", "날짜", "date"),
    "ticker": ("티커", "종목코드", "symbol", "ticker", "종목"),
    "side": ("매매구분", "구분", "거래종류", "side", "type"),
    "price": ("단가", "체결가", "가격", "price"),
    "quantity": ("수량", "quantity", "qty", "shares"),
}
FEE_KEYWORDS = ("수수료", "세금", "제세금", "fee", "commission", "tax")


def guess_mapping(columns: list[str]) -> ColumnMapping | None:
    """프리셋이 없을 때 헤더 이름으로 컬럼을 추측한다. 필수 컬럼을 다 못 찾으면 None."""
    if key := guess_preset(columns):
        return PRESETS[key][1]
    picked: dict[str, str] = {}
    for field, words in KEYWORDS.items():
        for word in words:  # 앞쪽 키워드가 더 구체적이므로 우선
            hit = next((c for c in columns if word in c.lower() and c not in picked.values()), None)
            if hit:
                picked[field] = hit
                break
    if len(picked) < len(KEYWORDS):
        return None
    fees = [c for c in columns if any(w in c.lower() for w in FEE_KEYWORDS)]
    return ColumnMapping(
        date=picked["date"],
        ticker=picked["ticker"],
        side=picked["side"],
        price=picked["price"],
        quantity=picked["quantity"],
        fee_columns=fees,
    )


def guess_preset(columns: list[str]) -> str | None:
    """CSV 헤더에 프리셋의 필수 컬럼이 모두 있으면 그 프리셋을 고른다."""
    cols = set(columns)
    for key, (_, m) in PRESETS.items():
        if {m.date, m.ticker, m.side, m.price, m.quantity, *m.fee_columns} <= cols:
            return key
    return None
