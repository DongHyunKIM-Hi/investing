"""매매 결정의 결과 지표 계산 (순수 함수).

- 실현 수익률은 실제 체결가·수수료 기준
- 기간 수익률(SPY 대비, 최대낙폭, 매도 후 흐름)은 수정종가 기준 → 분할·배당 영향 제거
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

POST_EXIT_DAYS = 20


@dataclass(frozen=True)
class FillLite:
    id: int
    trade_date: date
    price: Decimal
    quantity: Decimal
    fee: Decimal


@dataclass(frozen=True)
class Leg:
    """FIFO로 매칭된 한 조각: 매수 체결 + 매도 체결 + 매칭 수량."""

    buy: FillLite
    sell: FillLite
    quantity: Decimal


def value_at(s: pd.Series, d: date) -> float | None:
    """d 당일 또는 그 이전 마지막 거래일 값."""
    past = s[s.index <= d]
    return float(past.iloc[-1]) if len(past) else None


def period_return(s: pd.Series, start: date, end: date) -> float | None:
    a, b = value_at(s, start), value_at(s, end)
    return b / a - 1 if a and b else None


def max_drawdown(s: pd.Series, start: date, end: date) -> float | None:
    base = value_at(s, start)
    window = s[(s.index > start) & (s.index <= end)]
    if not base or window.empty:
        return None
    return min(float(window.min()) / base - 1, 0.0)


def forward_return(s: pd.Series, start: date, days: int = POST_EXIT_DAYS) -> float | None:
    """start 이후 N거래일 뒤 수익률. 아직 N거래일이 안 지났으면 None."""
    base = value_at(s, start)
    after = s[s.index > start]
    if not base or len(after) < days:
        return None
    return float(after.iloc[days - 1]) / base - 1


def forward_date(s: pd.Series, start: date, days: int = POST_EXIT_DAYS) -> date | None:
    """start 이후 N번째 거래일. 아직 안 왔으면 None."""
    after = s[s.index > start]
    return after.index[days - 1] if len(after) >= days else None


def _leg_pnl(leg: Leg) -> tuple[Decimal, Decimal]:
    """(원가, 회수금액). 수수료는 체결 수량 비율로 배분."""
    q = leg.quantity
    cost = q * leg.buy.price + leg.buy.fee * q / leg.buy.quantity
    proceeds = q * leg.sell.price - leg.sell.fee * q / leg.sell.quantity
    return cost, proceeds


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def buy_outcome(
    buys: list[FillLite],
    legs: list[Leg],
    close: pd.Series,
    adj: pd.Series,
    spy_adj: pd.Series,
) -> dict[str, Any]:
    total_qty = sum((b.quantity for b in buys), Decimal(0))
    avg_entry = sum((b.price * b.quantity for b in buys), Decimal(0)) / total_qty
    closed_qty = sum((leg.quantity for leg in legs), Decimal(0))
    opened_on = min(b.trade_date for b in buys)

    status = "open" if closed_qty == 0 else "closed" if closed_qty >= total_qty else "partial"
    realized = None
    if legs:
        cost, proceeds = map(sum, zip(*(_leg_pnl(leg) for leg in legs), strict=True))
        realized = float(proceeds / cost - 1)

    last_day = adj.index[-1] if len(adj) else opened_on
    closed_on = max(leg.sell.trade_date for leg in legs) if status == "closed" else None
    end = closed_on or last_day

    last_close = value_at(close, end)
    mark = float(Decimal(str(last_close)) / avg_entry - 1) if last_close else None
    result_return = realized if status == "closed" else mark
    spy_ret = period_return(spy_adj, opened_on, end)

    return {
        "status": status,
        "opened_on": opened_on.isoformat(),
        "closed_on": closed_on.isoformat() if closed_on else None,
        "holding_days": (end - opened_on).days,
        "quantity": float(total_qty),
        "closed_quantity": float(closed_qty),
        "avg_entry": float(avg_entry),
        "realized_return": _r(realized),
        "mark_return": _r(mark) if status != "closed" else None,
        "spy_return": _r(spy_ret),
        "excess_return": _r(result_return - spy_ret)
        if result_return is not None and spy_ret is not None
        else None,
        "max_drawdown": _r(max_drawdown(adj, opened_on, end)),
        "post_exit_20d": _r(forward_return(adj, closed_on)) if closed_on else None,
        "win": realized > 0 if status == "closed" and realized is not None else None,
    }


def sell_outcome(sells: list[FillLite], legs: list[Leg], adj: pd.Series) -> dict[str, Any]:
    sold_on = max(s.trade_date for s in sells)
    realized = None
    if legs:
        cost, proceeds = map(sum, zip(*(_leg_pnl(leg) for leg in legs), strict=True))
        realized = float(proceeds / cost - 1)
    held_from = min((leg.buy.trade_date for leg in legs), default=None)
    return {
        "sold_on": sold_on.isoformat(),
        "realized_return": _r(realized),
        "holding_days": (sold_on - held_from).days if held_from else None,
        # 판 뒤 20거래일 동안 더 올랐다면 '너무 일찍 판' 신호
        "post_exit_20d": _r(forward_return(adj, sold_on)),
        # 위 값이 확정된 날. 매도 판정에서 이날 이전의 상담에는 이 매도를 쓰지 않는다
        "post_exit_on": post_on.isoformat() if (post_on := forward_date(adj, sold_on)) else None,
    }


STOP_SLIPPAGE = 0.03  # 손절가보다 이만큼 더 내려간 적이 있으면 손절을 안 지킨 것으로 본다
HORIZON_OVERRUN = 2.0  # 계획한 보유 기간의 2배를 넘겨 들고 있었으면 기간을 안 지킨 것


def plan_status(
    outcome: dict[str, Any], stop_price: float | None, horizon_days: int | None
) -> str | None:
    """청산된 매수가 세운 계획(손절가·보유 기간)을 지켰는가.

    kept | broken | none(계획 없이 삼) | None(청산 전이거나 판단할 수 없음)
    - 손절: 보유 중 최저점이 손절가 아래로 STOP_SLIPPAGE 넘게 내려갔다면 손절하지 않고 버틴 것.
      최대낙폭은 매수일 종가 기준이라 평균 매수가와 약간 다를 수 있어 여유를 둔다.
    - 목표가는 보지 않는다: 목표 전에 판 건 계획 위반보다 판단 변경에 가깝다.
    """
    if outcome.get("status") != "closed":
        return None
    if stop_price is None and horizon_days is None:
        return "none"
    if stop_price is not None:
        entry, dd = outcome.get("avg_entry"), outcome.get("max_drawdown")
        if not entry or dd is None:
            return None
        if dd < stop_price / entry - 1 - STOP_SLIPPAGE:
            return "broken"
    if horizon_days is not None:
        held = outcome.get("holding_days")
        if held is None:
            return None
        if held > horizon_days * HORIZON_OVERRUN:
            return "broken"
    return "kept"


SKIP_WINDOWS = (5, 20, 60)  # 보류 후 추적할 거래일 수. 마지막까지 지나면 추적 끝


def skip_outcome(close: pd.Series, adj: pd.Series, decided_on: date) -> dict[str, Any]:
    """보류한 결정: 그날 종가에 샀다면 이후 어떻게 됐을까.

    수익률은 수정종가 기준. N거래일이 아직 안 지난 구간은 None.
    """
    entry = value_at(close, decided_on)
    base = value_at(adj, decided_on)
    after = adj[adj.index > decided_on]
    window = after.iloc[:POST_EXIT_DAYS]
    if base and not window.empty:
        low, high = float(window.min()) / base - 1, float(window.max()) / base - 1
    else:
        low = high = None
    return {
        "status": "skipped",
        "tracking_done": len(after) >= SKIP_WINDOWS[-1],
        "tracked_days": len(after),
        "entry": entry,
        **{f"fwd_{n}d": _r(forward_return(adj, decided_on, n)) for n in SKIP_WINDOWS},
        # 샀다면 20거래일 안에 겪었을 최대 낙폭·최대 상승
        "max_drawdown_20d": _r(min(low, 0.0)) if low is not None else None,
        "max_runup_20d": _r(max(high, 0.0)) if high is not None else None,
    }
