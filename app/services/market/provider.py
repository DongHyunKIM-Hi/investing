import logging
from datetime import date, timedelta
from typing import Protocol

import pandas as pd
import yfinance as yf


class MarketDataProvider(Protocol):
    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """date 인덱스, 컬럼 close / adj_close / volume. end 포함."""
        ...

    def next_earnings(self, ticker: str) -> date | None:
        """다음 실적발표 예정일. 모르면 None (상담 시점 경고용, 과거 매매에는 쓰지 않는다)."""
        ...

    def splits(self, ticker: str) -> list[tuple[date, float]]:
        """주식 분할 이력 (분할일, 비율). 2:1 분할 = 2.0."""
        ...


log = logging.getLogger(__name__)


class YahooProvider:
    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        raw = yf.Ticker(ticker).history(start=start, end=end + timedelta(days=1), auto_adjust=False)
        if raw.empty:
            return pd.DataFrame(columns=["close", "adj_close", "volume"])
        df = pd.DataFrame(
            {
                "close": raw["Close"].astype(float),
                "adj_close": raw["Adj Close"].astype(float),
                "volume": raw["Volume"].fillna(0).astype("int64"),
            }
        )
        df.index = [ts.date() for ts in raw.index]
        return df.dropna()

    def next_earnings(self, ticker: str) -> date | None:
        try:
            dates = yf.Ticker(ticker).calendar.get("Earnings Date") or []
        except Exception:  # 비공식 API라 형식이 자주 바뀐다: 경고 하나 못 띄우는 정도로 끝낸다
            log.warning("earnings date lookup failed: %s", ticker, exc_info=True)
            return None
        upcoming = sorted(d for d in dates if isinstance(d, date) and d >= date.today())
        return upcoming[0] if upcoming else None

    def splits(self, ticker: str) -> list[tuple[date, float]]:
        raw = yf.Ticker(ticker).splits
        days = [ts.date() for ts in pd.DatetimeIndex(raw.index)]
        return [(d, float(r)) for d, r in zip(days, raw.to_numpy(), strict=True) if float(r) > 0]
