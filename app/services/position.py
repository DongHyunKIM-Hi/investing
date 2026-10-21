"""보유 포지션: FIFO로 아직 안 팔린 매수 lot을 모아 평단가·평가 손익을 낸다 (순수 함수)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.importer.fifo import FillIn, match_fifo


@dataclass(frozen=True)
class Lot:
    buy_fill_id: int
    trade_date: date
    price: Decimal
    quantity: Decimal  # 아직 안 팔린 수량


@dataclass(frozen=True)
class PricedFill:
    id: int
    side: str
    trade_date: date
    seq: int
    price: Decimal
    quantity: Decimal


def open_lots(fills: Iterable[PricedFill]) -> list[Lot]:
    """한 티커의 체결에서 아직 안 팔린 매수 lot (오래된 순)."""
    fills = list(fills)
    fifo = match_fifo(FillIn(f.id, f.side, f.trade_date, f.seq, f.quantity) for f in fills)
    by_id = {f.id: f for f in fills}
    lots = [
        Lot(i, by_id[i].trade_date, by_id[i].price, q) for i, q in fifo.open_qty.items() if q > 0
    ]
    return sorted(lots, key=lambda lot: (lot.trade_date, lot.buy_fill_id))


def summarize_position(
    lots: Sequence[Lot], last_close: float | None, today: date
) -> dict[str, Any] | None:
    """상담 context에 넣을 보유 정보. 들고 있는 게 없으면 None."""
    qty = sum((lot.quantity for lot in lots), Decimal(0))
    if qty <= 0:
        return None
    avg = sum((lot.price * lot.quantity for lot in lots), Decimal(0)) / qty
    opened = min(lot.trade_date for lot in lots)
    return {
        "position_qty": float(qty),
        "avg_cost": round(float(avg), 4),
        "opened_on": opened.isoformat(),
        "held_days": (today - opened).days,
        "position_return": round(last_close / float(avg) - 1, 4) if last_close else None,
    }


def manual_position(avg_cost: float, last_close: float | None) -> dict[str, Any]:
    """보유 기록이 없을 때 사용자가 알려 준 평단가로 손익만 계산한다."""
    return {
        "avg_cost": avg_cost,
        "position_return": round(last_close / avg_cost - 1, 4) if last_close else None,
        "position_manual": True,
    }
