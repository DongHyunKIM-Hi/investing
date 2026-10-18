from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal as D

import pandas as pd
import pytest

from app.services.outcomes import FillLite, Leg, buy_outcome, forward_return, sell_outcome

START = date(2026, 1, 1)


def series(values: Sequence[float]) -> pd.Series:
    return pd.Series(values, index=[START + timedelta(days=i) for i in range(len(values))])


def fill(id: int, day: int, price: str, qty: str, fee: str = "0") -> FillLite:
    return FillLite(id, START + timedelta(days=day), D(price), D(qty), D(fee))


def test_closed_round_trip_with_fees():
    prices = series([100, 90, 80, 110, 120] + [130] * 25)
    spy = series([100, 101, 102, 103, 105] + [105] * 25)
    buy = fill(1, 0, "100", "10", fee="10")
    sell = fill(2, 4, "120", "10", fee="12")

    out = buy_outcome([buy], [Leg(buy, sell, D("10"))], prices, prices, spy)

    # 원가 1010, 회수 1188 → +17.62%
    assert out["status"] == "closed"
    assert out["realized_return"] == pytest.approx(0.1762, abs=1e-4)
    assert out["win"] is True
    assert out["holding_days"] == 4
    assert out["spy_return"] == pytest.approx(0.05)
    assert out["excess_return"] == pytest.approx(0.1262, abs=1e-4)
    assert out["max_drawdown"] == pytest.approx(-0.20)
    assert out["post_exit_20d"] == pytest.approx(130 / 120 - 1, abs=1e-4)


def test_partial_position_uses_mark_return():
    prices = series([100, 105, 110])
    b1, b2 = fill(1, 0, "100", "2"), fill(2, 1, "104", "2")
    sell = fill(3, 2, "110", "1")

    out = buy_outcome([b1, b2], [Leg(b1, sell, D("1"))], prices, prices, prices)

    assert out["status"] == "partial"
    assert out["avg_entry"] == pytest.approx(102)
    assert out["realized_return"] == pytest.approx(0.10)
    assert out["mark_return"] == pytest.approx(110 / 102 - 1, abs=1e-4)
    assert out["win"] is None
    assert out["closed_on"] is None


def test_forward_return_needs_enough_days():
    assert forward_return(series([100] * 10), START) is None


def test_sell_outcome_detects_selling_too_early():
    prices = series([100] * 5 + [150] * 30)
    buy, sell = fill(1, 0, "90", "1"), fill(2, 4, "100", "1")
    out = sell_outcome([sell], [Leg(buy, sell, D("1"))], prices)
    assert out["realized_return"] == pytest.approx(0.1111, abs=1e-4)
    assert out["post_exit_20d"] == pytest.approx(0.5)
    assert out["holding_days"] == 4
