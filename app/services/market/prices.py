"""시세 조회 + price_bars 캐시. 이미 받은 기간은 다시 요청하지 않는다."""

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PriceBar, PriceCoverage
from app.services.market.provider import MarketDataProvider, YahooProvider

BENCHMARK = "SPY"


class PriceService:
    def __init__(self, db: Session, provider: MarketDataProvider | None = None):
        self.db = db
        self.provider = provider or YahooProvider()

    def bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        end = min(end, date.today())
        self._ensure(ticker, start, end)
        rows = self.db.execute(
            select(PriceBar.day, PriceBar.close, PriceBar.adj_close)
            .where(PriceBar.ticker == ticker, PriceBar.day >= start, PriceBar.day <= end)
            .order_by(PriceBar.day)
        ).all()
        df = pd.DataFrame(rows, columns=["day", "close", "adj_close"])
        return df.set_index("day")

    def _ensure(self, ticker: str, start: date, end: date) -> None:
        cov = self.db.get(PriceCoverage, ticker)
        if cov and cov.start <= start and cov.end >= end:
            return
        # 구멍이 생기지 않도록 기존 범위와 합친 전체 구간을 받는다
        fetch_start = min(start, cov.start) if cov else start
        fetch_end = max(end, cov.end) if cov else end
        df = self.provider.daily_bars(ticker, fetch_start, fetch_end)
        if not df.empty:
            stmt = insert(PriceBar).values(
                [
                    {
                        "ticker": ticker,
                        "day": d,
                        "close": r.close,
                        "adj_close": r.adj_close,
                        "volume": int(r.volume),
                    }
                    for d, r in df.iterrows()
                ]
            )
            self.db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["ticker", "day"],
                    set_={
                        "close": stmt.excluded.close,
                        "adj_close": stmt.excluded.adj_close,
                        "volume": stmt.excluded.volume,
                    },
                )
            )
        if cov:
            cov.start, cov.end = fetch_start, fetch_end
        else:
            self.db.add(PriceCoverage(ticker=ticker, start=fetch_start, end=fetch_end))
        self.db.flush()
