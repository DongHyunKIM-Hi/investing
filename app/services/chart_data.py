"""결정 하나의 차트 재료를 DB 캐시에서 모은다 (네트워크 없음). 결정 상세·판정 화면이 같이 쓴다."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Decision, Fill, LotMatch
from app.services.chart import Level, Mark, render_chart
from app.services.importer.splits import split_factor
from app.services.market.prices import PriceService

BEFORE = timedelta(days=90)  # 결정일 앞 (달력일)
AFTER = timedelta(days=45)  # 마지막 체결·청산 뒤


def _counterparts(db: Session, d: Decision) -> list[Fill]:
    """FIFO로 이 결정의 체결과 짝지어진 반대쪽 체결 (매수 결정이면 그 lot을 판 매도들)."""
    ids = [f.id for f in d.fills]
    if not ids:
        return []
    mine, other = (LotMatch.buy_fill_id, LotMatch.sell_fill_id)
    if d.side == "sell":
        mine, other = other, mine
    return list(db.scalars(select(Fill).join(LotMatch, other == Fill.id).where(mine.in_(ids))))


def decision_chart(db: Session, d: Decision, prices: PriceService, today: date) -> str:
    """결정일 앞 90일부터, 청산(또는 마지막 체결)+45일 또는 오늘까지의 종가 + 체결·계획선."""
    o = d.outcome or {}
    fills = [*d.fills, *_counterparts(db, d)]
    last = d.decided_on
    if o.get("closed_on"):
        last = max(last, date.fromisoformat(o["closed_on"]))
    for f in fills:
        last = max(last, f.trade_date)
    end = min(today, last + AFTER)
    bars = prices.cached_bars(d.ticker, d.decided_on - BEFORE, end)
    if len(bars) < 2:
        return ""

    # 분할 전 체결가는 지금 기준으로 맞춘다 (시세는 분할 보정된 값이므로)
    splits = prices.cached_splits(d.ticker)
    marks = [
        Mark(f.trade_date, float(f.price / split_factor(f.trade_date, splits)), f.side)
        for f in fills
    ]
    levels: list[Level] = []
    k = split_factor(d.decided_on, splits)  # 상담 뒤 분할이 있었으면 계획가도 지금 기준으로
    if d.target_price is not None:
        levels.append(Level(float(d.target_price / k), "목표", "target"))
    if d.stop_price is not None:
        levels.append(Level(float(d.stop_price / k), "손절", "stop"))
    ctx = d.context or {}
    if d.side == "sell" and ctx.get("avg_cost"):
        levels.append(Level(float(ctx["avg_cost"]), "평단", "avg"))
    return render_chart(bars, marks, levels, d.decided_on)
