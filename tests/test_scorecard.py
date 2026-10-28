from datetime import date
from decimal import Decimal

import pytest

from app.models import Decision
from app.services.scorecard import Judged, build_scorecard, judged_from_decision

TODAY = date(2026, 10, 29)


def judged(
    id: int,
    verdict: str,
    action: str,
    win: bool | None = None,
    ret: float | None = None,
    fwd: float | None = None,
    side: str = "buy",
    prob: float | None = 0.5,
) -> Judged:
    return Judged(
        id=id,
        ticker="IONQ",
        decided_on=date(2026, 1, id),
        side=side,
        verdict=verdict,
        win_prob=prob,
        action=action,
        win=win,
        ret=ret,
        fwd_20d=fwd,
    )


def test_followed_only_for_decisive_verdicts():
    assert judged(1, "recommend", "executed").followed is True
    assert judged(2, "recommend", "skipped").followed is False
    assert judged(3, "against", "skipped").followed is True
    assert judged(4, "against", "executed").followed is False
    assert judged(5, "caution", "executed").followed is None
    assert judged(6, "insufficient", "skipped").followed is None
    assert judged(7, "recommend", "pending").followed is None


def test_hit_uses_actual_result_or_would_have_result():
    assert judged(1, "recommend", "executed", win=True).hit is True
    assert judged(2, "against", "executed", win=True).hit is False  # 비추천인데 샀고 이김
    assert judged(3, "against", "skipped", fwd=-0.1).hit is True  # 참길 잘함
    assert judged(4, "recommend", "skipped", fwd=0.1).hit is True  # 안 샀지만 판정은 맞음
    assert judged(5, "recommend", "executed").hit is None  # 아직 보유 중
    assert judged(6, "caution", "executed", win=False).hit is None


def test_sell_hold_is_right_when_price_did_not_fall():
    # 매도 추천을 거슬러 들고 있었는데 떨어졌다 → 팔았어야 했다 → 판정 적중
    assert judged(1, "recommend", "skipped", fwd=-0.1, side="sell").hit is True
    assert judged(2, "recommend", "skipped", fwd=0.1, side="sell").hit is False
    # 매도 비추천을 따라 들고 있었고 올랐다 → 적중
    assert judged(3, "against", "skipped", fwd=0.1, side="sell").hit is True


def test_build_scorecard_counts_and_rates():
    rows = [
        judged(1, "recommend", "executed", win=True, ret=0.2, prob=0.7),
        judged(2, "recommend", "executed", win=False, ret=-0.1, prob=0.7),
        judged(3, "against", "skipped", fwd=-0.05, prob=0.3),
        judged(4, "against", "skipped", fwd=0.12, prob=0.3),  # 껄
        judged(5, "against", "executed", win=None, prob=0.3),  # 거슬러 샀고 아직 보유
        judged(6, "caution", "pending", prob=0.5),
        judged(7, "recommend", "executed", win=True, ret=0.1, side="sell", prob=0.6),
    ]
    sc = build_scorecard(rows, "buy")
    assert sc.n == 6
    assert [r.key for r in sc.labels] == ["recommend", "caution", "against"]
    assert sc.scored == 4 and sc.hits == 2 and sc.hit_rate == 0.5
    assert sc.followable == 5 and sc.followed == 4
    assert sc.undecided == 1
    # (0.7-1)² (0.7-0)² (0.3-0)² (0.3-1)² 의 평균: 반은 맞고 반은 틀려 동전 던지기보다 나쁘다
    assert sc.brier_n == 4 and sc.brier == pytest.approx(0.29)
    assert [j.id for j in sc.rows] == [6, 5, 4, 3, 2, 1]

    rec = sc.labels[0]
    assert (rec.executed, rec.skipped, rec.pending) == (2, 0, 0)
    assert rec.traded.n == 2 and rec.traded.wins == 1 and rec.traded.median_return == 0.05
    agn = sc.labels[2]
    assert agn.skip_n == 2 and agn.skip_good == 1 and agn.skip_missed == 1
    assert agn.skip_good_rate == 0.5

    sell = build_scorecard(rows, "sell")
    assert sell.n == 1 and sell.hits == 1


def test_empty_scorecard():
    sc = build_scorecard([], "buy")
    assert sc.n == 0 and sc.labels == [] and sc.hit_rate is None and sc.brier is None


def _decision(**kw) -> Decision:
    base = dict(
        id=1,
        ticker="IONQ",
        side="buy",
        source="consult",
        decided_on=date(2026, 3, 2),
        reason_tags=["theme"],
        verdict="against",
        win_prob=0.35,
        user_action="executed",
    )
    return Decision(**{**base, **kw})


def test_judged_from_executed_buy_decision():
    closed = {"status": "closed", "closed_on": "2026-03-20", "win": False, "realized_return": -0.12}
    d = _decision(outcome=closed)
    j = judged_from_decision(d)
    assert (j.action, j.win, j.ret, j.fwd_20d) == ("executed", False, -0.12, None)
    assert j.hit is True and j.followed is False and j.result == -0.12


def test_judged_from_skipped_and_undecided_decisions():
    skipped = _decision(
        id=2, user_action="skipped", outcome={"status": "skipped", "fwd_20d": 0.08, "fwd_60d": None}
    )
    j = judged_from_decision(skipped)
    assert (j.action, j.win, j.fwd_20d) == ("skipped", None, 0.08)
    assert j.hit is False and j.followed is True  # 비추천 따라 참았지만 올랐다

    still_open = _decision(id=3, outcome={"status": "open"})
    assert judged_from_decision(still_open).hit is None

    pending = _decision(id=4, user_action="pending", outcome=None, stop_price=Decimal("10"))
    assert judged_from_decision(pending).action == "pending"


def test_judged_from_sell_decision_uses_post_exit():
    d = _decision(
        id=5,
        side="sell",
        verdict="recommend",
        outcome={"realized_return": 0.3, "post_exit_20d": 0.07, "post_exit_on": "2026-03-30"},
    )
    j = judged_from_decision(d)
    assert j.win is False and j.ret == -0.07  # 판 뒤 더 올랐다 → 잘 못 판 매도
    assert j.hit is False
