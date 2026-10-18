from datetime import date, timedelta

import pandas as pd
import pytest

from app.services.market.snapshot import build_snapshot, rsi

START = date(2025, 1, 1)


def series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=[START + timedelta(days=i) for i in range(len(values))])


def test_rsi_extremes():
    assert rsi(series([float(i) for i in range(1, 40)])).iloc[-1] == pytest.approx(100)
    assert rsi(series([float(i) for i in range(40, 1, -1)])).iloc[-1] == pytest.approx(0)


def test_snapshot_excludes_decision_day():
    prices = series([100.0] * 30 + [200.0])  # 결정 당일 급등은 반영되면 안 된다
    on = START + timedelta(days=30)
    snap = build_snapshot(prices, prices, on)
    assert snap is not None
    assert snap["as_of"] == (on - timedelta(days=1)).isoformat()
    assert snap["ret_5d"] == 0
    assert snap["vs_ma200"] is None  # 데이터 부족


def test_snapshot_momentum_chase_signals():
    prices = series([100.0] * 25 + [100.0 + 5 * i for i in range(1, 6)])
    snap = build_snapshot(prices, prices, START + timedelta(days=30))
    assert snap is not None
    assert snap["ret_5d"] == pytest.approx(0.25)
    assert snap["from_52w_high"] == 0
    assert snap["rsi14"] > 80


def test_snapshot_requires_history():
    assert (
        build_snapshot(series([100.0] * 10), series([100.0] * 10), START + timedelta(days=10))
        is None
    )
