"""티커 단위로 FIFO 매칭, 결정 시점 상황(context), 결과(outcome)를 다시 계산한다.

보류한 상담 결정은 체결이 없으므로 track_skipped로 따로 '샀다면' 결과를 추적한다.
"""

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models import Decision, Fill, LotMatch
from app.services.importer.fifo import FillIn, match_fifo
from app.services.market.prices import BENCHMARK, PriceService
from app.services.market.snapshot import build_snapshot
from app.services.outcomes import FillLite, Leg, buy_outcome, sell_outcome, skip_outcome

log = logging.getLogger(__name__)
LOOKBACK = timedelta(days=400)  # 200일선·52주 고점 계산용


def recompute_tickers(db: Session, tickers: Iterable[str], prices: PriceService) -> list[str]:
    """반환값: 실패한 티커에 대한 경고 메시지."""
    warnings: list[str] = []
    for ticker in sorted(set(tickers)):
        try:
            warnings.extend(_recompute_ticker(db, ticker, prices))
        except Exception as e:  # 시세 조회 실패 등으로 다른 티커까지 멈추지 않게
            log.exception("recompute failed: %s", ticker)
            warnings.append(f"{ticker}: 재계산 실패 ({e})")
    db.flush()
    return warnings


def _recompute_ticker(db: Session, ticker: str, prices: PriceService) -> list[str]:
    warnings: list[str] = []
    fills = db.scalars(select(Fill).where(Fill.ticker == ticker)).all()
    if not fills:
        return warnings

    fifo = match_fifo(FillIn(f.id, f.side, f.trade_date, f.seq, f.quantity) for f in fills)
    db.execute(delete(LotMatch).where(LotMatch.ticker == ticker))
    db.add_all(
        LotMatch(ticker=ticker, buy_fill_id=m.buy_id, sell_fill_id=m.sell_id, quantity=m.quantity)
        for m in fifo.matches
    )
    if fifo.unmatched_sells:
        qty = sum(fifo.unmatched_sells.values(), Decimal(0))
        warnings.append(
            f"{ticker}: 매수 기록 없이 매도된 수량 {qty.normalize()}주 (CSV 기간 이전 매수분)"
        )

    lite = {f.id: FillLite(f.id, f.trade_date, f.price, f.quantity, f.fee) for f in fills}
    legs = [Leg(lite[m.buy_id], lite[m.sell_id], m.quantity) for m in fifo.matches]

    start = min(f.trade_date for f in fills) - LOOKBACK
    bars = prices.bars(ticker, start, date.today())
    if bars.empty:
        return [*warnings, f"{ticker}: 시세를 찾을 수 없음 (상장폐지·티커 변경 가능성)"]
    spy = prices.bars(BENCHMARK, start, date.today())["adj_close"]

    decisions = db.scalars(
        select(Decision).where(Decision.ticker == ticker).options(selectinload(Decision.fills))
    ).all()
    for d in decisions:
        if not d.fills:
            continue
        ids = {f.id for f in d.fills}
        snap = build_snapshot(bars["adj_close"], spy, d.decided_on)
        # 상담 때 저장한 현재가·실적일 같은 항목은 남긴다
        d.context = {**(d.context or {}), **snap} if snap else d.context
        if d.side == "buy":
            d.outcome = buy_outcome(
                [lite[i] for i in ids],
                [leg for leg in legs if leg.buy.id in ids],
                bars["close"],
                bars["adj_close"],
                spy,
            )
        else:
            d.outcome = sell_outcome(
                [lite[i] for i in ids],
                [leg for leg in legs if leg.sell.id in ids],
                bars["adj_close"],
            )
    return warnings


def track_skipped(db: Session, prices: PriceService, ids: Iterable[int] | None = None) -> list[str]:
    """보류한 상담 결정의 이후 가격을 추적한다. 추적이 끝난 건(60거래일 경과)은 건너뛴다.

    ids를 주면 그 결정만 계산한다. 반환값: 실패한 결정에 대한 경고 메시지.
    """
    query = select(Decision).where(Decision.user_action == "skipped", ~Decision.fills.any())
    if ids is not None:
        query = query.where(Decision.id.in_(list(ids)))
    warnings: list[str] = []
    for d in db.scalars(query).all():
        if (d.outcome or {}).get("tracking_done"):
            continue
        try:
            bars = prices.bars(d.ticker, d.decided_on - timedelta(days=10), date.today())
        except Exception as e:
            log.exception("skip tracking failed: %s", d.ticker)
            warnings.append(f"{d.ticker} 보류 건: 시세 조회 실패 ({e})")
            continue
        if bars.empty:
            warnings.append(f"{d.ticker} 보류 건: 시세를 찾을 수 없음")
            continue
        d.outcome = skip_outcome(bars["close"], bars["adj_close"], d.decided_on)
    db.flush()
    return warnings
