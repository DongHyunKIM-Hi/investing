from datetime import date
from decimal import Decimal as D

from app.services.importer.fifo import FillIn, match_fifo
from app.services.importer.splits import split_factor
from app.services.position import PricedFill, open_lots, summarize_position

NVDA = [(date(2021, 7, 20), 4.0), (date(2024, 6, 10), 10.0)]


def test_split_factor_counts_only_later_splits():
    assert split_factor(date(2020, 1, 1), NVDA) == D(40)
    assert split_factor(date(2024, 6, 7), NVDA) == D(10)
    assert split_factor(date(2024, 6, 10), NVDA) == D(1)  # 분할일 당일 체결은 이미 분할 후
    assert split_factor(date(2024, 6, 7), [(date(2024, 6, 10), 0.1)]) == D("0.1")  # 병합
    assert split_factor(date(2024, 1, 1), []) == D(1)


def test_fifo_matches_after_split_adjustment():
    # 분할 전 1주 매수 → 분할 후 10주 매도: 보정 없이는 9주가 '매수 기록 없는 매도'가 된다
    buy_day, sell_day = date(2024, 6, 3), date(2024, 7, 1)
    raw = [
        PricedFill(1, "buy", buy_day, 0, D("1100"), D("1")),
        PricedFill(2, "sell", sell_day, 0, D("125"), D("10")),
    ]
    unadjusted = match_fifo(FillIn(f.id, f.side, f.trade_date, f.seq, f.quantity) for f in raw)
    assert unadjusted.unmatched_sells == {2: D(9)}

    adj = []
    for f in raw:
        k = split_factor(f.trade_date, NVDA)
        adj.append(PricedFill(f.id, f.side, f.trade_date, f.seq, f.price / k, f.quantity * k))
    fixed = match_fifo(FillIn(f.id, f.side, f.trade_date, f.seq, f.quantity) for f in adj)
    assert fixed.unmatched_sells == {}
    assert [m.quantity for m in fixed.matches] == [D(10)]


def test_position_return_uses_post_split_cost():
    k = split_factor(date(2024, 6, 3), NVDA)
    lots = open_lots([PricedFill(1, "buy", date(2024, 6, 3), 0, D("1100") / k, D("1") * k)])
    pos = summarize_position(lots, 132.0, date(2024, 7, 1))
    assert pos is not None
    assert pos["position_qty"] == 10 and pos["avg_cost"] == 110
    assert abs(pos["position_return"] - 0.2) < 1e-9  # 보정 없이는 -88%로 보인다
