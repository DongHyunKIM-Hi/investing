from datetime import date
from decimal import Decimal as D

from app.services.importer.fifo import FillIn, Match, match_fifo


def f(id: int, side: str, day: int, qty: str, seq: int = 0) -> FillIn:
    return FillIn(id=id, side=side, trade_date=date(2026, 1, day), seq=seq, quantity=D(qty))


def test_split_buys_and_partial_sell():
    r = match_fifo([f(1, "buy", 2, "10"), f(2, "buy", 3, "5"), f(3, "sell", 5, "12")])
    assert r.matches == [Match(1, 3, D("10")), Match(2, 3, D("2"))]
    assert r.open_qty == {1: D("0"), 2: D("3")}
    assert r.unmatched_sells == {}


def test_input_order_does_not_matter():
    fills = [f(3, "sell", 5, "4"), f(1, "buy", 2, "4")]
    assert match_fifo(fills).matches == [Match(1, 3, D("4"))]


def test_same_day_uses_seq():
    # 같은 날 매도 후 재매수: 매도는 이전 보유분과 매칭되어야 한다
    fills = [f(1, "buy", 2, "5"), f(2, "sell", 9, "5", seq=0), f(3, "buy", 9, "5", seq=1)]
    r = match_fifo(fills)
    assert r.matches == [Match(1, 2, D("5"))]
    assert r.open_qty[3] == D("5")


def test_oversell_is_recorded_not_raised():
    r = match_fifo([f(1, "buy", 2, "1"), f(2, "sell", 3, "3")])
    assert r.matches == [Match(1, 2, D("1"))]
    assert r.unmatched_sells == {2: D("2")}


def test_fractional_shares():
    r = match_fifo([f(1, "buy", 2, "0.5"), f(2, "buy", 3, "0.25"), f(3, "sell", 4, "0.6")])
    assert r.matches == [Match(1, 3, D("0.5")), Match(2, 3, D("0.1"))]
    assert r.open_qty[2] == D("0.15")
