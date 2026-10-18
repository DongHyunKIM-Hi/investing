"""선입선출(FIFO)로 매도 체결을 앞선 매수 체결에 매칭한다."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class FillIn:
    id: int
    side: str
    trade_date: date
    seq: int
    quantity: Decimal


@dataclass(frozen=True)
class Match:
    buy_id: int
    sell_id: int
    quantity: Decimal


@dataclass
class FifoResult:
    matches: list[Match] = field(default_factory=list)
    open_qty: dict[int, Decimal] = field(default_factory=dict)  # 매수 체결별 미청산 수량
    # CSV 기간 이전에 산 주식을 판 경우처럼 짝이 없는 매도 수량
    unmatched_sells: dict[int, Decimal] = field(default_factory=dict)


def match_fifo(fills: Iterable[FillIn]) -> FifoResult:
    """한 티커의 체결들을 받아 매칭한다. 정렬 기준: (체결일, seq, id)."""
    result = FifoResult()
    lots: deque[list] = deque()  # [buy_id, 남은 수량]

    for f in sorted(fills, key=lambda f: (f.trade_date, f.seq, f.id)):
        if f.side == "buy":
            lots.append([f.id, f.quantity])
            result.open_qty[f.id] = f.quantity
            continue

        remaining = f.quantity
        while remaining > 0 and lots:
            lot = lots[0]
            take = min(lot[1], remaining)
            result.matches.append(Match(buy_id=lot[0], sell_id=f.id, quantity=take))
            lot[1] -= take
            result.open_qty[lot[0]] = lot[1]
            remaining -= take
            if lot[1] == 0:
                lots.popleft()
        if remaining > 0:
            result.unmatched_sells[f.id] = remaining

    return result
