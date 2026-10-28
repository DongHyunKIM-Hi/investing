from datetime import date

from app.services.watch import check_position, sort_watches

TODAY = date(2026, 10, 28)
OPENED = date(2026, 9, 1)
PLAN = {"decision_id": 1, "target_price": 60.0, "stop_price": 40.0, "horizon_days": 30}


def levels(w) -> list[str]:
    return [a.level for a in w.alerts]


def texts(w) -> str:
    return " | ".join(a.text for a in w.alerts)


def test_stop_breached_is_high():
    w = check_position("IONQ", 10, 50.0, OPENED, 38.0, TODAY, PLAN, TODAY)
    assert "high" in levels(w) and "손절가 $40 아래" in texts(w)
    assert w.position_return == -0.24 and w.held_days == 57


def test_target_hit_is_good_and_horizon_overrun_is_mid():
    w = check_position("IONQ", 10, 50.0, OPENED, 61.0, TODAY, PLAN, TODAY)
    assert "good" in levels(w) and "목표가 $60에 도달" in texts(w)
    assert "보유 기간 30일을 넘겼어요 (현재 57일)" in texts(w)


def test_horizon_counts_from_plan_date_not_oldest_lot():
    plan = {**PLAN, "decided_on": "2026-10-20"}  # 오래된 lot(9/1)에 최근 상담으로 추가 매수
    w = check_position("IONQ", 10, 50.0, OPENED, 50.0, TODAY, plan, TODAY)
    assert "보유 기간" not in texts(w)


def test_near_levels_are_mid():
    near_stop = check_position("A", 1, 50.0, TODAY, 41.0, TODAY, PLAN, TODAY)
    assert "손절가 $40에 가까워요" in texts(near_stop)
    near_target = check_position("A", 1, 50.0, TODAY, 58.5, TODAY, PLAN, TODAY)
    assert "목표가 $60에 가까워요" in texts(near_target)


def test_no_plan_and_big_loss():
    w = check_position("A", 1, 50.0, OPENED, 39.0, TODAY, None, TODAY)
    assert levels(w) == ["info", "high"]
    calm = check_position("A", 1, 50.0, OPENED, 55.0, TODAY, None, TODAY)
    assert levels(calm) == ["info"]


def test_plan_without_stop():
    w = check_position("A", 1, 50.0, TODAY, 39.0, TODAY, {"target_price": 60.0}, TODAY)
    assert "손절가 없이 들고 있어요" in texts(
        w
    ) and "평가 손실 -22.0%인데 손절 기준이 없어요" in texts(w)


def test_no_price_still_checks_plan_shape():
    w = check_position("A", 1, 50.0, OPENED, None, None, PLAN, TODAY)
    assert w.position_return is None and levels(w) == ["mid"]  # 기간 초과만


def test_sort_urgent_first_then_worst_return():
    breached = check_position("A", 1, 50.0, OPENED, 38.0, TODAY, PLAN, TODAY)
    fine = check_position("B", 1, 50.0, TODAY, 52.0, TODAY, PLAN, TODAY)
    noplan_loss = check_position("C", 1, 50.0, OPENED, 30.0, TODAY, None, TODAY)
    noplan_ok = check_position("D", 1, 50.0, OPENED, 51.0, TODAY, None, TODAY)
    # 급한 둘(A·C) 중에서는 손실이 큰 C가 먼저
    order = [w.ticker for w in sort_watches([fine, noplan_ok, noplan_loss, breached])]
    assert order == ["C", "A", "D", "B"]
