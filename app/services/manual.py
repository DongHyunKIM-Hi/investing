"""상담 뒤 실제로 산/판 체결을 직접 기록한다 (CSV를 다시 올리기 전에도 결과 추적이 이어지게).

- 수동 체결은 dedup_key가 'manual:'로 시작한다. 같은 결정의 체결이 나중에 CSV로 들어오면
  CSV 체결로 대체된다 (importer/pipeline).
- 체결을 넣거나 지운 뒤에는 그 종목을 다시 계산한다 (FIFO → outcome).
"""

import secrets
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import Decision, Fill
from app.services.market.prices import PriceService
from app.services.recompute import recompute_tickers

MANUAL_PREFIX = "manual:"
EARLIEST = date(2000, 1, 1)


@dataclass(frozen=True)
class ManualFill:
    trade_date: date
    price: Decimal
    quantity: Decimal
    fee: Decimal


def is_manual(fill: Fill) -> bool:
    return fill.dedup_key.startswith(MANUAL_PREFIX)


def _decimal(raw: str, name: str) -> Decimal:
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{name}을(를) 숫자로 적어 주세요.") from None
    if not value.is_finite():
        raise ValueError(f"{name}을(를) 숫자로 적어 주세요.")
    return value


def parse_manual_fill(
    trade_date: str, price: str, quantity: str, fee: str, today: date
) -> ManualFill:
    """폼 입력을 검증한다. 문제가 있으면 사용자에게 보여 줄 메시지로 ValueError."""
    try:
        day = date.fromisoformat(trade_date.strip())
    except ValueError:
        raise ValueError("체결일을 YYYY-MM-DD 형식으로 적어 주세요.") from None
    if day > today:
        raise ValueError("체결일이 오늘보다 뒤예요.")
    if day < EARLIEST:
        raise ValueError(f"체결일은 {EARLIEST.year}년 이후만 기록할 수 있어요.")
    px = _decimal(price, "체결가")
    qty = _decimal(quantity, "수량")
    fee_value = _decimal(fee or "0", "수수료")
    if px <= 0:
        raise ValueError("체결가는 0보다 커야 해요.")
    if qty <= 0:
        raise ValueError("수량은 0보다 커야 해요.")
    if fee_value < 0:
        raise ValueError("수수료는 0 이상이어야 해요.")
    return ManualFill(day, px, qty, fee_value)


def record_fill(db: Session, d: Decision, mf: ManualFill, prices: PriceService) -> list[str]:
    """결정에 수동 체결을 붙이고 종목을 다시 계산한다. 반환값: 재계산 경고."""
    same_day = [f for f in d.fills if f.trade_date == mf.trade_date]
    fill = Fill(
        decision=d,
        ticker=d.ticker,
        side=d.side,
        trade_date=mf.trade_date,
        seq=max((f.seq for f in same_day), default=-1) + 1,
        price=mf.price,
        quantity=mf.quantity,
        fee=mf.fee,
        dedup_key=f"{MANUAL_PREFIX}{d.id}:{secrets.token_hex(8)}",
    )
    db.add(fill)
    d.user_action = "executed"
    if (d.outcome or {}).get("status") == "skipped":
        d.outcome = None  # '샀다면' 추적은 끝. 실제 체결 결과로 바뀐다
    db.flush()
    return recompute_tickers(db, [d.ticker], prices)


def remove_fill(db: Session, d: Decision, fill: Fill, prices: PriceService) -> list[str]:
    """수동 체결만 지울 수 있다. 체결이 모두 사라지면 결과도 지운다."""
    if fill.decision_id != d.id or not is_manual(fill):
        raise ValueError("직접 기록한 체결만 지울 수 있어요.")
    db.delete(fill)
    db.flush()
    db.refresh(d)
    if not d.fills:
        d.outcome = None  # 재계산은 체결이 있는 결정만 건드리므로 여기서 비운다
    return recompute_tickers(db, [d.ticker], prices)
