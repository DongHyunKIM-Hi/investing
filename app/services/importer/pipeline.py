"""파싱된 체결 → DB 저장 → 같은 날·같은 종목·같은 방향 체결을 하나의 결정(Decision)으로 묶는다.

상담 뒤 직접 기록한 수동 체결이 있는 결정에 CSV 체결이 들어오면, CSV가 증권사 원본이므로
수동 체결은 지우고 CSV 체결로 대체한다 (같은 거래를 두 번 세지 않게).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Decision, Fill
from app.services.importer.csv_parser import ParsedFill
from app.services.manual import MANUAL_PREFIX, is_manual

# 상담일(한국 날짜)과 체결일(미국 날짜)이 다르거나, 상담 후 하루이틀 뒤 사는 경우를 잇는다
CONSULT_LINK_DAYS = 3


@dataclass
class ImportSummary:
    new_fills: int = 0
    duplicates: int = 0
    new_decisions: int = 0
    linked_decisions: int = 0  # 상담에서 미리 만든 결정에 연결된 수
    replaced_manual: int = 0  # CSV 체결로 대체된 수동 체결 수
    tickers: set[str] = field(default_factory=set)


def import_fills(db: Session, parsed: list[ParsedFill]) -> ImportSummary:
    summary = ImportSummary()
    keys = [p.dedup_key for p in parsed]
    existing = set(db.scalars(select(Fill.dedup_key).where(Fill.dedup_key.in_(keys))))

    groups: dict[tuple[str, str, date], list[Fill]] = defaultdict(list)
    for p in parsed:
        if p.dedup_key in existing:
            summary.duplicates += 1
            continue
        fill = Fill(
            ticker=p.ticker,
            side=p.side,
            trade_date=p.trade_date,
            seq=p.seq,
            price=p.price,
            quantity=p.quantity,
            fee=p.fee,
            dedup_key=p.dedup_key,
        )
        db.add(fill)
        groups[(p.ticker, p.side, p.trade_date)].append(fill)
        summary.new_fills += 1
        summary.tickers.add(p.ticker)

    for (ticker, side, day), fills in groups.items():
        decision = (
            _manual_match(db, ticker, side, day)
            or db.scalars(
                select(Decision).where(
                    Decision.ticker == ticker,
                    Decision.side == side,
                    Decision.decided_on == day,
                    Decision.user_action != "skipped",
                )
            ).first()
            or _open_consult(db, ticker, side, day)
        )
        if decision is None:
            decision = Decision(
                ticker=ticker,
                side=side,
                source="csv",
                decided_on=day,
                reason_tags=[],
                similar_ids=[],
            )
            db.add(decision)
            summary.new_decisions += 1
        else:
            if decision.source != "csv":
                decision.user_action = "executed"
                summary.linked_decisions += 1
            for manual in [f for f in decision.fills if is_manual(f)]:
                db.delete(manual)
                summary.replaced_manual += 1
            summary.tickers.add(ticker)
        for fill in fills:
            fill.decision = decision

    db.flush()
    return summary


def _manual_match(db: Session, ticker: str, side: str, day: date) -> Decision | None:
    """같은 날 같은 종목·방향으로 직접 기록한 체결이 있는 결정. 그 거래가 CSV로 온 것이다."""
    return db.scalars(
        select(Decision)
        .join(Fill, Fill.decision_id == Decision.id)
        .where(
            Fill.ticker == ticker,
            Fill.side == side,
            Fill.trade_date == day,
            Fill.dedup_key.like(f"{MANUAL_PREFIX}%"),
        )
        .order_by(Decision.id.desc())
    ).first()


def _open_consult(db: Session, ticker: str, side: str, day: date) -> Decision | None:
    """체결 즈음에 한, 아직 CSV 체결이 연결되지 않은 상담 결정 (가장 최근 것).

    상담은 한국 날짜, 체결은 미국 날짜라 상담일이 체결일보다 하루 뒤일 수 있다.
    직접 기록한 수동 체결만 붙어 있는 상담은 아직 CSV와 이어지지 않은 것으로 본다.
    """
    candidates = db.scalars(
        select(Decision)
        .where(
            Decision.ticker == ticker,
            Decision.side == side,
            Decision.source == "consult",
            Decision.user_action != "skipped",
            Decision.decided_on.between(
                day - timedelta(days=CONSULT_LINK_DAYS), day + timedelta(days=1)
            ),
        )
        .order_by(Decision.decided_on.desc(), Decision.id.desc())
    ).all()
    return next((d for d in candidates if all(is_manual(f) for f in d.fills)), None)
