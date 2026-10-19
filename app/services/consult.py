"""매수 전 상담: 상담 시작(상황 스냅샷) → 대화/폼으로 계획 기록 → 유사 전적 판정."""

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConsultMessage, Decision
from app.services.engine.case import case_from_decision
from app.services.engine.checks import Plan, check_plan
from app.services.engine.similarity import find_similar
from app.services.engine.verdict import decide, patterns
from app.services.llm.consult import (
    ConsultError,
    ConsultLLM,
    RecordInput,
    situation_text,
    tool_result,
)
from app.services.market.prices import BENCHMARK, PriceService
from app.services.market.snapshot import build_snapshot
from app.taxonomy import BUY_REASONS, EMOTIONS

LOOKBACK = timedelta(days=400)


def start_consult(db: Session, ticker: str, prices: PriceService) -> Decision:
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

    decision = Decision(
        ticker=ticker,
        side="buy",
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


def _money(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def apply_record(d: Decision, record: RecordInput, reason_text: str | None = None) -> None:
    tags = [t for t in dict.fromkeys(record.reason_tags) if t in BUY_REASONS]
    if len(tags) > 1 and "unknown" in tags:
        tags.remove("unknown")
    d.reason_tags = tags or ["unknown"]
    d.emotion = record.emotion if record.emotion in EMOTIONS else "unknown"
    d.conviction = record.conviction
    d.target_price = _money(record.target_price)
    d.stop_price = _money(record.stop_price)
    d.horizon_days = record.horizon_days
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


def judge(db: Session, d: Decision) -> None:
    """과거 매수 결정 전체와 비교해 판정하고, 그 근거를 verdict_detail에 고정해 둔다."""
    others = db.scalars(select(Decision).where(Decision.side == "buy", Decision.id != d.id)).all()
    history = [case_from_decision(o) for o in others]
    query = case_from_decision(d)

    matches = find_similar(query, history)
    verdict = decide(matches)
    flags = check_plan(plan_of(d), d.context or {}, d.decided_on)

    d.verdict = verdict.label
    d.win_prob = verdict.win_prob
    d.similar_ids = [m.case.id for m in matches]
    d.verdict_detail = {
        "judged_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "history_size": sum(1 for c in history if c.resolved_before(d.decided_on)),
        "verdict": verdict.to_dict(),
        "matches": [
            {"id": m.case.id, "similarity": m.similarity, "parts": dict(m.parts)} for m in matches
        ],
        "patterns": [p.to_dict() for p in patterns(query, history)],
        "flags": [asdict(f) for f in flags],
    }


# ---- 대화형 상담 ----


def _append(db: Session, d: Decision, role: str, content: list[dict[str, Any]]) -> None:
    db.add(ConsultMessage(decision=d, role=role, content=content))
    db.flush()


def chat(db: Session, d: Decision, llm: ConsultLLM, user_text: str) -> bool:
    """사용자 발화 1개 처리. 상담이 끝나(기록 완료) 판정까지 했으면 True."""
    _append(db, d, "user", [{"type": "text", "text": user_text}])
    situation = situation_text(d.ticker, d.context or {})

    # 도구 입력이 검증에 실패하면 오류를 돌려주고 한 번만 다시 받는다
    for _ in range(2):
        turn = llm.reply(situation, [{"role": m.role, "content": m.content} for m in d.messages])
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
