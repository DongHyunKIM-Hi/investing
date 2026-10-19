"""매수 계획 점검: 승률과 별개로 "지금 이 결정의 위험 신호"를 짚는다 (순수 함수)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

EARNINGS_SOON_DAYS = 14
MIN_REWARD_RISK = 1.5


@dataclass(frozen=True)
class Plan:
    tags: frozenset[str]
    emotion: str | None
    conviction: int | None
    target_price: float | None
    stop_price: float | None
    horizon_days: int | None


@dataclass(frozen=True)
class Flag:
    level: str  # high | mid
    text: str


def check_plan(plan: Plan, ctx: Mapping[str, Any], today: date) -> list[Flag]:
    flags: list[Flag] = []
    price = ctx.get("last_close")

    if plan.stop_price is None:
        flags.append(
            Flag("high", "손절가 없이 사려고 해요. 어디서 틀렸다고 인정할지 먼저 정하세요.")
        )
    if plan.target_price is None and plan.horizon_days is None:
        flags.append(
            Flag("mid", "목표가도 보유 기간도 없어요. 언제 팔지 모르면 '껄'로 끝나기 쉬워요.")
        )

    if price:
        if plan.stop_price is not None and plan.stop_price >= price:
            msg = f"손절가({plan.stop_price:g})가 현재가({price:.2f}) 이상이에요."
            flags.append(Flag("high", msg))
        if plan.target_price is not None and plan.target_price <= price:
            msg = f"목표가({plan.target_price:g})가 현재가({price:.2f}) 이하예요."
            flags.append(Flag("high", msg))
        if (
            plan.stop_price is not None
            and plan.target_price is not None
            and plan.stop_price < price < plan.target_price
        ):
            ratio = (plan.target_price - price) / (price - plan.stop_price)
            if ratio < MIN_REWARD_RISK:
                flags.append(
                    Flag("mid", f"손익비가 {ratio:.1f} : 1 이에요. 먹을 폭보다 잃을 폭이 커요.")
                )

    ret_5d, ret_20d, rsi = ctx.get("ret_5d"), ctx.get("ret_20d"), ctx.get("rsi14")
    if ret_5d is not None and ret_5d >= 0.15 and "momentum_chase" not in plan.tags:
        flags.append(
            Flag("high", f"직전 5일 {ret_5d * 100:+.0f}% 급등했어요. 사실상 추격 매수 아닌가요?")
        )
    elif ret_20d is not None and ret_20d >= 0.40 and "momentum_chase" not in plan.tags:
        msg = f"직전 20일 {ret_20d * 100:+.0f}% 올랐어요. 이미 많이 오른 자리예요."
        flags.append(Flag("mid", msg))
    if rsi is not None and rsi >= 70:
        flags.append(Flag("mid", f"RSI {rsi:.0f}: 단기 과열 구간이에요."))

    if plan.emotion in ("fomo", "revenge"):
        what = "놓칠까 조급한" if plan.emotion == "fomo" else "손실을 만회하려는"
        msg = f"{what} 마음으로 하는 매수예요. 하루만 미뤄도 손해 볼 건 없어요."
        flags.append(Flag("high", msg))
    if "averaging_down" in plan.tags:
        flags.append(Flag("mid", "물타기예요. 산 이유가 아직 유효한지부터 확인하세요."))
    if "community_tip" in plan.tags and len(plan.tags) == 1:
        flags.append(Flag("mid", "남의 추천만이 이유예요. 내가 직접 확인한 근거가 있나요?"))

    if (earnings := ctx.get("next_earnings")) is not None:
        days = (date.fromisoformat(earnings) - today).days
        # 곧 있거나, 계획한 보유 기간 안에 있으면 짚어 준다
        if 0 <= days <= max(EARNINGS_SOON_DAYS, plan.horizon_days or 0):
            msg = f"실적발표가 {days}일 남았어요 ({earnings}). 보유 중 큰 변동에 대비하세요."
            flags.append(Flag("mid", msg))

    flags.sort(key=lambda f: f.level != "high")
    return flags
