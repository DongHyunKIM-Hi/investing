"""시세 조회 + price_bars 캐시. 이미 받은 기간은 다시 요청하지 않는다."""

from datetime import date
from typing import Any

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

    def splits(self, ticker: str) -> list[tuple[date, float]]:
        """분할 이력. 캐시에 없으면 받아서 저장한다."""
        cov = self.db.get(PriceCoverage, ticker)
        if cov is not None and cov.splits is not None:
            return _parse_splits(cov.splits)
        found = self.provider.splits(ticker)
        if cov is not None:
            cov.splits = [[d.isoformat(), r] for d, r in found]
            self.db.flush()
        return found

    def cached_splits(self, ticker: str) -> list[tuple[date, float]]:
        """네트워크 없이 캐시에 있는 분할 이력만 (화면 목록용)."""
        cov = self.db.get(PriceCoverage, ticker)
        return _parse_splits(cov.splits) if cov is not None and cov.splits else []

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
        # 새 분할이 생겼을 수 있으므로 시세를 받을 때마다 갱신한다
        splits = [[d.isoformat(), r] for d, r in self.provider.splits(ticker)]
        if cov:
            cov.start, cov.end, cov.splits = fetch_start, fetch_end, splits
        else:
            self.db.add(
                PriceCoverage(ticker=ticker, start=fetch_start, end=fetch_end, splits=splits)
            )
        self.db.flush()


def _parse_splits(raw: list[list[Any]]) -> list[tuple[date, float]]:
    return [(date.fromisoformat(d), float(r)) for d, r in raw]
