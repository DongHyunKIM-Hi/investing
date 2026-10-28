"""매칭 엔진이 다루는 결정 1건의 형태. DB 모델(Decision)과 분리해 순수 함수로 테스트한다.

매수: 청산되면 승패가 난다 (실현 수익률 > 0).
매도: 판 뒤 20거래일이 지나면 승패가 난다. 그동안 내려갔거나 그대로면 '잘 판' 것(win),
ret은 '판 덕에 피한 손익' = -(판 뒤 20일 수익률). 그래서 매수와 같은 판정 함수를 쓴다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from app.services.outcomes import plan_status

if TYPE_CHECKING:
    from app.models import Decision

UNKNOWN = "unknown"
# 예전 매도 결과에 확정일(post_exit_on)이 없을 때: 20거래일 ≈ 28일, 넉넉히 잡는다
SELL_RESOLVE_FALLBACK = timedelta(days=31)


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
    side: str = "buy"
    # 상담 판정을 따랐나. 추천→실행, 비추천→보류만 따진다. 그 외(신중·CSV·미정)는 None
    followed: bool | None = None

    def resolved_before(self, day: date) -> bool:
        """day 시점에 이미 승패가 확정돼 있었는가."""
        return self.win is not None and self.closed_on is not None and self.closed_on < day


def case_from_decision(d: "Decision") -> Case:
    if d.side == "sell":
        return sell_case_from_decision(d)
    o = d.outcome or {}
    closed = o.get("status") == "closed"
    has_stop: bool | None = None
    plan: str | None = None
    if d.source != "csv":  # 상담에서 만든 결정만 계획(손절가·기간) 여부를 안다
        has_stop = d.stop_price is not None
        # 손절가는 상담 당시 가격, outcome의 매수가는 분할 보정된 지금 기준 → 같은 기준으로 맞춘다
        k = o.get("plan_factor") or 1.0
        stop = float(d.stop_price) / k if d.stop_price is not None else None
        plan = plan_status(o, stop, d.horizon_days)
    return Case(
        id=d.id,
        ticker=d.ticker,
        decided_on=d.decided_on,
        tags=_tags(d),
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
        followed=_followed(d),
    )


def _tags(d: "Decision") -> frozenset[str]:
    return frozenset(t for t in d.reason_tags or [] if t != UNKNOWN)


def _followed(d: "Decision") -> bool | None:
    if d.verdict not in ("recommend", "against") or d.user_action not in ("executed", "skipped"):
        return None
    return (d.verdict == "recommend") == (d.user_action == "executed")


def sell_case_from_decision(d: "Decision") -> Case:
    o = d.outcome or {}
    ctx = dict(d.context or {})
    # 팔 때 포지션 손익: 상담 중인 매도는 context에, 체결된 매도는 실현 수익률로 안다
    if o.get("realized_return") is not None:
        ctx["position_return"] = o["realized_return"]
    post = o.get("post_exit_20d") if o.get("status") != "skipped" else None
    resolved_on = None
    if post is not None:
        resolved_on = (
            date.fromisoformat(o["post_exit_on"])
            if o.get("post_exit_on")
            else d.decided_on + SELL_RESOLVE_FALLBACK
        )
    return Case(
        id=d.id,
        ticker=d.ticker,
        decided_on=d.decided_on,
        tags=_tags(d),
        emotion=d.emotion if d.emotion and d.emotion != UNKNOWN else None,
        context=ctx,
        closed_on=resolved_on,
        win=post <= 0 if post is not None else None,
        ret=-post if post is not None else None,
        post_exit_20d=post,
        holding_days=o.get("holding_days"),
        side="sell",
        followed=_followed(d),
    )
