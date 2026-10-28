"""매수·매도 전 상담: 상담 시작(상황 스냅샷) → 대화/폼으로 이유·계획 기록 → 유사 전적 판정.

매도 상담은 시작할 때 지금 보유 중인 lot(평단가·평가 손익)과 살 때 남긴 계획을 context에 담는다.
"""

from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConsultMessage, Decision, Fill
from app.services.engine.case import case_from_decision
from app.services.engine.checks import Plan, SellPlan, check_plan, check_sell
from app.services.engine.similarity import find_similar
from app.services.engine.summary import headline
from app.services.engine.verdict import decide, patterns
from app.services.importer.splits import Split, split_factor
from app.services.llm.consult import (
    ConsultError,
    ConsultLLM,
    Record,
    RecordInput,
    situation_text,
    tool_result,
)
from app.services.market.prices import BENCHMARK, PriceService
from app.services.market.snapshot import build_snapshot
from app.services.position import PricedFill, manual_position, open_lots, summarize_position
from app.taxonomy import EMOTIONS, reasons_for

LOOKBACK = timedelta(days=400)


def open_consult(db: Session, ticker: str, side: str, today: date) -> Decision | None:
    """오늘 시작해 아직 판정 전인 같은 종목·방향 상담. 있으면 새로 만들지 않고 이어간다."""
    return db.scalars(
        select(Decision)
        .where(
            Decision.ticker == ticker,
            Decision.side == side,
            Decision.source == "consult",
            Decision.decided_on == today,
            Decision.verdict.is_(None),
        )
        .order_by(Decision.id.desc())
    ).first()


def start_consult(db: Session, ticker: str, prices: PriceService, side: str = "buy") -> Decision:
    """티커의 현재 상황을 스냅샷으로 찍고 '고민 중' 결정을 만든다."""
    today = date.today()
    bars = prices.bars(ticker, today - LOOKBACK, today)
    if bars.empty:
        raise ValueError(f"{ticker} 시세를 찾을 수 없습니다. 티커를 확인하세요.")
    spy = prices.bars(BENCHMARK, today - LOOKBACK, today)["adj_close"]

    ctx: dict[str, Any] = build_snapshot(bars["adj_close"], spy, today) or {}
    past_close = bars["close"][bars.index < today]
    if len(past_close):
        ctx["last_close"] = round(float(past_close.iloc[-1]), 4)
    if earnings := prices.provider.next_earnings(ticker):
        ctx["next_earnings"] = earnings.isoformat()

    if side == "sell":
        ctx.update(holding_of(db, ticker, ctx.get("last_close"), today, prices.splits(ticker)))

    decision = Decision(
        ticker=ticker,
        side=side,
        source="consult",
        decided_on=today,
        reason_tags=[],
        similar_ids=[],
        context=ctx,
        user_action="pending",
    )
    db.add(decision)
    db.flush()
    return decision


def _priced(fills: Iterable[Fill], splits: Sequence[Split]) -> list[PricedFill]:
    """분할 보정한 체결 (지금 기준 수량·가격)."""
    out = []
    for f in fills:
        k = split_factor(f.trade_date, splits)
        out.append(PricedFill(f.id, f.side, f.trade_date, f.seq, f.price / k, f.quantity * k))
    return out


def holding_of(
    db: Session, ticker: str, last_close: float | None, today: date, splits: Sequence[Split]
) -> dict[str, Any]:
    """지금 보유 중인 lot 요약 + 그 lot을 살 때 상담에서 남긴 계획 (가장 최근 것). 없으면 {}."""
    fills = db.scalars(select(Fill).where(Fill.ticker == ticker)).all()
    lots = open_lots(_priced(fills, splits))
    pos = summarize_position(lots, last_close, today)
    if pos is None:
        return {}
    lot_ids = {lot.buy_fill_id for lot in lots}
    planned = [
        f.decision
        for f in fills
        if f.id in lot_ids and f.decision is not None and f.decision.source == "consult"
    ]
    if planned:
        b = max(planned, key=lambda d: (d.decided_on, d.id))
        pos["buy_plan"] = {
            "decision_id": b.id,
            "decided_on": b.decided_on.isoformat(),
            "reason_tags": [t for t in b.reason_tags if t != "unknown"],
            "reason_text": b.reason_text,
            "target_price": float(b.target_price) if b.target_price is not None else None,
            "stop_price": float(b.stop_price) if b.stop_price is not None else None,
            "horizon_days": b.horizon_days,
        }
    return pos


