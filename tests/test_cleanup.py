from datetime import date, timedelta

from app.models import Decision, Fill
from app.services.cleanup import find_loose

TODAY = date(2026, 10, 29)


def d(id: int, days_ago: int, verdict=None, action="pending", source="consult", fills=0):
    dec = Decision(
        id=id, ticker="A", side="buy", source=source, decided_on=TODAY - timedelta(days=days_ago),
        reason_tags=[], verdict=verdict, user_action=action,
    )  # fmt: skip
    dec.fills = [
        Fill(id=100 + i, ticker="A", side="buy", trade_date=dec.decided_on) for i in range(fills)
    ]
    return dec


def test_categories_and_grace_periods():
    loose = find_loose(
        [
            d(1, 0),  # 오늘 시작, 아직 진행 중
            d(2, 1),  # 어제 멈춤 → 미완료
            d(3, 2, "caution", "executed"),  # 샀다는데 체결 없음, 아직 CSV 기다리는 중
            d(4, 3, "caution", "executed"),  # → 체결 없음
            d(5, 5, "caution", "executed", fills=1),  # 정상
            d(6, 0, "against", "pending"),  # 오늘 판정, 아직 고르는 중
            d(7, 1, "against", "pending"),  # → 미정
            d(8, 10, "against", "skipped"),  # 정상
            d(9, 30, None, "executed", source="csv"),  # CSV 결정은 대상 아님
        ],
        TODAY,
    )
    assert [x.id for x in loose.unfinished] == [2]
    assert [x.id for x in loose.unlinked] == [4]
    assert [x.id for x in loose.undecided] == [7]
    assert loose.count == 3


def test_sorted_recent_first_and_empty():
    loose = find_loose([d(1, 5), d(2, 3)], TODAY)
    assert [x.id for x in loose.unfinished] == [2, 1]
    assert find_loose([], TODAY).count == 0
