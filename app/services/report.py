"""껄껄껄 리포트: 지난 매매 전체에서 반복되는 습관을 찾는다 (순수 함수).

판정과 달리 '지금' 결정이 없으므로 청산된 매매 전부를 본다. 승패는 청산된 매수만 따진다.
상담 후 안 산 결정은 '그날 샀다면 20거래일 뒤'로 참은 게 잘한 건지 따로 본다.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from app.services.engine.case import UNKNOWN, Case
from app.services.engine.verdict import (
    EARLY_EXIT,
    MIN_SAMPLES,
    SITUATIONS,
    VERDICTS,
    Pattern,
    Stats,
    median,
    smoothed_win_prob,
    stats,
)
from app.taxonomy import BUY_REASONS, EMOTIONS, SELL_REASONS

if TYPE_CHECKING:
    from app.models import Decision

PLAN_LABELS: dict[str, str] = {
    "kept": "계획대로 한",
    "broken": "계획을 어긴",
    "none": "계획 없이 산",
}
MISTAKES_TOP = 3
EARLY_EXITS_TOP = 5
TICKERS_TOP = 10
RECENT_SKIPS = 10
# 손실 매매를 수익 매매보다 이 배수 넘게 오래 들고 있으면 '손실은 버티고 수익은 빨리 판다'
HOLD_LOSERS_RATIO = 1.5


@dataclass(frozen=True)
class SellCase:
    id: int
    ticker: str
    sold_on: date
    tags: frozenset[str]
    ret: float | None
    post_exit_20d: float | None


def sell_case_from_decision(d: "Decision") -> SellCase:
    o = d.outcome or {}
    return SellCase(
        id=d.id,
        ticker=d.ticker,
        sold_on=d.decided_on,
        tags=frozenset(t for t in d.reason_tags or [] if t != UNKNOWN),
        ret=o.get("realized_return"),
        post_exit_20d=o.get("post_exit_20d"),
    )


@dataclass(frozen=True)
class SkipCase:
    """상담 후 안 사기로(매도 상담이면 안 팔기로) 한 결정. fwd_*는 그날 종가 대비 이후 수익률."""

    id: int
    ticker: str
    decided_on: date
    verdict: str | None
    fwd_20d: float | None
    fwd_60d: float | None
    side: str = "buy"


def skip_case_from_decision(d: "Decision") -> SkipCase:
    o = d.outcome or {}
    return SkipCase(
        id=d.id,
        ticker=d.ticker,
        decided_on=d.decided_on,
        verdict=d.verdict,
        fwd_20d=o.get("fwd_20d"),
        fwd_60d=o.get("fwd_60d"),
        side=d.side,
    )


@dataclass(frozen=True)
class SkipGroup:
    key: str
    label: str
    n: int  # 보류 후 20거래일이 지난 건
    # 안 산 것: 20일 뒤 손실이면 잘 참음, EARLY_EXIT 넘게 올랐으면 껄
    # 안 판 것: 20일 뒤 그대로거나 올랐으면 잘 버팀, EARLY_EXIT 넘게 떨어졌으면 껄(팔걸)
    good: int
    missed: int
    median_20d: float | None
    tracking: int  # 아직 20거래일이 안 지난 건

    @property
    def good_rate(self) -> float | None:
        return self.good / self.n if self.n else None


def skip_group(key: str, label: str, skips: Sequence[SkipCase], side: str = "buy") -> SkipGroup:
    known = [s.fwd_20d for s in skips if s.fwd_20d is not None]
    sign = 1 if side == "buy" else -1  # 안 판 것은 떨어져야 껄
    return SkipGroup(
        key=key,
        label=label,
        n=len(known),
        good=sum(1 for v in known if sign * v <= 0),
        missed=sum(1 for v in known if sign * v > EARLY_EXIT),
        median_20d=median(known),
        tracking=len(skips) - len(known),
    )


@dataclass(frozen=True)
class SellGroup:
    key: str
    label: str
    n: int  # 판 뒤 20거래일이 지난 매도 수
    rebounds: int  # 판 뒤 EARLY_EXIT 넘게 더 오름
    drops: int  # 판 뒤 EARLY_EXIT 넘게 더 떨어짐 (잘 판 것)
    median_post: float | None

    @property
    def rebound_rate(self) -> float | None:
        return self.rebounds / self.n if self.n else None


@dataclass(frozen=True)
class EarlyExits:
    n: int  # 청산 후 20거래일이 지난 매수 수
    early: int  # 판 뒤 EARLY_EXIT 넘게 더 오름
    good: int  # 판 뒤 EARLY_EXIT 넘게 더 떨어짐
    median_post: float | None
    worst: list[Case]  # 판 뒤 가장 많이 오른 순

    @property
    def rate(self) -> float | None:
        return self.early / self.n if self.n else None


@dataclass(frozen=True)
class Holding:
    wins: int
    losses: int
    win_days: float | None  # 중앙값
    loss_days: float | None

    @property
    def holds_losers(self) -> bool:
        """손실은 버티고 수익은 빨리 파는 습관(처분 효과)이 보이는가."""
        if self.wins < MIN_SAMPLES or self.losses < MIN_SAMPLES:
            return False
        if self.win_days is None or self.loss_days is None:
            return False
        return self.loss_days > max(self.win_days, 1) * HOLD_LOSERS_RATIO


@dataclass(frozen=True)
class Report:
    overall: Stats  # 청산된 매수
    open_count: int
    median_excess: float | None
    tagged: int  # 청산된 매수 중 이유가 기록된 수
    by_tag: list[Pattern]
    by_emotion: list[Pattern]
    by_situation: list[Pattern]
    by_ticker: list[Pattern]
    by_plan: list[Pattern]
    mistakes: list[Pattern]
    early_exits: EarlyExits
    holding: Holding
    sells: list[SellGroup]
    skips: SkipGroup  # 안 산 것
    skips_by_verdict: list[SkipGroup]  # 판정 라벨 순. 해당 건이 없는 라벨은 뺀다
    holds: SkipGroup  # 매도 상담 후 안 판 것
    holds_by_verdict: list[SkipGroup]
    recent_skips: list[SkipCase]  # 둘 다, 최근 순


def _groups(
    kind: str,
    keys: Iterable[tuple[str, str]],
    member: Callable[[str, Case], bool],
    cases: Sequence[Case],
    min_n: int = 1,
) -> list[Pattern]:
    found = []
    for key, label in keys:
        members = [c for c in cases if member(key, c)]
        if len(members) >= min_n:
            found.append(Pattern(kind, key, label, stats(members)))
    # 표본이 충분한 것 먼저, 그 안에서 승률 나쁜 순
    found.sort(key=lambda p: (p.stats.n < MIN_SAMPLES, p.stats.win_rate or 0, -p.stats.n))
    return found


def _badness(p: Pattern) -> float:
    return smoothed_win_prob(p.stats.wins, p.stats.n)[0]


def find_mistakes(groups: Iterable[Pattern], top: int = MISTAKES_TOP) -> list[Pattern]:
    """표본이 충분하고, 스무딩한 승률이 50% 미만이며, 보통(중앙값) 손실인 패턴."""
    bad = [
        p
        for p in groups
        if p.stats.n >= MIN_SAMPLES
        and (p.stats.median_return or 0) < 0
        and _badness(p) < 0.5
        and not (p.kind == "plan" and p.key == "kept")
    ]
    bad.sort(key=lambda p: (_badness(p), p.stats.median_return or 0))
    return bad[:top]


def early_exits(closed: Sequence[Case], top: int = EARLY_EXITS_TOP) -> EarlyExits:
    known = [c for c in closed if c.post_exit_20d is not None]
    early = [c for c in known if (c.post_exit_20d or 0) > EARLY_EXIT]
    early.sort(key=lambda c: c.post_exit_20d or 0, reverse=True)
    return EarlyExits(
        n=len(known),
        early=len(early),
        good=sum(1 for c in known if (c.post_exit_20d or 0) < -EARLY_EXIT),
        median_post=median(c.post_exit_20d for c in known),
        worst=early[:top],
    )


def holding(closed: Sequence[Case]) -> Holding:
    wins = [c for c in closed if c.win]
    losses = [c for c in closed if c.win is False]
    return Holding(
        wins=len(wins),
        losses=len(losses),
        win_days=median(c.holding_days for c in wins),
        loss_days=median(c.holding_days for c in losses),
    )


def sell_groups(sells: Sequence[SellCase]) -> list[SellGroup]:
    known = [s for s in sells if s.post_exit_20d is not None]
    found = []
    for key, label in SELL_REASONS.items():
        members = [s for s in known if key in s.tags]
        if not members:
            continue
        found.append(
            SellGroup(
                key=key,
                label=label,
                n=len(members),
                rebounds=sum(1 for s in members if (s.post_exit_20d or 0) > EARLY_EXIT),
                drops=sum(1 for s in members if (s.post_exit_20d or 0) < -EARLY_EXIT),
                median_post=median(s.post_exit_20d for s in members),
            )
        )
    found.sort(key=lambda g: (g.n < MIN_SAMPLES, -(g.rebound_rate or 0), -g.n))
    return found


def _skips_by_verdict(skips: Sequence[SkipCase], side: str) -> list[SkipGroup]:
    return [
        skip_group(key, label, members, side)
        for key, label in VERDICTS.items()
        if (members := [s for s in skips if s.side == side and s.verdict == key])
    ]


def build_report(
    buys: Sequence[Case], sells: Sequence[SellCase] = (), skips: Sequence[SkipCase] = ()
) -> Report:
    """buys: 체결이 있는 매수 결정 전부(보유 중 포함). sells: 체결이 있는 매도 결정.
    skips: 상담 후 안 사기로(또는 안 팔기로) 한 결정."""
    closed = [c for c in buys if c.win is not None]

    by_tag = _groups("tag", BUY_REASONS.items(), lambda k, c: k in c.tags, closed)
    by_emotion = _groups("emotion", EMOTIONS.items(), lambda k, c: c.emotion == k, closed)
    by_situation = _groups(
        "situation",
        ((k, label) for k, (label, _) in SITUATIONS.items()),
        lambda k, c: SITUATIONS[k][1](c.context),
        closed,
    )
    tickers = sorted({c.ticker for c in closed})
    by_ticker = _groups(
        "ticker", ((t, t) for t in tickers), lambda k, c: c.ticker == k, closed, min_n=2
    )
    by_ticker = sorted(by_ticker, key=lambda p: -p.stats.n)[:TICKERS_TOP]
    by_plan = _groups("plan", PLAN_LABELS.items(), lambda k, c: c.plan == k, closed)

    return Report(
        overall=stats(closed),
        open_count=len(buys) - len(closed),
        median_excess=median(c.excess for c in closed),
        tagged=sum(1 for c in closed if c.tags),
        by_tag=by_tag,
        by_emotion=by_emotion,
        by_situation=by_situation,
        by_ticker=by_ticker,
        by_plan=by_plan,
        mistakes=find_mistakes([*by_tag, *by_emotion, *by_situation, *by_ticker, *by_plan]),
        early_exits=early_exits(closed),
        holding=holding(closed),
        sells=sell_groups(sells),
        skips=skip_group("all", "전체", [s for s in skips if s.side == "buy"]),
        skips_by_verdict=_skips_by_verdict(skips, "buy"),
        holds=skip_group("all", "전체", [s for s in skips if s.side == "sell"], "sell"),
        holds_by_verdict=_skips_by_verdict(skips, "sell"),
        recent_skips=sorted(skips, key=lambda s: (s.decided_on, s.id), reverse=True)[:RECENT_SKIPS],
    )
