"""보유 종목 계획 점검: 살 때 남긴 목표가·손절가·기간을 지금 가격과 대조한다 (순수 함수).

판정 엔진이 '사기 전'을 다룬다면, 이건 '산 뒤'를 다룬다. 손절가를 지나쳤는데 들고 있거나
목표가에 왔는데 안 파는 것이 "그때 팔걸"의 주된 원인이라 매일 보는 화면에서 바로 짚는다.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

STOP_NEAR = 0.03  # 손절가 위 3% 안이면 '근접'
TARGET_NEAR = 0.03  # 목표가 아래 3% 안이면 '근접'


@dataclass(frozen=True)
class Alert:
    level: str  # high | mid | good | info
    text: str


@dataclass(frozen=True)
class Watch:
    ticker: str
    quantity: float
    avg_cost: float
    last_close: float | None
    as_of: date | None
    opened_on: date
    held_days: int
    position_return: float | None
    plan: (
        dict[str, Any] | None
    )  # 살 때 상담의 계획 (decision_id, target_price, stop_price, horizon_days...)
    alerts: list[Alert] = field(default_factory=list)

    @property
    def urgency(self) -> int:
        order = {"high": 0, "good": 1, "mid": 2, "info": 3}
        return min((order[a.level] for a in self.alerts), default=4)


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def check_position(
    ticker: str,
    quantity: float,
    avg_cost: float,
    opened_on: date,
    last_close: float | None,
    as_of: date | None,
    plan: dict[str, Any] | None,
    today: date,
) -> Watch:
    alerts: list[Alert] = []
    held = (today - opened_on).days
    ret = last_close / avg_cost - 1 if last_close and avg_cost else None

    if plan is None:
        alerts.append(Alert("info", "상담 없이 산 종목이에요. 언제 팔지(목표가·손절가)가 없어요."))
    else:
        stop, target, horizon = (
            plan.get("stop_price"),
            plan.get("target_price"),
            plan.get("horizon_days"),
        )
        if last_close is not None:
            if stop is not None:
                if last_close <= stop:
                    alerts.append(
                        Alert(
                            "high",
                            f"손절가 ${stop:g} 아래예요 (현재 ${last_close:.2f}, "
                            f"{_pct(last_close / stop - 1)}). 계획대로면 이미 팔았어야 해요.",
                        )
                    )
                elif last_close <= stop * (1 + STOP_NEAR):
                    alerts.append(
                        Alert("mid", f"손절가 ${stop:g}에 가까워요 (현재 ${last_close:.2f}).")
                    )
            if target is not None:
                if last_close >= target:
                    alerts.append(
                        Alert(
                            "good",
                            f"목표가 ${target:g}에 도달했어요 (현재 ${last_close:.2f}). "
                            "팔거나, 왜 더 들고 갈지 다시 정하세요.",
                        )
                    )
                elif last_close >= target * (1 - TARGET_NEAR):
                    alerts.append(
                        Alert("mid", f"목표가 ${target:g}에 가까워요 (현재 ${last_close:.2f}).")
                    )
        if stop is None:
            alerts.append(Alert("mid", "손절가 없이 들고 있어요. 지금이라도 정해 두세요."))
        # 기간은 계획을 세운 상담일부터 센다 (더 오래된 lot이 섞여 있을 수 있어서)
        since_plan = (
            (today - date.fromisoformat(plan["decided_on"])).days
            if plan.get("decided_on")
            else held
        )
        if horizon is not None and since_plan > horizon:
            alerts.append(
                Alert(
                    "mid",
                    f"계획한 보유 기간 {horizon}일을 넘겼어요 (현재 {since_plan}일). "
                    "산 이유가 아직 유효한지 확인하세요.",
                )
            )
        if ret is not None and ret <= -0.20 and stop is None:
            alerts.append(Alert("high", f"평가 손실 {_pct(ret)}인데 손절 기준이 없어요."))

    if plan is None and ret is not None and ret <= -0.20:
        alerts.append(
            Alert("high", f"평가 손실 {_pct(ret)}. 어디까지 버틸지 정하지 않으면 물리기 쉬워요.")
        )

    return Watch(
        ticker=ticker,
        quantity=quantity,
        avg_cost=avg_cost,
        last_close=last_close,
        as_of=as_of,
        opened_on=opened_on,
        held_days=held,
        position_return=round(ret, 4) if ret is not None else None,
        plan=plan,
        alerts=alerts,
    )


def sort_watches(watches: list[Watch]) -> list[Watch]:
    """급한 것(손절 이탈·목표 도달)이 위로, 그다음 평가 손실이 큰 순."""
    return sorted(
        watches,
        key=lambda w: (w.urgency, w.position_return if w.position_return is not None else 0),
    )
