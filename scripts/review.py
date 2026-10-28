"""주간 회고 출력: 지난 7일의 상담·판정 거스른 결정·청산·보유 점검을 텍스트로.

홈 화면의 '지난 7일' 카드와 같은 내용이다. 월요일 아침 cron 메일로 받아 보려고 따로 둔다.
시세는 캐시만 쓰므로 scripts/recompute.py 뒤에 돌려야 최신이다.

  uv run python scripts/review.py        # 지난 7일
  uv run python scripts/review.py 14     # 지난 14일

crontab 예 (월요일 오전 8시 30분, 한국 시간):
  30 8 * * 1 cd /path/to/investing && uv run python scripts/review.py
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.market.prices import PriceService  # noqa: E402
from app.services.review import DAYS, review_lines  # noqa: E402
from app.services.weekly import weekly_review  # noqa: E402


def main(args: list[str]) -> int:
    days = int(args[0]) if args else DAYS
    with SessionLocal() as db:
        review = weekly_review(db, PriceService(db), date.today(), days)
    print("\n".join(review_lines(review)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
