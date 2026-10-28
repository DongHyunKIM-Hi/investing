"""매일 한 번 돌리는 재계산: 전 종목 FIFO·결과 지표 + 보류한 상담 결정의 '샀다면' 추적.

화면의 '시세·결과 재계산' 버튼과 같은 일을 한다. 미국 장 마감 뒤(한국 아침)에 cron으로 돌리면
보류 건 20·60거래일 결과와 매도 후 20일 흐름이 사람이 누르지 않아도 쌓인다.

  uv run python scripts/recompute.py            # 전체
  uv run python scripts/recompute.py IONQ PLTR  # 일부 종목만 (보류 건 추적은 항상 전체)

crontab 예 (화~토 오전 8시, 한국 시간):
  0 8 * * 2-6 cd /path/to/investing && uv run python scripts/recompute.py >> data/recompute.log 2>&1
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import Fill  # noqa: E402
from app.services.market.prices import PriceService  # noqa: E402
from app.services.recompute import recompute_tickers, track_skipped  # noqa: E402


def main(only: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING)
    with SessionLocal() as db:
        tickers = [t.upper() for t in only] or list(db.scalars(select(Fill.ticker).distinct()))
        prices = PriceService(db)
        warnings = recompute_tickers(db, tickers, prices)
        warnings += track_skipped(db, prices)
        db.commit()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{stamp}] 재계산 완료: 종목 {len(tickers)}개, 경고 {len(warnings)}건")
    for w in warnings:
        print("  -", w)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
