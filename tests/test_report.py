from datetime import date, timedelta

import pytest

from app.services.engine.case import Case
from app.services.outcomes import plan_status
from app.services.report import SellCase, SkipCase, build_report, find_mistakes, holding

TODAY = date(2026, 10, 21)
SURGE = {"ret_5d": 0.30, "rsi14": 82}


def case(
    id: int,
    ret: float | None,
    tags: set[str] = frozenset(),  # type: ignore[assignment]
    emotion: str | None = None,
    ctx: dict | None = None,
    held: int = 10,
    post: float | None = None,
    plan: str | None = None,
    ticker: str = "IONQ",
    followed: bool | None = None,
) -> Case:
    opened = TODAY - timedelta(days=200 - id)
    closed = ret is not None
    return Case(
        id=id,
        ticker=ticker,
        decided_on=opened,
        tags=frozenset(tags),
        emotion=emotion,
        context=ctx or {},
        closed_on=opened + timedelta(days=held) if closed else None,
        win=ret > 0 if ret is not None else None,
        ret=ret,
        post_exit_20d=post,
        holding_days=held if closed else None,
        plan=plan,
        followed=followed,
    )


def test_empty_history():
    r = build_report([])
    assert r.overall.n == 0
    assert r.mistakes == []
    assert r.early_exits.rate is None


def test_overall_excludes_open_positions():
    r = build_report([case(1, 0.1), case(2, -0.2), case(3, None)])
    assert r.overall.n == 2
    assert r.overall.wins == 1
    assert r.open_count == 1


def test_groups_by_tag_and_emotion_worst_first():
    buys = [
        *(case(i, -0.1, {"momentum_chase"}, emotion="fomo") for i in range(1, 4)),
        case(4, 0.2, {"momentum_chase"}, emotion="fomo"),
        *(case(i, 0.1, {"earnings"}, emotion="calm") for i in range(5, 9)),
    ]
    r = build_report(buys)
    assert [p.key for p in r.by_tag] == ["momentum_chase", "earnings"]
    assert r.by_tag[0].stats.wins == 1 and r.by_tag[0].stats.n == 4
    assert [p.key for p in r.by_emotion] == ["fomo", "calm"]
    assert r.tagged == 8


def test_small_samples_sorted_after_enough():
    buys = [case(1, -0.1, {"theme"}), *(case(i, 0.1, {"earnings"}) for i in range(2, 5))]
    r = build_report(buys)
    # theme은 승률 0%지만 1건뿐이라 뒤로
    assert [p.key for p in r.by_tag] == ["earnings", "theme"]


def test_mistakes_need_samples_and_losses():
    buys = [
        *(case(i, -0.1, {"momentum_chase"}, ctx=SURGE) for i in range(1, 5)),
        case(5, -0.3, {"community_tip"}),  # 1건뿐 → 실수 후보 아님
        *(case(i, 0.1, {"earnings"}) for i in range(6, 10)),
    ]
    r = build_report(buys)
    keys = {(p.kind, p.key) for p in r.mistakes}
    assert ("tag", "momentum_chase") in keys
    assert ("situation", "surge_5d") in keys
    assert all(p.key != "community_tip" for p in r.mistakes)
    assert all(p.key != "earnings" for p in r.mistakes)
    assert len(r.mistakes) <= 3


def test_mistakes_ignore_kept_plan_even_if_losing():
    kept = build_report([case(i, -0.05, plan="kept") for i in range(1, 5)]).by_plan
    assert find_mistakes(kept) == []
    broken = build_report([case(i, -0.2, plan="broken") for i in range(1, 5)]).by_plan
    assert [p.key for p in find_mistakes(broken)] == ["broken"]
    assert broken[0].sentence.startswith("계획을 어긴 매매 4건 중 0건 수익")


def test_early_exits():
    buys = [case(1, 0.1, post=0.25), case(2, 0.05, post=0.08), case(3, -0.1, post=-0.1)]
    buys += [case(4, 0.1, post=0.01), case(5, 0.1)]  # 5: 아직 20거래일 안 지남
    e = build_report(buys).early_exits
    assert e.n == 4
    assert e.early == 2
    assert e.good == 1
    assert e.rate == pytest.approx(0.5)
    assert [c.id for c in e.worst] == [1, 2]


