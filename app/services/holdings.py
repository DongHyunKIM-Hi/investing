"""보유 종목 목록 + 계획 점검 (DB 읽기). 화면(/positions)과 매일 돌리는 CLI가 같이 쓴다."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fill
from app.services.consult import holding_of
from app.services.market.prices import PriceService
from app.services.watch import Watch, check_position, sort_watches


def watches(db: Session, prices: PriceService, today: date) -> list[Watch]:
    """지금 보유 중인 종목마다 계획 점검 결과. 시세는 캐시만 쓴다."""
    tickers = db.scalars(select(Fill.ticker).distinct().order_by(Fill.ticker)).all()
    out: list[Watch] = []
    for ticker in tickers:
        last = prices.last_bar(ticker)
        pos = holding_of(db, ticker, last[1] if last else None, today, prices.cached_splits(ticker))
        if not pos:
            continue
        out.append(
            check_position(
                ticker=ticker,
                quantity=pos["position_qty"],
                avg_cost=pos["avg_cost"],
                opened_on=date.fromisoformat(pos["opened_on"]),
                last_close=last[1] if last else None,
                as_of=last[0] if last else None,
                plan=pos.get("buy_plan"),
                today=today,
            )
        )
    return sort_watches(out)
