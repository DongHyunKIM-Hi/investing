"""보유 현황·계획 점검: 지금 들고 있는 종목을 살 때 남긴 계획과 대조한다."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.holdings import watches
from app.services.market.prices import PriceService
from app.services.recompute import recompute_tickers
from app.web import templates

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]


@router.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request, db: DB, notice: str | None = None):
    today = date.today()
    held = watches(db, PriceService(db), today)
    cost = sum(w.avg_cost * w.quantity for w in held)
    value = sum((w.last_close or w.avg_cost) * w.quantity for w in held)
    stale = [w.ticker for w in held if w.as_of is None or (today - w.as_of).days > 4]
    return templates.TemplateResponse(
        request,
        "positions.html",
        {
            "watches": held,
            "cost": cost,
            "value": value,
            "total_return": value / cost - 1 if cost else None,
            "alerts": sum(1 for w in held if w.urgency <= 2),
            "stale": stale,
            "notice": notice,
            "today": today,
        },
    )


@router.post("/positions/refresh")
def positions_refresh(db: DB):
    """보유 종목 시세를 오늘까지 받고 결과도 다시 계산한다."""
    held = [w.ticker for w in watches(db, PriceService(db), date.today())]
    warnings = recompute_tickers(db, held, PriceService(db))
    db.commit()
    notice = " / ".join(warnings) if warnings else f"{len(held)}개 종목 시세를 갱신했어요."
    return RedirectResponse(f"/positions?notice={notice}", status_code=303)
