from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Decision
from app.services.engine.case import case_from_decision
from app.services.engine.verdict import EARLY_EXIT, MIN_SAMPLES, VERDICTS
from app.services.report import build_report, sell_case_from_decision, skip_case_from_decision
from app.web import templates

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]


@router.get("/report", response_class=HTMLResponse)
def report_page(request: Request, db: DB):
    # 결과(outcome)는 체결이 있는 결정과 보류한 상담 결정(샀다면의 결과)에만 생긴다
    rows = db.scalars(select(Decision).where(Decision.outcome.is_not(None))).all()
    # JSONB 칼럼에 None을 넣으면 SQL NULL이 아닌 JSON null이 되므로 한 번 더 거른다
    decisions = [d for d in rows if d.outcome]
    skipped = [d for d in decisions if (d.outcome or {}).get("status") == "skipped"]
    traded = [d for d in decisions if (d.outcome or {}).get("status") != "skipped"]
    report = build_report(
        [case_from_decision(d) for d in traded if d.side == "buy"],
        [sell_case_from_decision(d) for d in traded if d.side == "sell"],
        [skip_case_from_decision(d) for d in skipped],
    )
    return templates.TemplateResponse(
        request,
        "report.html",
        {"r": report, "MIN_SAMPLES": MIN_SAMPLES, "EARLY_EXIT": EARLY_EXIT, "VERDICTS": VERDICTS},
    )
