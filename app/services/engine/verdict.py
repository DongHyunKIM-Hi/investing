"""유사 전적 → 승률·판정, 그리고 "이런 상황에서 당신은" 패턴 통계."""

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.services.engine.case import Case
from app.services.engine.similarity import Match
from app.taxonomy import BUY_REASONS, EMOTIONS

# Beta(2, 2) 사전분포: 표본이 적으면 승률을 50% 쪽으로 당긴다
PRIOR = 2.0
MIN_SAMPLES = 3
Z80 = 1.2816  # 80% 구간
EARLY_EXIT = 0.05  # 판 뒤 20일간 이만큼 더 오르면 '너무 일찍 판' 것으로 본다

VERDICTS: dict[str, str] = {
    "recommend": "추천",
    "caution": "신중",
    "against": "비추천",
    "insufficient": "데이터 부족",
}


@dataclass(frozen=True)
class Stats:
    n: int
    wins: int
    win_rate: float | None  # 단순 승률 (wins / n)
    avg_return: float | None
    # 테마주는 한두 건의 대박이 평균을 끌어올리므로 '보통 어땠는지'는 중앙값으로 본다
    median_return: float | None


@dataclass(frozen=True)
class Verdict:
    label: str
    n: int  # 유사 사례 수
    n_eff: float  # 유사도 가중 표본 수
    win_prob: float  # 스무딩된 승률
    ci_low: float
    ci_high: float
    raw: Stats  # 가중치 없는 단순 집계 (화면에 "N건 중 M건"으로 표시)
    avg_return: float | None  # 이하 유사도 가중 평균
    avg_excess: float | None
    avg_max_dd: float | None
    early_exit_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def median(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def _wmean(pairs: Iterable[tuple[float | None, float]]) -> float | None:
    pairs = [(v, w) for v, w in pairs if v is not None]
    total = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total if total else None


def stats(cases: Sequence[Case]) -> Stats:
    wins = sum(1 for c in cases if c.win)
    return Stats(
        n=len(cases),
        wins=wins,
        win_rate=wins / len(cases) if cases else None,
        avg_return=_mean(c.ret for c in cases),
        median_return=median(c.ret for c in cases),
    )


def smoothed_win_prob(wins: float, n: float, prior: float = PRIOR) -> tuple[float, float, float]:
    """(사후 평균, 80% 하한, 80% 상한). 구간은 베타분포의 정규 근사."""
    a, b = prior + wins, prior + (n - wins)
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    return mean, max(0.0, mean - Z80 * sd), min(1.0, mean + Z80 * sd)


def decide(matches: Sequence[Match]) -> Verdict:
    cases = [m.case for m in matches]
    n_eff = sum(m.similarity for m in matches)
    w_wins = sum(m.similarity for m in matches if m.case.win)
    p, lo, hi = smoothed_win_prob(w_wins, n_eff)
    avg_ret = _wmean((m.case.ret, m.similarity) for m in matches)
    raw = stats(cases)
    typical = raw.median_return

    exits = [c.post_exit_20d for c in cases if c.post_exit_20d is not None]
    early = sum(1 for v in exits if v > EARLY_EXIT) / len(exits) if exits else None

    if len(matches) < MIN_SAMPLES:
        label = "insufficient"
    elif p >= 0.6 and (typical or 0) > 0:
        label = "recommend"
    elif p <= 0.4 or (typical is not None and typical <= -0.05):
        label = "against"
    else:
        label = "caution"

    return Verdict(
        label=label,
        n=len(matches),
        n_eff=round(n_eff, 2),
        win_prob=round(p, 4),
        ci_low=round(lo, 4),
        ci_high=round(hi, 4),
        raw=raw,
        avg_return=avg_ret,
        avg_excess=_wmean((m.case.excess, m.similarity) for m in matches),
        avg_max_dd=_wmean((m.case.max_dd, m.similarity) for m in matches),
        early_exit_rate=early,
    )


# ---- 패턴: 지금 결정과 같은 '조건'을 가진 과거 매매 전체의 성적 ----

Ctx = Mapping[str, Any]


def _ge(key: str, x: float) -> Callable[[Ctx], bool]:
    return lambda c: c.get(key) is not None and c[key] >= x


def _le(key: str, x: float) -> Callable[[Ctx], bool]:
    return lambda c: c.get(key) is not None and c[key] <= x


SITUATIONS: dict[str, tuple[str, Callable[[Ctx], bool]]] = {
    "surge_5d": ("직전 5일 +15% 이상 급등한 뒤", _ge("ret_5d", 0.15)),
    "surge_20d": ("직전 20일 +40% 이상 오른 뒤", _ge("ret_20d", 0.40)),
    "near_high": ("52주 고점 근처(-5% 이내)에서", _ge("from_52w_high", -0.05)),
    "deep_dip": ("52주 고점 대비 -40% 이상 빠진 뒤", _le("from_52w_high", -0.40)),
    "overbought": ("RSI 70 이상 과열 구간에서", _ge("rsi14", 70)),
    "oversold": ("RSI 30 이하 과매도 구간에서", _le("rsi14", 30)),
    "weak_market": ("시장(SPY)이 200일선 아래일 때", _le("spy_vs_ma200", 0)),
}


@dataclass(frozen=True)
class Pattern:
    kind: str  # tag | emotion | situation | ticker | plan
    key: str
    label: str
    stats: Stats

    @property
    def sentence(self) -> str:
        s = self.stats
        avg = ""
        if s.median_return is not None and s.avg_return is not None:
            avg = f", 중앙값 {s.median_return * 100:+.1f}% (평균 {s.avg_return * 100:+.1f}%)"
        subject = {
            "tag": f"'{self.label}' 이유로 산",
            "emotion": f"'{self.label}' 상태로 산",
            "situation": f"{self.label} 산",
            "ticker": f"{self.label}을(를) 산",
            "plan": self.label,
        }[self.kind]
        return f"{subject} 매매 {s.n}건 중 {s.wins}건 수익{avg}"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "sentence": self.sentence}


def patterns(query: Case, history: Iterable[Case], min_n: int = 2) -> list[Pattern]:
    past = [c for c in history if c.id != query.id and c.resolved_before(query.decided_on)]
    found: list[Pattern] = []

    def add(kind: str, key: str, label: str, members: list[Case]) -> None:
        if len(members) >= min_n:
            found.append(Pattern(kind, key, label, stats(members)))

    for tag in sorted(query.tags):
        add("tag", tag, BUY_REASONS.get(tag, tag), [c for c in past if tag in c.tags])
    if query.emotion:
        label = EMOTIONS.get(query.emotion, query.emotion)
        add("emotion", query.emotion, label, [c for c in past if c.emotion == query.emotion])
    for key, (label, test) in SITUATIONS.items():
        if test(query.context):
            add("situation", key, label, [c for c in past if test(c.context)])
    add("ticker", query.ticker, query.ticker, [c for c in past if c.ticker == query.ticker])

    # 승률이 극단적인(나쁜 쪽 먼저) 패턴이 위로
    found.sort(key=lambda p: (p.stats.win_rate or 0, -p.stats.n))
    return found
