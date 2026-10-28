from datetime import date
from decimal import Decimal

import pytest

from app.models import Fill
from app.services.manual import MANUAL_PREFIX, is_manual, parse_manual_fill

TODAY = date(2026, 10, 28)


def test_parse_manual_fill_cleans_numbers():
    mf = parse_manual_fill("2026-10-27", "$1,234.5", "3", "", TODAY)
    assert mf.trade_date == date(2026, 10, 27)
    assert mf.price == Decimal("1234.5") and mf.quantity == 3 and mf.fee == 0


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (("2026/10/27", "10", "1", "0"), "YYYY-MM-DD"),
        (("2026-10-29", "10", "1", "0"), "오늘보다 뒤"),
        (("1999-12-31", "10", "1", "0"), "2000년 이후"),
        (("2026-10-27", "abc", "1", "0"), "체결가"),
        (("2026-10-27", "0", "1", "0"), "체결가는 0보다"),
        (("2026-10-27", "10", "-1", "0"), "수량은 0보다"),
        (("2026-10-27", "10", "1", "-0.5"), "수수료는 0 이상"),
        (("2026-10-27", "NaN", "1", "0"), "체결가"),
    ],
)
def test_parse_manual_fill_rejects_bad_input(raw: tuple[str, str, str, str], message: str):
    day, price, qty, fee = raw
    with pytest.raises(ValueError, match=message):
        parse_manual_fill(day, price, qty, fee, TODAY)


def test_is_manual_by_dedup_prefix():
    assert is_manual(Fill(dedup_key=f"{MANUAL_PREFIX}7:abcd"))
    assert not is_manual(Fill(dedup_key="a1b2c3"))
