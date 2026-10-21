"""매도 상담: 매도 Case 변환, 패턴 문장, 매도 점검, 보유 포지션, LLM 도구 입력."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.models import Decision
from app.services.engine.case import Case, case_from_decision
from app.services.engine.checks import SellPlan, check_sell
from app.services.engine.similarity import find_similar
from app.services.engine.verdict import decide, patterns
from app.services.llm.consult import (
    RECORD_SELL_TOOL,
    SELL_SYSTEM_PROMPT,
    SellRecordInput,
    situation_text,
    to_turn,
)
from app.services.llm.keywords import suggest_tags
from app.services.position import PricedFill, manual_position, open_lots, summarize_position
from app.services.report import SkipCase, build_report
from app.taxonomy import SELL_REASONS

TODAY = date(2026, 10, 21)
DROP = {"ret_5d": -0.18, "ret_20d": -0.25, "from_52w_high": -0.4, "vs_ma50": -0.2, "rsi14": 25}


def sell_decision(id: int, post: float | None, **kw) -> Decision:
    sold = kw.pop("sold", TODAY - timedelta(days=200 - id))
    outcome = kw.pop("outcome", None)
    if outcome is None and post is not None:
        outcome = {
            "sold_on": sold.isoformat(),
            "realized_return": kw.pop("realized", -0.1),
            "post_exit_20d": post,
            "post_exit_on": (sold + timedelta(days=28)).isoformat(),
        }
    return Decision(
        id=id,
        ticker=kw.pop("ticker", "IONQ"),
        side="sell",
        source=kw.pop("source", "csv"),
        decided_on=sold,
        reason_tags=kw.pop("tags", ["fear"]),
        emotion=kw.pop("emotion", "fear"),
        context=kw.pop("context", dict(DROP)),
        outcome=outcome,
    )


def test_sell_case_win_means_price_did_not_rise():
    c = case_from_decision(sell_decision(1, -0.08))
    assert c.side == "sell"
    assert c.win is True
    assert c.ret == pytest.approx(0.08)  # 판 덕에 피한 손실
    assert c.closed_on == c.decided_on + timedelta(days=28)
    assert c.context["position_return"] == -0.1

    rebound = case_from_decision(sell_decision(2, 0.12))
    assert rebound.win is False and rebound.ret == pytest.approx(-0.12)


def test_sell_case_unresolved_until_20_days():
    c = case_from_decision(sell_decision(1, None, outcome={"sold_on": "2026-10-01"}))
    assert c.win is None and c.closed_on is None
    # 예전 결과처럼 확정일이 없으면 넉넉히 31일 뒤로 본다
    old = sell_decision(2, 0.05, outcome={"post_exit_20d": 0.05, "realized_return": 0.2})
    assert case_from_decision(old).closed_on == old.decided_on + timedelta(days=31)


def test_skipped_sell_is_not_a_resolved_case():
    d = sell_decision(1, None, outcome={"status": "skipped", "fwd_20d": 0.1}, source="consult")
    assert case_from_decision(d).win is None


def _query(**kw) -> Case:
    ctx = {**DROP, "position_return": -0.15}
    return Case(
        id=0,
        ticker="IONQ",
        decided_on=TODAY,
        tags=frozenset({"fear"}),
        emotion="fear",
        context=ctx,
        side="sell",
        **kw,
    )


def test_sell_verdict_against_when_fear_sells_rebounded():
    history = [case_from_decision(sell_decision(i, 0.15)) for i in range(1, 6)]
    v = decide(find_similar(_query(), history))
    assert v.n == 5
    assert v.label == "against"
    assert v.early_exit_rate == pytest.approx(1.0)


def test_sell_patterns_only_compare_sells():
    sells = [case_from_decision(sell_decision(i, p)) for i, p in [(1, 0.2), (2, 0.1), (3, -0.05)]]
    buy = Case(
        id=9,
        ticker="IONQ",
        decided_on=TODAY - timedelta(days=50),
        tags=frozenset({"fear"}),
        closed_on=TODAY - timedelta(days=40),
        win=True,
        ret=0.3,
    )
    by_key = {(p.kind, p.key): p for p in patterns(_query(), [*sells, buy])}
    tag = by_key[("tag", "fear")]
    assert tag.stats.n == 3
    assert tag.sentence.startswith(
        "'떨어질까 불안해서' 이유로 판 매도 3건 중 2건은 판 뒤 더 올랐어요"
    )
    assert "판 뒤 20일 중앙값 +10.0%" in tag.sentence
    assert by_key[("emotion", "fear")].stats.n == 3
    assert by_key[("position", "in_loss")].stats.n == 3
    assert by_key[("situation", "oversold")].label.startswith("RSI 30 이하")


# ---- 매도 점검 ----


def flags(plan: SellPlan, ctx: dict) -> list[tuple[str, str]]:
    return [(f.level, f.text) for f in check_sell(plan, ctx)]


def test_check_sell_stop_hit_is_good():
    plan = SellPlan(frozenset({"stop_loss"}), "calm", -0.12, 20, buy_target=40, buy_stop=27)
    out = flags(plan, {"last_close": 26.0, "ret_5d": -0.2, "rsi14": 25})
    assert out[0][0] == "good" or all(level != "high" for level, _ in out)
    assert any("손절가" in t and level == "good" for level, t in out)
    assert not any("급락" in t for _, t in out)  # 계획대로 손절이면 공포 매도로 보지 않음


def test_check_sell_fear_between_plan():
    plan = SellPlan(
        frozenset({"fear"}), "fear", -0.05, 3, buy_target=40, buy_stop=27, buy_horizon=30
    )
    out = flags(plan, {"last_close": 30.0, "ret_5d": -0.16, "rsi14": 28})
    texts = " ".join(t for _, t in out)
    assert out[0][0] == "high"
    assert "불안해서" in texts and "급락" in texts and "RSI 28" in texts
    assert "산 지 3일 만" in texts


def test_check_sell_tiny_profit_and_no_position():
    out = flags(SellPlan(frozenset({"take_profit"}), None, 0.02, 10), {"last_close": 10})
    assert any("+2.0%에서 익절" in t for _, t in out)
    none = flags(SellPlan(frozenset({"boredom"}), None, None, None), {})
    assert any("보유 기록이 없어" in t for _, t in none)
    assert any("산 이유는 아직" in t for _, t in none)


# ---- 보유 포지션 ----


def pf(id: int, side: str, day: int, price: str, qty: str) -> PricedFill:
    return PricedFill(id, side, TODAY - timedelta(days=100 - day), 0, D(price), D(qty))


def test_open_lots_fifo_and_average():
    fills = [
        pf(1, "buy", 0, "10", "10"),
        pf(2, "buy", 10, "20", "10"),
        pf(3, "sell", 20, "30", "15"),
    ]
    lots = open_lots(fills)
    assert [(lot.buy_fill_id, lot.quantity) for lot in lots] == [(2, D("5"))]
    pos = summarize_position(lots, 25.0, TODAY)
    assert pos is not None
    assert pos["avg_cost"] == 20 and pos["position_qty"] == 5
    assert pos["position_return"] == pytest.approx(0.25)
    assert pos["held_days"] == 90


def test_no_position_when_all_sold():
    fills = [pf(1, "buy", 0, "10", "10"), pf(2, "sell", 5, "12", "10")]
    assert summarize_position(open_lots(fills), 12.0, TODAY) is None
    assert manual_position(20.0, 15.0)["position_return"] == pytest.approx(-0.25)


# ---- LLM ----


def test_sell_tool_and_prompt():
    enum = RECORD_SELL_TOOL["input_schema"]["properties"]["reason_tags"]["items"]["enum"]
    assert enum == list(SELL_REASONS)
    assert "{" not in SELL_SYSTEM_PROMPT  # 포맷 자리표시자가 모두 채워졌는지
    assert "record_decision" in SELL_SYSTEM_PROMPT


def test_to_turn_sell_record():
    tool = {
        "type": "tool_use",
        "id": "t1",
        "name": "record_decision",
        "input": {
            "reason_tags": ["fear"],
            "emotion": "fear",
            "conviction": 2,
            "avg_cost": None,
            "reason_summary": "더 떨어질까 봐",
        },
    }
    turn = to_turn([tool], side="sell")
    assert isinstance(turn.record, SellRecordInput)
    # 매수 이유 코드로 매도를 기록하면 검증 실패
    bad = {**tool, "input": {**tool["input"], "reason_tags": ["momentum_chase"]}}
    assert to_turn([bad], side="sell").error is not None


def test_situation_text_includes_position_and_buy_plan():
    ctx = {
        "last_close": 30.0,
        "avg_cost": 25.0,
        "position_return": 0.2,
        "position_qty": 10.0,
        "opened_on": "2026-09-01",
        "held_days": 50,
        "buy_plan": {
            "decided_on": "2026-09-01",
            "reason_tags": ["theme"],
            "target_price": 40.0,
            "stop_price": None,
            "horizon_days": 30,
            "reason_text": "양자 테마",
        },
    }
    text = situation_text("IONQ", ctx, "sell")
    assert "평단 $25.00" in text and "+20.0%" in text
    assert "목표가 $40" in text and "손절가 없음" in text and "30일" in text
    assert "양자 테마" in text
    assert "평균 매수가를 물어보세요" in situation_text("IONQ", {"last_close": 30.0}, "sell")
    assert "보유 정보" not in situation_text("IONQ", ctx, "buy")


def test_sell_keywords():
    reasons, emotion = suggest_tags("더 떨어질까 봐 불안해서 손절하려고요", "sell")
    assert "fear" in reasons and "stop_loss" in reasons
    assert emotion == "fear"


# ---- 리포트: 안 판 것 ----


def test_report_holds_are_judged_inversely():
    skips = [
        SkipCase(1, "IONQ", TODAY, "against", 0.1, None, side="sell"),  # 안 팔고 올랐음 → 잘 버팀
        SkipCase(2, "IONQ", TODAY, "against", -0.12, None, side="sell"),  # 안 팔고 떨어짐 → 껄
        SkipCase(3, "IONQ", TODAY, "against", 0.1, None),  # 안 사고 올랐음 → 껄
    ]
    r = build_report([], skips=skips)
    assert (r.holds.n, r.holds.good, r.holds.missed) == (2, 1, 1)
    assert (r.skips.n, r.skips.good, r.skips.missed) == (1, 0, 1)
    assert [g.key for g in r.holds_by_verdict] == ["against"]
    assert len(r.recent_skips) == 3
