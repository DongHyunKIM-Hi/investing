from datetime import date
from decimal import Decimal as D

import pytest

from app.services.importer.csv_parser import ColumnMapping, extract_ticker, parse_fills, read_csv
from app.services.importer.presets import PRESETS, guess_mapping, guess_preset

GENERIC = PRESETS["generic"][1]

KOREAN_CSV = """거래일자,종목명,구분,체결단가,체결수량,수수료,제세금
2026.03.05,테슬라(TSLA),해외주식매도,"1,020.50",2,1.02,0.05
2026.03.04,애플(AAPL),해외주식매수,210.00,3,0.63,0
2026.03.04,입금,입금,0,0,0,0
2026.03.03,테슬라(TSLA),해외주식매수,"1,000.00",2,1.00,0
"""

KOREAN_MAPPING = ColumnMapping(
    date="거래일자",
    date_format="%Y.%m.%d",
    ticker="종목명",
    side="구분",
    price="체결단가",
    quantity="체결수량",
    fee_columns=["수수료", "제세금"],
)


def test_korean_cp949_descending_file():
    df = read_csv(KOREAN_CSV.encode("cp949"))
    result = parse_fills(df, KOREAN_MAPPING)

    assert [(p.ticker, p.side, p.trade_date) for p in result.fills] == [
        ("TSLA", "buy", date(2026, 3, 3)),
        ("AAPL", "buy", date(2026, 3, 4)),
        ("TSLA", "sell", date(2026, 3, 5)),
    ]
    assert [p.seq for p in result.fills] == [0, 1, 2]
    sell = result.fills[2]
    assert sell.price == D("1020.50") and sell.fee == D("1.07")
    assert result.skipped == [(4, "매수/매도가 아님: '입금'")]


def test_identical_rows_get_distinct_keys_and_reimport_is_stable():
    row = "2026-01-02,AAPL,buy,100,1,0\n"
    csv = "date,ticker,side,price,quantity,fee\n" + row * 2
    first = parse_fills(read_csv(csv.encode()), GENERIC)
    again = parse_fills(read_csv(csv.encode()), GENERIC)
    keys = [p.dedup_key for p in first.fills]
    assert len(set(keys)) == 2
    assert keys == [p.dedup_key for p in again.fills]


def test_missing_column_raises():
    with pytest.raises(ValueError, match="price"):
        parse_fills(read_csv(b"date,ticker,side,quantity,fee\n"), GENERIC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        (" nvda ", "NVDA"),
        ("애플(AAPL)", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("애플", None),
    ],
)
def test_extract_ticker(raw: str, expected: str | None):
    assert extract_ticker(raw) == expected


def test_guess_preset():
    assert guess_preset(["date", "ticker", "side", "price", "quantity", "fee", "memo"]) == "generic"
    assert guess_preset(["거래일자", "종목명"]) is None


def test_guess_mapping_from_korean_headers():
    m = guess_mapping(
        ["거래일자", "종목명", "매매구분", "체결단가", "체결수량", "수수료", "제세금"]
    )
    assert m is not None
    assert (m.date, m.ticker, m.side, m.price, m.quantity) == (
        "거래일자",
        "종목명",
        "매매구분",
        "체결단가",
        "체결수량",
    )
    assert m.fee_columns == ["수수료", "제세금"]
