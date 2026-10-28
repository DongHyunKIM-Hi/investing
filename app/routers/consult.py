import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Decision, Fill
from app.services.consult import (
    apply_record,
    chat,
    held_tickers,
    judge,
    open_consult,
    start_consult,
    visible_messages,
)
from app.services.engine.backtest import backtest, summarize
from app.services.engine.case import case_from_decision
from app.services.engine.verdict import EARLY_EXIT, VERDICTS
from app.services.llm.consult import ConsultError, Record, RecordInput, SellRecordInput, get_llm
from app.services.llm.keywords import suggest_tags
from app.services.market.prices import PriceService
from app.services.recompute import track_skipped
from app.services.scorecard import ACTIONS, build_scorecard, judged_from_decision
from app.web import templates

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
USER_ACTIONS = {"executed", "skipped", "pending"}
SIDES = {"buy", "sell"}


def _llm():
    return get_llm(settings.anthropic_api_key, settings.consult_model, settings.consult_effort)


def _get(db: Session, decision_id: int) -> Decision:
    d = db.get(Decision, decision_id)
    if d is None or d.source != "consult":
        raise HTTPException(404)
    return d


def _num(raw: str) -> float | None:
    cleaned = raw.replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    try:
        return float(Decimal(cleaned))
    except InvalidOperation:
        return None


@router.get("/consult", response_class=HTMLResponse)
def consult_home(
    request: Request, db: DB, ticker: str = "", side: str = "buy", error: str | None = None
):
    recent = db.scalars(
        select(Decision).where(Decision.source == "consult").order_by(Decision.id.desc()).limit(20)
    ).all()
    fill_count = db.scalar(select(func.count()).select_from(Fill)) or 0
    untagged = db.scalar(
        select(func.count()).select_from(Decision).where(Decision.tagged_at.is_(None))
    )
    return templates.TemplateResponse(
        request,
        "consult/home.html",
        {
            "recent": recent,
            "fill_count": fill_count,
            "untagged": untagged or 0,
            "llm_enabled": _llm() is not None,
            "ticker": ticker,
            "side": side if side in SIDES else "buy",
            "held": held_tickers(db),
            "error": error,
            "VERDICTS": VERDICTS,
        },
    )


@router.post("/consult")
def consult_start(
    request: Request, db: DB, ticker: Annotated[str, Form()], side: Annotated[str, Form()] = "buy"
):
    symbol = ticker.strip().upper()
    if side not in SIDES:
        raise HTTPException(400)
    if not TICKER.match(symbol):
        return consult_home(request, db, ticker, side, error="티커 형식이 아닙니다 (예: IONQ)")
    if existing := open_consult(db, symbol, side, date.today()):
        return RedirectResponse(f"/consult/{existing.id}", status_code=303)
    try:
        d = start_consult(db, symbol, PriceService(db), side)
    except ValueError as e:
        db.rollback()
        return consult_home(request, db, ticker, side, error=str(e))
    db.commit()
    return RedirectResponse(f"/consult/{d.id}", status_code=303)


@router.get("/consult/{decision_id}", response_class=HTMLResponse)
def consult_page(
    request: Request,
    db: DB,
    decision_id: int,
    error: str | None = None,
    notice: str | None = None,
):
    d = _get(db, decision_id)
    if d.verdict is None:
        suggested, emotion = suggest_tags(d.reason_text or "", d.side)
        return templates.TemplateResponse(
            request,
            "consult/talk.html",
            {
                "d": d,
                "messages": visible_messages(d),
                "llm_enabled": _llm() is not None,
                "suggested": suggested,
                "suggested_emotion": emotion,
                "error": None,
            },
        )
    detail = d.verdict_detail or {}
    ids = [m["id"] for m in detail.get("matches", [])]
    by_id = {x.id: x for x in db.scalars(select(Decision).where(Decision.id.in_(ids)))}
    matches = [(m, by_id[m["id"]]) for m in detail.get("matches", []) if m["id"] in by_id]
    fills = sorted(d.fills, key=lambda f: (f.trade_date, f.seq, f.id))
    return templates.TemplateResponse(
        request,
        "consult/verdict.html" if d.side == "buy" else "consult/sell_verdict.html",
        {
            "d": d,
            "detail": detail,
            "matches": matches,
            "fills": fills,
            "error": error,
            "notice": notice,
            "VERDICTS": VERDICTS,
        },
    )


@router.post("/consult/{decision_id}/delete")
def consult_delete(db: DB, decision_id: int):
    """잘못 시작했거나 그만둔 상담을 지운다. 실제 체결이 붙은 결정은 매매 기록이므로 못 지운다."""
    d = _get(db, decision_id)
    if d.fills:
        raise HTTPException(400, "체결이 연결된 상담은 지울 수 없어요.")
    db.delete(d)
    db.commit()
    return RedirectResponse("/consult", status_code=303)


@router.post("/consult/{decision_id}/chat", response_class=HTMLResponse)
def consult_chat(request: Request, db: DB, decision_id: int, message: Annotated[str, Form()]):
    d = _get(db, decision_id)
    llm = _llm()
    text = message.strip()
    if llm is None or d.verdict is not None or not text:
        raise HTTPException(400)
    try:
        done = chat(db, d, llm, text)
    except ConsultError as e:
        db.rollback()
        db.refresh(d)
        return templates.TemplateResponse(
            request,
            "consult/_messages.html",
            {"d": d, "messages": visible_messages(d), "error": str(e)},
        )
    db.commit()
    if done:
        return Response(headers={"HX-Redirect": f"/consult/{d.id}"})
    return templates.TemplateResponse(
        request, "consult/_messages.html", {"d": d, "messages": visible_messages(d), "error": None}
    )


