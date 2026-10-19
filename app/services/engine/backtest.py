"""판정 엔진 검증: 과거 각 매수 시점에 엔진이 있었다면 뭐라고 했을지, 실제 결과와 비교한다.

각 시점에는 그 이전에 청산된 매매만 보므로(find_similar의 누출 방지) 공정한 사후 검증이 된다.
"비추천" 판정을 받은 매매의 실제 승률이 "추천"보다 낮아야 엔진이 의미가 있다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.engine.case import Case
from app.services.engine.similarity import find_similar
from app.services.engine.verdict import VERDICTS, Stats, Verdict, decide, stats


@dataclass(frozen=True)
class BacktestRow:
    case: Case
    verdict: Verdict


def backtest(history: Sequence[Case]) -> list[BacktestRow]:
    rows = [BacktestRow(c, decide(find_similar(c, history))) for c in history if c.win is not None]
    rows.sort(key=lambda r: r.case.decided_on)
    return rows


def summarize(rows: Sequence[BacktestRow]) -> dict[str, Stats]:
    """판정 라벨별 실제 성적. 라벨 순서는 VERDICTS 순."""
    return {label: stats([r.case for r in rows if r.verdict.label == label]) for label in VERDICTS}