def _money(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def apply_record(d: Decision, record: Record, reason_text: str | None = None) -> None:
    allowed = reasons_for(d.side)
    tags = [t for t in dict.fromkeys(record.reason_tags) if t in allowed]
    if len(tags) > 1 and "unknown" in tags:
        tags.remove("unknown")
    d.reason_tags = tags or ["unknown"]
    d.emotion = record.emotion if record.emotion in EMOTIONS else "unknown"
    d.conviction = record.conviction
    if isinstance(record, RecordInput):
        d.target_price = _money(record.target_price)
        d.stop_price = _money(record.stop_price)
        d.horizon_days = record.horizon_days
    else:
        ctx = d.context or {}
        if record.avg_cost is not None and ctx.get("avg_cost") is None:
            # JSONB는 새 dict를 넣어야 변경이 저장된다
            d.context = {**ctx, **manual_position(record.avg_cost, ctx.get("last_close"))}
    d.reason_text = (reason_text or record.reason_summary).strip() or None
    d.tagged_at = datetime.now(UTC)


def plan_of(d: Decision) -> Plan:
    return Plan(
        tags=frozenset(t for t in d.reason_tags if t != "unknown"),
        emotion=d.emotion if d.emotion != "unknown" else None,
        conviction=d.conviction,
        target_price=float(d.target_price) if d.target_price is not None else None,
        stop_price=float(d.stop_price) if d.stop_price is not None else None,
        horizon_days=d.horizon_days,
    )


def sell_plan_of(d: Decision) -> SellPlan:
    ctx = d.context or {}
    bp = ctx.get("buy_plan") or {}
    return SellPlan(
        tags=frozenset(t for t in d.reason_tags if t != "unknown"),
        emotion=d.emotion if d.emotion != "unknown" else None,
        position_return=ctx.get("position_return"),
        held_days=ctx.get("held_days"),
        buy_target=bp.get("target_price"),
        buy_stop=bp.get("stop_price"),
        buy_horizon=bp.get("horizon_days"),
    )


def judge(db: Session, d: Decision) -> None:
    """같은 쪽(매수/매도) 과거 결정 전체와 비교해 판정하고, 근거를 verdict_detail에 고정한다."""
    others = db.scalars(select(Decision).where(Decision.side == d.side, Decision.id != d.id)).all()
    history = [case_from_decision(o) for o in others]
    query = case_from_decision(d)

    matches = find_similar(query, history)
    verdict = decide(matches)
    if d.side == "sell":
        flags = check_sell(sell_plan_of(d), d.context or {})
    else:
        flags = check_plan(plan_of(d), d.context or {}, d.decided_on)

    pats = patterns(query, history)
    d.verdict = verdict.label
    d.win_prob = verdict.win_prob
    d.similar_ids = [m.case.id for m in matches]
    d.verdict_detail = {
        "judged_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "headline": headline(verdict, pats, flags, d.side),
        "history_size": sum(1 for c in history if c.resolved_before(d.decided_on)),
        "verdict": verdict.to_dict(),
        "matches": [
            {"id": m.case.id, "similarity": m.similarity, "parts": dict(m.parts)} for m in matches
        ],
        "patterns": [p.to_dict() for p in pats],
        "flags": [asdict(f) for f in flags],
    }


# ---- 대화형 상담 ----


def _append(db: Session, d: Decision, role: str, content: list[dict[str, Any]]) -> None:
    db.add(ConsultMessage(decision=d, role=role, content=content))
    db.flush()


def chat(db: Session, d: Decision, llm: ConsultLLM, user_text: str) -> bool:
    """사용자 발화 1개 처리. 상담이 끝나(기록 완료) 판정까지 했으면 True."""
    _append(db, d, "user", [{"type": "text", "text": user_text}])
    situation = situation_text(d.ticker, d.context or {}, d.side)

    # 도구 입력이 검증에 실패하면 오류를 돌려주고 한 번만 다시 받는다
    for _ in range(2):
        history = [{"role": m.role, "content": m.content} for m in d.messages]
        turn = llm.reply(situation, history, d.side)
        _append(db, d, "assistant", turn.content)
        if turn.tool_use_id is None:
            return False
        if turn.record is not None:
            done = tool_result(turn.tool_use_id, "기록했습니다. 판정 화면으로 이동합니다.")
            _append(db, d, "user", done)
            apply_record(d, turn.record)
            judge(db, d)
            return True
        _append(db, d, "user", tool_result(turn.tool_use_id, turn.error or "잘못된 입력", True))
    raise ConsultError("기록 형식이 계속 맞지 않아요. 아래 폼으로 직접 입력해 주세요.")


def visible_messages(d: Decision) -> list[dict[str, str]]:
    """화면용: 텍스트가 있는 말풍선만."""
    out: list[dict[str, str]] = []
    for m in d.messages:
        text = "\n\n".join(
            b["text"] for b in m.content if b.get("type") == "text" and b.get("text")
        )
        if text:
            out.append({"role": m.role, "text": text})
    return out


def held_tickers(db: Session) -> list[tuple[str, float]]:
    """지금 보유 중인 종목과 수량 (CSV 체결 기준)."""
    fills = db.scalars(select(Fill)).all()
    by_ticker: dict[str, list[Fill]] = {}
    for f in fills:
        by_ticker.setdefault(f.ticker, []).append(f)
    prices = PriceService(db)
    held = []
    for ticker, rows in sorted(by_ticker.items()):
        # 화면 목록이라 시세를 새로 받지 않고 캐시된 분할 이력만 쓴다
        lots = open_lots(_priced(rows, prices.cached_splits(ticker)))
        qty = sum(float(lot.quantity) for lot in lots)
        if qty > 0:
            held.append((ticker, qty))
    return held
