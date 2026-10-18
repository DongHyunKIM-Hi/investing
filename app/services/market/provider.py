from datetime import date, timedelta
from typing import Protocol

import pandas as pd
import yfinance as yf


class MarketDataProvider(Protocol):
    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """date 인덱스, 컬럼 close / adj_close / volume. end 포함."""
        ...


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
