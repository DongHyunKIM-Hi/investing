"""유사 과거 결정 찾기.

유사도 = 아는 항목끼리의 가중 평균 × 정보 충분도.
- 이유 태그: Jaccard
- 상황 지표: 지표별 척도로 나눈 거리 → 가우시안 커널
- 감정 일치, 손절 계획 유무 일치, 같은 종목 여부
CSV에서 가져와 이유를 모르는 결정도 상황 지표로는 비교할 수 있지만,
아는 항목이 적을수록 유사도를 깎는다.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.services.engine.case import Case

# 지표별 "이 정도 차이면 꽤 다른 상황" 척도
CONTEXT_SCALES: dict[str, float] = {
    "ret_5d": 0.10,
    "ret_20d": 0.20,
    "from_52w_high": 0.25,
    "vs_ma50": 0.15,
    "rsi14": 15.0,
    "spy_ret_20d": 0.05,
    "position_return": 0.20,  # 매도만: 팔 때 수익 중이었나 손실 중이었나
}
WEIGHTS: dict[str, float] = {
    "tags": 0.40,
    "context": 0.35,
    "emotion": 0.10,
    "plan": 0.05,
    "ticker": 0.10,
}
MIN_CONTEXT_DIMS = 3
TOP_K = 10
MIN_SIMILARITY = 0.35


@dataclass(frozen=True)
class Match:
    case: Case
    similarity: float
    parts: Mapping[str, float]  # 항목별 점수 (화면에서 '왜 비슷한지' 설명용)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float | None:
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def context_similarity(a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
    sq: list[float] = []
    for key, scale in CONTEXT_SCALES.items():
        x, y = a.get(key), b.get(key)
        if x is None or y is None:
            continue
        sq.append(((x - y) / scale) ** 2)
    if len(sq) < MIN_CONTEXT_DIMS:
        return None
    return math.exp(-0.5 * sum(sq) / len(sq))


def similarity(query: Case, other: Case) -> tuple[float, dict[str, float]] | None:
    parts: dict[str, float] = {}
    if (s := jaccard(query.tags, other.tags)) is not None:
        parts["tags"] = s
    if (s := context_similarity(query.context, other.context)) is not None:
        parts["context"] = s
    if query.emotion and other.emotion:
        parts["emotion"] = 1.0 if query.emotion == other.emotion else 0.0
    if query.has_stop is not None and other.has_stop is not None:
        parts["plan"] = 1.0 if query.has_stop == other.has_stop else 0.0
    parts["ticker"] = 1.0 if query.ticker == other.ticker else 0.0

    # 종목만 같은 건 근거가 못 된다: 이유나 상황 중 하나는 비교 가능해야 함
    if "tags" not in parts and "context" not in parts:
        return None
    known = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / known
    coverage = known / sum(WEIGHTS.values())
    return score * (0.5 + 0.5 * coverage), parts


def find_similar(
    query: Case,
    history: Iterable[Case],
    k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[Match]:
    """query 시점에 이미 청산돼 승패가 확정된 결정만 비교한다 (미래 정보 누출 방지)."""
    matches: list[Match] = []
    for other in history:
        if other.id == query.id or not other.resolved_before(query.decided_on):
            continue
        result = similarity(query, other)
        if result is None or result[0] < min_similarity:
            continue
        matches.append(Match(other, round(result[0], 4), result[1]))
    matches.sort(key=lambda m: (-m.similarity, -m.case.decided_on.toordinal()))
    return matches[:k]
