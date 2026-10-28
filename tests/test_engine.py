from datetime import date, timedelta

import pytest

from app.services.engine.backtest import backtest, summarize
from app.services.engine.case import Case
from app.services.engine.similarity import (
    MIN_SIMILARITY,
    context_similarity,
    find_similar,
    jaccard,
    similarity,
)
from app.services.engine.verdict import decide, patterns, smoothed_win_prob

TODAY = date(2026, 10, 19)
SURGE = {"ret_5d": 0.30, "ret_20d": 0.60, "from_52w_high": -0.02, "vs_ma50": 0.40, "rsi14": 82}
DIP = {"ret_5d": -0.08, "ret_20d": -0.25, "from_52w_high": -0.55, "vs_ma50": -0.20, "rsi14": 28}


def case(
    id: int,
    tags: set[str] = frozenset(),  # type: ignore[assignment]
    ctx: dict | None = None,
    win: bool | None = True,
    ret: float | None = 0.1,
    days_ago: int = 100,
    held: int = 10,
    ticker: str = "IONQ",
    emotion: str | None = None,
) -> Case:
    opened = TODAY - timedelta(days=days_ago)
    return Case(
        id=id,
        ticker=ticker,
        decided_on=opened,
        tags=frozenset(tags),
        emotion=emotion,
        context=ctx or {},
        closed_on=opened + timedelta(days=held) if win is not None else None,
        win=win,
        ret=ret if win is not None else None,
    )


def test_jaccard_unknown_when_either_empty():
    assert jaccard(frozenset(), frozenset({"news"})) is None
    assert jaccard(frozenset({"news", "theme"}), frozenset({"news"})) == 0.5


def test_context_similarity_identical_and_far():
    assert context_similarity(SURGE, SURGE) == pytest.approx(1.0)
    far = context_similarity(SURGE, DIP)
    assert far is not None and far < 0.01
    assert context_similarity({"ret_5d": 0.1}, {"ret_5d": 0.1}) is None  # 비교 지표 부족


def test_similarity_needs_reason_or_context():
    q = case(1, ctx={})
    assert similarity(q, case(2, ctx={})) is None  # 종목만 같으면 근거가 아님


def test_untagged_history_is_discounted():
    q = case(1, {"momentum_chase"}, SURGE, win=None, days_ago=0)
    tagged = similarity(q, case(2, {"momentum_chase"}, SURGE))
    untagged = similarity(q, case(3, set(), SURGE))
    assert tagged is not None and untagged is not None
    assert tagged[0] > untagged[0]
    assert "tags" not in untagged[1]


def test_find_similar_excludes_future_and_unresolved():
    q = case(1, {"momentum_chase"}, SURGE, win=None, days_ago=0)
    history = [
        case(2, {"momentum_chase"}, SURGE),
        case(3, {"momentum_chase"}, SURGE, win=None),  # 아직 보유 중
        case(4, {"momentum_chase"}, SURGE, days_ago=5, held=10),  # 청산이 query 이후
        case(5, {"dip_buy"}, DIP, ticker="AAPL"),  # 전혀 다른 상황
    ]
    found = find_similar(q, history)
    assert [m.case.id for m in found] == [2]
    assert found[0].similarity >= MIN_SIMILARITY


def test_smoothing_pulls_small_samples_toward_half():
    p1, _, _ = smoothed_win_prob(1, 1)
    p10, lo, hi = smoothed_win_prob(10, 10)
    assert 0.5 < p1 < p10 < 1
    assert lo < p10 < hi


def test_decide_labels():
    q = case(0, {"momentum_chase"}, SURGE, win=None, days_ago=0)
    losers = [case(i, {"momentum_chase"}, SURGE, win=False, ret=-0.15) for i in range(1, 7)]
    winners = [case(i, {"momentum_chase"}, SURGE, win=True, ret=0.2) for i in range(1, 7)]
    assert decide(find_similar(q, losers)).label == "against"
    assert decide(find_similar(q, winners)).label == "recommend"
    assert decide(find_similar(q, losers[:2])).label == "insufficient"
    mixed = losers[:3] + [case(i, {"momentum_chase"}, SURGE, ret=0.12) for i in range(7, 10)]
    assert decide(find_similar(q, mixed)).label == "caution"
    # 승률은 반반이지만 대박 한 건이 평균만 끌어올린 경우: 보통의 결과(중앙값)로 판정
    lottery = [
        *losers[:4],
        *(case(i, {"momentum_chase"}, SURGE, ret=0.01) for i in (7, 8)),
        case(9, {"momentum_chase"}, SURGE, ret=1.5),
    ]
    v = decide(find_similar(q, lottery))
    assert v.avg_return is not None and v.avg_return > 0
    assert v.raw.median_return == -0.15 and v.label == "against"


def test_patterns_report_tag_and_situation_records():
    q = case(0, {"momentum_chase"}, SURGE, win=None, days_ago=0, emotion="fomo")
    history = [
        case(1, {"momentum_chase"}, SURGE, win=False, ret=-0.2, emotion="fomo"),
        case(2, {"momentum_chase"}, SURGE, win=False, ret=-0.1, emotion="fomo"),
        case(3, {"theme"}, SURGE, win=True, ret=0.3),
        case(4, {"dip_buy"}, DIP, win=True, ret=0.2, ticker="AAPL"),
    ]
    by_key = {p.key: p for p in patterns(q, history)}
    assert by_key["momentum_chase"].stats.wins == 0
    assert by_key["momentum_chase"].sentence.startswith("'급등 추격' 이유로 산 매매 2건 중 0건")
    assert by_key["fomo"].stats.n == 2
    assert by_key["surge_5d"].stats.n == 3  # 이유와 무관하게 급등 직후 산 매매 전체
    assert by_key["IONQ"].stats.n == 3
    assert "deep_dip" not in by_key


def test_backtest_uses_only_prior_trades():
    history = [
        case(1, {"momentum_chase"}, SURGE, win=False, ret=-0.2, days_ago=300),
        case(2, {"momentum_chase"}, SURGE, win=False, ret=-0.1, days_ago=250),
        case(3, {"momentum_chase"}, SURGE, win=False, ret=-0.1, days_ago=200),
        case(4, {"momentum_chase"}, SURGE, win=True, ret=0.1, days_ago=150),
    ]
    rows = backtest(history)
    assert [r.verdict.n for r in rows] == [0, 1, 2, 3]
    assert rows[-1].verdict.label == "against"
    summary = summarize(rows)
    assert summary["against"].n == 1 and summary["insufficient"].n == 3


def test_backtest_score_brier_and_spread():
    from app.services.engine.backtest import score

    history = [case(i, {"theme"}, SURGE, win=i % 3 == 0, days_ago=400 - i * 20) for i in range(12)]
    rows = backtest(history)
    sc = score(rows)
    assert sc.total == 12 and 0 < sc.judged <= 12
    assert sc.brier is not None and 0 <= sc.brier <= 1
    assert sc.coverage == sc.judged / 12


def test_find_similar_accepts_custom_weights():
    q = case(1, {"theme"}, SURGE, ticker="AAA")
    same_ticker = case(2, {"news"}, DIP, ticker="AAA", days_ago=300)
    same_reason = case(3, {"theme"}, SURGE, ticker="BBB", days_ago=300)
    ticker_only = {"tags": 0.0, "context": 0.0, "emotion": 0.0, "plan": 0.0, "ticker": 1.0}
    top = find_similar(q, [same_ticker, same_reason], weights=ticker_only, min_similarity=0.0)
    assert top[0].case.id == 2
    assert find_similar(q, [same_ticker, same_reason])[0].case.id == 3
