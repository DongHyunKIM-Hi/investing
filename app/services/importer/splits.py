"""주식 분할 보정 (순수 함수).

시세(yfinance close)는 분할이 반영된 값인데 CSV 체결가·수량은 그때 기준이다.
계산할 때만 체결을 '지금 기준'으로 바꾼다: 수량 × 비율, 가격 ÷ 비율 (수수료는 그대로).
분할 날짜(ex-date) 당일 이후 체결은 이미 분할 후 기준이다.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

Split = tuple[date, float]  # (분할일, 비율). 2:1 분할 = 2.0, 1:10 병합 = 0.1


def split_factor(trade_date: date, splits: Sequence[Split]) -> Decimal:
    """trade_date 이후에 있었던 분할 비율의 곱. 분할이 없으면 1."""
    factor = Decimal(1)
    for day, ratio in splits:
        if day > trade_date and ratio > 0:
            factor *= Decimal(str(ratio))
    return factor
