from datetime import date, timedelta

from app.services.engine.case import Case
from app.services.review import build_review, override_text, review_lines
from app.services.scorecard import Judged
from app.services.watch import check_position

TODAY = date(2026, 10, 29)


def judged(id: int, days_ago: int, verdict: str, action: str, side: str = "buy") -> Judged:
    return Judged(
        id=id,
        ticker="IONQ",
        decided_on=TODAY - timedelta(days=days_ago),
        side=side,
        verdict=verdict,
        win_prob=0.5,
        action=action,
    )


def closed(id: int, days_ago: int, ret: float, ticker: str = "PLTR") -> Case:
    end = TODAY - timedelta(days=days_ago)
    return Case(
        id=id,
        ticker=ticker,
        decided_on=end - timedelta(days=30),
        closed_on=end,
        win=ret > 0,
        ret=ret,
    )


def test_window_is_inclusive_last_seven_days():
    r = build_review(
        [
            judged(1, 0, "caution", "executed"),
            judged(2, 6, "caution", "skipped"),
            judged(3, 7, "caution", "executed"),
        ],
        [closed(1, 6, 0.1), closed(2, 7, -0.1)],
        [],
        untagged=0,
        today=TODAY,
    )
    assert r.since == TODAY - timedelta(days=6)
    assert [j.id for j in r.consults] == [1, 2]
    assert [c.id for c in r.closed] == [1]
    assert r.by_verdict["caution"] == 2 and r.by_verdict["recommend"] == 0
    assert r.by_action == {"executed": 1, "skipped": 1, "pending": 0}


def test_overrides_and_closed_sorted_worst_first():
    rows = [
        judged(1, 1, "against", "executed"),  # 비추천인데 삼
        judged(2, 2, "recommend", "skipped"),  # 추천인데 안 삼
        judged(3, 3, "recommend", "executed"),  # 따름
        judged(4, 4, "against", "skipped", side="sell"),  # 따름 (안 팖)
        judged(5, 5, "recommend", "skipped", side="sell"),  # 매도 추천인데 안 팖
    ]
    r = build_review(
        rows, [closed(1, 1, 0.2), closed(2, 2, -0.3), closed(3, 3, 0.05)], [], 3, TODAY
    )
    assert [j.id for j in r.overrides] == [1, 2, 5]
    assert [override_text(j) for j in r.overrides] == [
        "비추천인데 샀음",
        "추천인데 안 샀음",
        "매도 추천인데 안 팔았음",
    ]
    assert [c.ret for c in r.closed] == [-0.3, 0.05, 0.2]
    assert r.closed_stats.wins == 2 and r.closed_stats.median_return == 0.05


def test_alerts_keep_only_urgent_watches():
    plan = {"decision_id": 1, "target_price": 60.0, "stop_price": 40.0, "horizon_days": 300}
    breached = check_position("A", 1, 50.0, TODAY - timedelta(days=10), 38.0, TODAY, plan, TODAY)
    fine = check_position("B", 1, 50.0, TODAY - timedelta(days=10), 51.0, TODAY, plan, TODAY)
    r = build_review([], [], [breached, fine], 0, TODAY)
    assert [w.ticker for w in r.alerts] == ["A"]
    assert not r.empty


def test_review_lines_empty_and_full():
    assert review_lines(build_review([], [], [], 0, TODAY))[1].startswith("  이번 주는")
    r = build_review([judged(1, 0, "against", "executed")], [closed(1, 0, -0.1)], [], 2, TODAY)
    text = "\n".join(review_lines(r))
    assert "상담 1건: 비추천 1 / 실행 1" in text
    assert "비추천인데 샀음" in text
    assert "청산 1건: 수익 0건, 중앙값 -10.0%" in text
    assert "이유가 비어 있는 결정 2건" in text
