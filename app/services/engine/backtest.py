"""판정 엔진 검증: 과거 각 매수 시점에 엔진이 있었다면 뭐라고 했을지, 실제 결과와 비교한다.

각 시점에는 그 이전에 청산된 매매만 보므로(find_similar의 누출 방지) 공정한 사후 검증이 된다.
"비추천" 판정을 받은 매매의 실제 승률이 "추천"보다 낮아야 엔진이 의미가 있다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.services.engine.case import Case
from app.services.engine.similarity import WEIGHTS, find_similar
from app.services.engine.verdict import VERDICTS, Stats, Verdict, decide, stats


@dataclass(frozen=True)
class BacktestRow:
    case: Case
    verdict: Verdict


def backtest(history: Sequence[Case], weights: Mapping[str, float] = WEIGHTS) -> list[BacktestRow]:
    rows = [
        BacktestRow(c, decide(find_similar(c, history, weights=weights)))
        for c in history
        if c.win is not None
    ]
    rows.sort(key=lambda r: r.case.decided_on)
    return rows


@dataclass(frozen=True)
class Score:
    """가중치 설정 하나의 성적. 판정이 나온(데이터 부족 아님) 매매만 센다."""

    judged: int  # 판정이 나온 매매 수
    total: int
    brier: (
        float | None
    )  # 보정 승률 vs 실제 승패의 평균 제곱 오차 (낮을수록 좋음, 0.25가 동전 던지기)
    spread: float | None  # 추천 승률 - 비추천 승률 (높을수록 판정이 갈라짐)

    @property
    def coverage(self) -> float:
        return self.judged / self.total if self.total else 0.0


def score(rows: Sequence[BacktestRow]) -> Score:
    judged = [r for r in rows if r.verdict.label != "insufficient"]
    brier = (
        sum((r.verdict.win_prob - (1.0 if r.case.win else 0.0)) ** 2 for r in judged) / len(judged)
        if judged
        else None
    )
    by = summarize(rows)
    rec, agn = by["recommend"].win_rate, by["against"].win_rate
    spread = rec - agn if rec is not None and agn is not None else None
    return Score(judged=len(judged), total=len(rows), brier=brier, spread=spread)


def summarize(rows: Sequence[BacktestRow]) -> dict[str, Stats]:
    """판정 라벨별 실제 성적. 라벨 순서는 VERDICTS 순."""
    return {label: stats([r.case for r in rows if r.verdict.label == label]) for label in VERDICTS}
