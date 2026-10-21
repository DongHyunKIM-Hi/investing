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
    level: str  # high | mid | good(매도: 계획대로)
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


# ---- 매도 ----

PANIC_DROP_5D = -0.15
TINY_PROFIT = 0.05


@dataclass(frozen=True)
class SellPlan:
    tags: frozenset[str]
    emotion: str | None
    position_return: float | None  # 팔려는 지금 평가 손익. 보유 기록이 없으면 None
    held_days: int | None
    # 이 주식을 살 때 상담에서 남긴 계획 (없으면 None)
    buy_target: float | None = None
    buy_stop: float | None = None
    buy_horizon: int | None = None


def check_sell(plan: SellPlan, ctx: Mapping[str, Any]) -> list[Flag]:
    """매도 점검. level: high(위험) | mid(확인) | good(계획대로 하는 매도)."""
    flags: list[Flag] = []
    price = ctx.get("last_close")
    hit_stop = price is not None and plan.buy_stop is not None and price <= plan.buy_stop
    hit_target = price is not None and plan.buy_target is not None and price >= plan.buy_target

    if hit_stop:
        flags.append(
            Flag(
                "good",
                f"살 때 정한 손절가({plan.buy_stop:g}) 아래예요. 계획대로라면 파는 게 맞아요.",
            )
        )
    elif hit_target:
        flags.append(
            Flag(
                "good",
                f"살 때 정한 목표가({plan.buy_target:g})에 닿았어요. 계획대로 하는 매도예요.",
            )
        )
    elif plan.emotion == "fear" or "fear" in plan.tags:
        if plan.buy_target is not None or plan.buy_stop is not None:
            msg = "목표가에도 손절가에도 안 닿았는데 불안해서 파는 거예요. 산 이유가 사라졌나요?"
            flags.append(Flag("high", msg))

    ret_5d, rsi = ctx.get("ret_5d"), ctx.get("rsi14")
    if ret_5d is not None and ret_5d <= PANIC_DROP_5D and not hit_stop:
        msg = f"직전 5일 {ret_5d * 100:+.0f}% 급락 직후예요. 공포에 파는 건 아닌지 한 번 더 보세요."
        flags.append(Flag("high", msg))
    if rsi is not None and rsi <= 30 and not hit_stop:
        flags.append(Flag("mid", f"RSI {rsi:.0f}: 과매도 구간에서 파는 거예요."))

    if "boredom" in plan.tags:
        flags.append(Flag("mid", "안 움직여서 파는 거라면, 산 이유는 아직 그대로인가요?"))
    pr = plan.position_return
    if "take_profit" in plan.tags and pr is not None and 0 <= pr < TINY_PROFIT:
        flags.append(
            Flag("mid", f"{pr * 100:+.1f}%에서 익절이에요. 수수료·세금을 빼면 남는 게 적어요.")
        )
    if (
        plan.buy_horizon is not None
        and plan.held_days is not None
        and plan.held_days < plan.buy_horizon / 3
        and not (hit_stop or hit_target)
    ):
        msg = (
            f"산 지 {plan.held_days}일 만이에요. 살 때는 {plan.buy_horizon}일 들고 갈 계획이었어요."
        )
        flags.append(Flag("mid", msg))
    if pr is None:
        flags.append(
            Flag(
                "mid", "보유 기록이 없어 지금 손익을 몰라요. CSV를 가져오거나 평단가를 입력하세요."
            )
        )

    order = {"high": 0, "mid": 1, "good": 2}
    flags.sort(key=lambda f: order[f.level])
    return flags