def test_holding_losers_longer():
    cases = [case(i, 0.1, held=5) for i in range(1, 4)] + [
        case(i, -0.1, held=40) for i in range(4, 7)
    ]
    h = holding(cases)
    assert (h.win_days, h.loss_days) == (5, 40)
    assert h.holds_losers
    assert not holding(cases[:2] + cases[3:]).holds_losers  # 수익 2건이면 판단 보류


def test_sell_groups_rebound():
    sells = [
        SellCase(i, "IONQ", TODAY, frozenset({"fear"}), -0.05, post)
        for i, post in enumerate([0.2, 0.1, -0.08, None])
    ]
    g = build_report([], sells).sells
    assert len(g) == 1
    assert (g[0].key, g[0].n, g[0].rebounds, g[0].drops) == ("fear", 3, 2, 1)


def test_ticker_groups_need_two_trades():
    buys = [case(1, 0.1, ticker="AAPL"), case(2, -0.1), case(3, -0.2)]
    assert [p.key for p in build_report(buys).by_ticker] == ["IONQ"]


# ---- 계획 준수 ----

CLOSED = {"status": "closed", "avg_entry": 100.0, "max_drawdown": -0.08, "holding_days": 20}


def test_plan_status_none_without_plan_or_before_close():
    assert plan_status(CLOSED, None, None) == "none"
    assert plan_status({**CLOSED, "status": "open"}, 90.0, 30) is None


def test_plan_status_stop():
    assert plan_status(CLOSED, 90.0, None) == "kept"  # 최저 -8%, 손절 -10%
    assert plan_status(CLOSED, 94.0, None) == "kept"  # 손절 -6%, 3% 여유 안
    assert plan_status(CLOSED, 97.0, None) == "broken"  # 손절 -3%인데 -8%까지 버팀
    assert plan_status({**CLOSED, "max_drawdown": None}, 90.0, None) is None


def test_plan_status_horizon():
    assert plan_status(CLOSED, None, 10) == "kept"  # 20일 = 계획의 2배까지는 허용
    assert plan_status(CLOSED, None, 9) == "broken"
    assert plan_status(CLOSED, 90.0, 5) == "broken"


# ---- 보류 ----


def skip(id: int, fwd: float | None, verdict: str = "against") -> SkipCase:
    return SkipCase(id, "IONQ", TODAY - timedelta(days=100 - id), verdict, fwd, None)


def test_skip_groups_by_verdict():
    skips = [skip(1, -0.1), skip(2, -0.02), skip(3, 0.12), skip(4, 0.03, "caution"), skip(5, None)]
    r = build_report([], skips=skips)
    assert (r.skips.n, r.skips.good, r.skips.missed, r.skips.tracking) == (4, 2, 1, 1)
    assert [g.key for g in r.skips_by_verdict] == ["caution", "against"]  # VERDICTS 순
    against = r.skips_by_verdict[1]
    assert (against.n, against.good, against.tracking) == (3, 2, 1)
    assert against.good_rate == pytest.approx(2 / 3)
    assert [s.id for s in r.recent_skips] == [5, 4, 3, 2, 1]


def test_overriding_verdict_is_a_mistake_but_following_is_not():
    overrode = [case(i, -0.2, followed=False) for i in range(1, 5)]
    followed = [case(i, -0.2, followed=True) for i in range(5, 9)]
    unknown = [case(9, 0.1)]
    r = build_report([*overrode, *followed, *unknown])
    assert [(p.key, p.stats.n) for p in r.by_follow] == [("followed", 4), ("overrode", 4)]
    keys = [p.key for p in r.mistakes]
    assert "overrode" in keys and "followed" not in keys
    by_key = {p.key: p for p in r.mistakes}
    assert by_key["overrode"].sentence.startswith("판정을 거스른 매매 4건 중 0건 수익")


def test_case_from_decision_sets_followed():
    from app.models import Decision
    from app.services.engine.case import case_from_decision

    def d(verdict, action, side="buy"):
        return Decision(
            id=1, ticker="A", side=side, source="consult", decided_on=TODAY, reason_tags=[],
            verdict=verdict, user_action=action,
        )  # fmt: skip

    assert case_from_decision(d("against", "executed")).followed is False
    assert case_from_decision(d("recommend", "executed")).followed is True
    assert case_from_decision(d("recommend", "skipped", side="sell")).followed is False
    assert case_from_decision(d("caution", "executed")).followed is None
    assert case_from_decision(d("against", "pending")).followed is None
