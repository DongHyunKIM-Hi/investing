"""매칭 엔진이 다루는 결정 1건의 형태. DB 모델(Decision)과 분리해 순수 함수로 테스트한다."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from app.services.outcomes import plan_status

if TYPE_CHECKING:
    from app.models import Decision

UNKNOWN = "unknown"


@dataclass(frozen=True)
class Case:
    id: int
    ticker: str
    decided_on: date
    tags: frozenset[str] = frozenset()  # 'unknown' 제외. 비어 있으면 이유를 모르는 것
    emotion: str | None = None  # 'unknown'이면 None
    has_stop: bool | None = None  # 손절 계획 유무. CSV에서 온 결정은 알 수 없음(None)
    context: Mapping[str, Any] = field(default_factory=dict)
    # 결과: 청산된 매수만 승패가 있다
    closed_on: date | None = None
    win: bool | None = None
    ret: float | None = None
    excess: float | None = None
    max_dd: float | None = None
    post_exit_20d: float | None = None
    holding_days: int | None = None
    plan: str | None = None  # kept | broken | none. CSV에서 온 결정이나 청산 전이면 None

    def resolved_before(self, day: date) -> bool:
        """day 시점에 이미 승패가 확정돼 있었는가."""
        return self.win is not None and self.closed_on is not None and self.closed_on < day


def case_from_decision(d: "Decision") -> Case:
    o = d.outcome or {}
    closed = o.get("status") == "closed"
    has_stop: bool | None = None
    plan: str | None = None
    if d.source != "csv":  # 상담에서 만든 결정만 계획(손절가·기간) 여부를 안다
        has_stop = d.stop_price is not None
        stop = float(d.stop_price) if d.stop_price is not None else None
        plan = plan_status(o, stop, d.horizon_days)
    return Case(
        id=d.id,
        ticker=d.ticker,
        decided_on=d.decided_on,
        tags=frozenset(t for t in d.reason_tags or [] if t != UNKNOWN),
        emotion=d.emotion if d.emotion and d.emotion != UNKNOWN else None,
        has_stop=has_stop,
        context=d.context or {},
        closed_on=date.fromisoformat(o["closed_on"]) if closed and o.get("closed_on") else None,
        win=o.get("win") if closed else None,
        ret=o.get("realized_return") if closed else None,
        excess=o.get("excess_return") if closed else None,
        max_dd=o.get("max_drawdown") if closed else None,
        post_exit_20d=o.get("post_exit_20d") if closed else None,
        holding_days=o.get("holding_days") if closed else None,
        plan=plan,
    )
