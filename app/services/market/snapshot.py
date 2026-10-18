"""결정 시점의 시장 상황 지표. 당일 종가는 결정 시점에 알 수 없으므로 전 거래일까지만 사용한다."""

from datetime import date
from typing import Any

import pandas as pd

MIN_BARS = 21


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def _ret(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


def _vs_ma(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] / s.iloc[-n:].mean() - 1) if len(s) >= n else None


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or pd.isna(x) else round(x, nd)


def build_snapshot(adj: pd.Series, spy_adj: pd.Series, on: date) -> dict[str, Any] | None:
    """adj/spy_adj: 날짜(date) 인덱스의 수정종가 시리즈."""
    past = adj[adj.index < on]
    if len(past) < MIN_BARS:
        return None
    spy_past = spy_adj[spy_adj.index < on]
    last_rsi = rsi(past).iloc[-1]
    return {
        "as_of": past.index[-1].isoformat(),
        "ret_5d": _r(_ret(past, 5)),
        "ret_20d": _r(_ret(past, 20)),
        "from_52w_high": _r(float(past.iloc[-1] / past.iloc[-252:].max() - 1)),
        "vs_ma20": _r(_vs_ma(past, 20)),
        "vs_ma50": _r(_vs_ma(past, 50)),
        "vs_ma200": _r(_vs_ma(past, 200)),
        "rsi14": _r(float(last_rsi), 1),
        "spy_ret_20d": _r(_ret(spy_past, 20)),
        "spy_vs_ma200": _r(_vs_ma(spy_past, 200)),
    }
