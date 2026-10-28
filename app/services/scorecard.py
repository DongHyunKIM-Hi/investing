"""상담 성적표: 실제로 내린 판정이 맞았는지, 그리고 그 판정을 따랐는지 (순수 함수).

/backtest의 엔진 검증은 '지금 엔진으로 과거를 다시 판정'한다. 여기서는 상담 당시 화면에 떴던
판정(verdict 칼럼, 그때 데이터로 고정)과 그 뒤 실제 결과를 비교한다.
실행한 건은 실제 체결 결과로, 보류한 건은 '그날 샀다면 20거래일 뒤'로 승패를 본다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from app.services.engine.case import case_from_decision
from app.services.engine.verdict import EARLY_EXIT, VERDICTS, Stats, median

if TYPE_CHECKING:
    from app.models import Decision

ACTIONS: dict[str, str] = {"executed": "실행", "skipped": "보류", "pending": "미정"}
DECISIVE = ("recommend", "against")  # 맞았는지 따질 수 있는 판정. 신중·데이터 부족은 채점 안 함
RECENT = 20


@dataclass(frozen=True)
class Judged:
    """판정을 받은 상담 결정 1건."""

    id: int
    ticker: str
    decided_on: date
    side: str
    verdict: str
    win_prob: float | None
    action: str  # executed | skipped | pending
    # 실행한 건: 매수는 실현 수익 > 0, 매도는 판 뒤 20일 안 오름. 청산 전이면 None
    win: bool | None = None
    ret: float | None = None  # 매도는 -(판 뒤 20일 수익률)
    fwd_20d: float | None = None  # 보류한 건: 그날 종가 대비 20거래일 수익률

    @property
    def would_win(self) -> bool | None:
        """'판정대로 샀다면(팔았다면)' 이겼을까. 결과가 아직 없으면 None."""
        if self.action == "executed":
            return self.win
        if self.action == "skipped" and self.fwd_20d is not None:
            # 매수 보류: 올랐으면 샀어야 했다. 매도 보류(버팀): 떨어졌으면 팔았어야 했다
            return self.fwd_20d > 0 if self.side == "buy" else self.fwd_20d <= 0
        return None

    @property
    def followed(self) -> bool | None:
        """판정을 따랐나. 추천→실행, 비추천→보류. 신중·데이터 부족이나 미정은 따질 수 없다."""
        if self.action == "pending" or self.verdict not in DECISIVE:
            return None
        return (self.verdict == "recommend") == (self.action == "executed")

    @property
    def hit(self) -> bool | None:
        """판정이 결과와 맞았나. 추천은 '하면 이김', 비추천은 '하면 짐'이어야 적중."""
        would = self.would_win
        if self.verdict not in DECISIVE or would is None:
            return None
        return would == (self.verdict == "recommend")

    @property
    def result(self) -> float | None:
        """화면용 결과 수익률: 실행한 건은 ret, 보류한 건은 그날 샀다면의 20일 수익률."""
        return self.ret if self.action == "executed" else self.fwd_20d


def judged_from_decision(d: "Decision") -> Judged:
    assert d.verdict is not None
    o = d.outcome or {}
    skipped = o.get("status") == "skipped"
    action = d.user_action if d.user_action in ACTIONS else "pending"
    c = case_from_decision(d) if action == "executed" and not skipped else None
    return Judged(
        id=d.id,
        ticker=d.ticker,
        decided_on=d.decided_on,
        side=d.side,
        verdict=d.verdict,
        win_prob=d.win_prob,
        action=action,
        win=c.win if c else None,
        ret=c.ret if c else None,
        fwd_20d=o.get("fwd_20d") if skipped else None,
    )


@dataclass(frozen=True)
class LabelRow:
    key: str
    label: str
    n: int
    executed: int
    skipped: int
    pending: int
    traded: Stats  # 실행해서 결과가 난 건
    skip_n: int  # 보류 후 20거래일이 지난 건
    skip_good: int  # 참길 잘한 건 (매수: 안 오름, 매도 보류: 안 떨어짐)
    skip_missed: int  # 껄: EARLY_EXIT 넘게 반대로 간 건
    skip_median: float | None  # 보류 건의 그날 샀다면 20일 수익률 중앙값

    @property
    def skip_good_rate(self) -> float | None:
        return self.skip_good / self.skip_n if self.skip_n else None


@dataclass(frozen=True)
class Scorecard:
    side: str
    n: int
    labels: list[LabelRow]  # VERDICTS 순, 해당 건이 있는 라벨만
    scored: int  # 추천·비추천 중 결과가 난 건
    hits: int
    followable: int  # 추천·비추천 중 실행/보류를 고른 건
    followed: int
    brier_n: int  # 결과가 난 건 중 승률이 기록된 건
    brier: float | None  # 당시 승률 vs 실제의 평균 제곱 오차 (0.25가 동전 던지기)
    undecided: int  # 판정은 받았는데 실행/보류를 아직 안 고른 건
    rows: list[Judged]  # 최근 순

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.scored if self.scored else None

    @property
    def follow_rate(self) -> float | None:
        return self.followed / self.followable if self.followable else None


def _traded_stats(members: Sequence[Judged]) -> Stats:
    traded = [j for j in members if j.action == "executed" and j.win is not None]
    wins = sum(1 for j in traded if j.win)
    rets = [j.ret for j in traded if j.ret is not None]
    return Stats(
        n=len(traded),
        wins=wins,
        win_rate=wins / len(traded) if traded else None,
        avg_return=sum(rets) / len(rets) if rets else None,
        median_return=median(rets),
    )


def _label_row(key: str, members: Sequence[Judged], side: str) -> LabelRow:
    skips = [j.fwd_20d for j in members if j.action == "skipped" and j.fwd_20d is not None]
    sign = 1 if side == "buy" else -1  # 매도 보류(버팀)는 떨어져야 껄
    return LabelRow(
        key=key,
        label=VERDICTS[key],
        n=len(members),
        executed=sum(1 for j in members if j.action == "executed"),
        skipped=sum(1 for j in members if j.action == "skipped"),
        pending=sum(1 for j in members if j.action == "pending"),
        traded=_traded_stats(members),
        skip_n=len(skips),
        skip_good=sum(1 for v in skips if sign * v <= 0),
        skip_missed=sum(1 for v in skips if sign * v > EARLY_EXIT),
        skip_median=median(skips),
    )


def build_scorecard(rows: Sequence[Judged], side: str) -> Scorecard:
    rows = [j for j in rows if j.side == side]
    decisive = [j for j in rows if j.verdict in DECISIVE]
    scored = [j for j in decisive if j.hit is not None]
    followable = [j for j in decisive if j.followed is not None]
    errors = [
        (j.win_prob - (1.0 if j.would_win else 0.0)) ** 2
        for j in rows
        if j.would_win is not None and j.win_prob is not None
    ]
    brier = sum(errors) / len(errors) if errors else None
    return Scorecard(
        side=side,
        n=len(rows),
        labels=[
            _label_row(key, members, side)
            for key in VERDICTS
            if (members := [j for j in rows if j.verdict == key])
        ],
        scored=len(scored),
        hits=sum(1 for j in scored if j.hit),
        followable=len(followable),
        followed=sum(1 for j in followable if j.followed),
        brier_n=len(errors),
        brier=brier,
        undecided=sum(1 for j in rows if j.action == "pending"),
        rows=sorted(rows, key=lambda j: (j.decided_on, j.id), reverse=True)[:RECENT],
    )
