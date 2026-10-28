"""보유 현황·계획 점검: 지금 들고 있는 종목을 살 때 남긴 계획과 대조한다."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Fill
from app.services.consult import holding_of
from app.services.market.prices import PriceService
from app.services.recompute import recompute_tickers
from app.services.watch import Watch, check_position, sort_watches
from app.web import templates

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]


def _watches(db: Session, prices: PriceService, today: date) -> list[Watch]:
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


@router.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request, db: DB, notice: str | None = None):
    today = date.today()
    watches = _watches(db, PriceService(db), today)
    cost = sum(w.avg_cost * w.quantity for w in watches)
    value = sum((w.last_close or w.avg_cost) * w.quantity for w in watches)
    stale = [w.ticker for w in watches if w.as_of is None or (today - w.as_of).days > 4]
    return templates.TemplateResponse(
        request,
        "positions.html",
        {
            "watches": watches,
            "cost": cost,
            "value": value,
            "total_return": value / cost - 1 if cost else None,
            "alerts": sum(1 for w in watches if w.urgency <= 2),
            "stale": stale,
            "notice": notice,
            "today": today,
        },
    )


@router.post("/positions/refresh")
def positions_refresh(db: DB):
    """보유 종목 시세를 오늘까지 받고 결과도 다시 계산한다."""
    held = [w.ticker for w in _watches(db, PriceService(db), date.today())]
    warnings = recompute_tickers(db, held, PriceService(db))
    db.commit()
    notice = " / ".join(warnings) if warnings else f"{len(held)}개 종목 시세를 갱신했어요."
    return RedirectResponse(f"/positions?notice={notice}", status_code=303)
