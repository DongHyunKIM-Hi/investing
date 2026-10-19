"""파싱된 체결 → DB 저장 → 같은 날·같은 종목·같은 방향 체결을 하나의 결정(Decision)으로 묶는다."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Decision, Fill
from app.services.importer.csv_parser import ParsedFill

# 상담일(한국 날짜)과 체결일(미국 날짜)이 다르거나, 상담 후 하루이틀 뒤 사는 경우를 잇는다
CONSULT_LINK_DAYS = 3


@dataclass
class ImportSummary:
    new_fills: int = 0
    duplicates: int = 0
    new_decisions: int = 0
    linked_decisions: int = 0  # 상담에서 미리 만든 결정에 연결된 수
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
        decision = db.scalars(
            select(Decision).where(
                Decision.ticker == ticker,
                Decision.side == side,
                Decision.decided_on == day,
                Decision.user_action != "skipped",
            )
        ).first() or _open_consult(db, ticker, side, day)
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
        elif decision.source != "csv":
            decision.user_action = "executed"
            summary.linked_decisions += 1
        for fill in fills:
            fill.decision = decision

    db.flush()
    return summary


def _open_consult(db: Session, ticker: str, side: str, day: date) -> Decision | None:
    """체결 직전 며칠 안에 한, 아직 체결이 연결되지 않은 상담 결정 (가장 최근 것)."""
    return db.scalars(
        select(Decision)
        .where(
            Decision.ticker == ticker,
            Decision.side == side,
            Decision.source == "consult",
            Decision.user_action != "skipped",
            Decision.decided_on.between(day - timedelta(days=CONSULT_LINK_DAYS), day),
            ~Decision.fills.any(),
        )
        .order_by(Decision.decided_on.desc(), Decision.id.desc())
    ).first()