@router.post("/consult/{decision_id}/suggest", response_class=HTMLResponse)
def consult_suggest(
    request: Request,
    db: DB,
    decision_id: int,
    reason_text: Annotated[str, Form()] = "",
    reasons: Annotated[list[str] | None, Form()] = None,
    emotion: Annotated[str, Form()] = "unknown",
):
    """이유를 적고 나면 키워드로 태그를 미리 골라 준다 (API 키 없이 동작). 이미 고른 건 유지."""
    d = _get(db, decision_id)
    suggested, suggested_emotion = suggest_tags(reason_text, d.side)
    return templates.TemplateResponse(
        request,
        "consult/_chips.html",
        {
            "d": d,
            "suggested": [*(reasons or []), *suggested],
            "suggested_emotion": emotion if emotion != "unknown" else suggested_emotion,
        },
    )


@router.post("/consult/{decision_id}/form", response_class=HTMLResponse)
def consult_form(
    request: Request,
    db: DB,
    decision_id: int,
    reason_text: Annotated[str, Form()] = "",
    reasons: Annotated[list[str] | None, Form()] = None,
    emotion: Annotated[str, Form()] = "unknown",
    conviction: Annotated[int, Form()] = 3,
    target_price: Annotated[str, Form()] = "",
    stop_price: Annotated[str, Form()] = "",
    horizon_days: Annotated[str, Form()] = "",
    avg_cost: Annotated[str, Form()] = "",
):
    d = _get(db, decision_id)
    if d.verdict is not None:
        return RedirectResponse(f"/consult/{d.id}", status_code=303)
    base = {
        "reason_tags": reasons or ["unknown"],
        "emotion": emotion,
        "conviction": conviction,
        "reason_summary": reason_text,
    }
    try:
        record: Record
        if d.side == "sell":
            record = SellRecordInput.model_validate({**base, "avg_cost": _num(avg_cost)})
        else:
            record = RecordInput.model_validate(
                {
                    **base,
                    "target_price": _num(target_price),
                    "stop_price": _num(stop_price),
                    "horizon_days": int(horizon_days) if horizon_days.strip().isdigit() else None,
                }
            )
    except ValidationError as e:
        suggested, emo = suggest_tags(reason_text, d.side)
        return templates.TemplateResponse(
            request,
            "consult/talk.html",
            {
                "d": d,
                "messages": visible_messages(d),
                "llm_enabled": _llm() is not None,
                "suggested": suggested,
                "suggested_emotion": emo,
                "error": f"입력값을 확인하세요: {e.errors()[0]['msg']}",
            },
            status_code=422,
        )
    apply_record(d, record, reason_text)
    judge(db, d)
    db.commit()
    return RedirectResponse(f"/consult/{d.id}", status_code=303)


@router.post("/consult/{decision_id}/rejudge")
def consult_rejudge(db: DB, decision_id: int):
    d = _get(db, decision_id)
    if d.tagged_at is None:
        raise HTTPException(400)
    judge(db, d)
    db.commit()
    return RedirectResponse(f"/consult/{d.id}", status_code=303)


@router.post("/consult/{decision_id}/action")
def consult_action(db: DB, decision_id: int, action: Annotated[str, Form()]):
    d = _get(db, decision_id)
    if action not in USER_ACTIONS:
        raise HTTPException(400)
    if not d.fills:  # CSV로 실제 체결이 연결된 뒤에는 '샀음'이 사실이므로 바꾸지 않는다
        d.user_action = action
        if action == "skipped":
            # 시세 조회가 실패해도 선택은 저장한다 (다음 재계산 때 다시 시도)
            track_skipped(db, PriceService(db), ids=[d.id])
        elif (d.outcome or {}).get("status") == "skipped":
            d.outcome = None
    db.commit()
    # '샀어요/팔았어요'를 고르면 체결을 바로 적을 수 있게 그 칸으로 내려간다
    anchor = "#fills" if action == "executed" and not d.fills else ""
    return RedirectResponse(f"/consult/{d.id}{anchor}", status_code=303)


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request, db: DB):
    decisions = db.scalars(select(Decision)).all()
    rows = backtest([case_from_decision(d) for d in decisions if d.side == "buy"])
    sell_rows = backtest([case_from_decision(d) for d in decisions if d.side == "sell"])
    # 상담 성적표: 당시 화면에 떴던 판정(verdict, 그때 데이터로 고정) vs 실제 결과
    consults = [d for d in decisions if d.source == "consult"]
    judged = [judged_from_decision(d) for d in consults if d.verdict]
    unfinished = sorted(
        (d for d in consults if d.verdict is None), key=lambda d: (d.decided_on, d.id), reverse=True
    )
    return templates.TemplateResponse(
        request,
        "consult/backtest.html",
        {
            "rows": rows,
            "summary": summarize(rows),
            "sell_summary": summarize(sell_rows),
            "cards": [build_scorecard(judged, side) for side in ("buy", "sell")],
            "unfinished": unfinished,
            "VERDICTS": VERDICTS,
            "ACTIONS": ACTIONS,
            "EARLY_EXIT": EARLY_EXIT,
        },
    )
