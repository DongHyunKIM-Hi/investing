"""주간 회고 재료를 DB에서 모은다. 홈 화면과 scripts/review.py가 같이 쓴다."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Decision
from app.services.engine.case import case_from_decision
from app.services.holdings import watches
from app.services.market.prices import PriceService
from app.services.review import DAYS, Review, build_review
from app.services.scorecard import judged_from_decision


def weekly_review(db: Session, prices: PriceService, today: date, days: int = DAYS) -> Review:
    judged = [
        judged_from_decision(d)
        for d in db.scalars(
            select(Decision).where(Decision.source == "consult", Decision.verdict.is_not(None))
        )
    ]
    buys = [
        case_from_decision(d)
        for d in db.scalars(
            select(Decision).where(Decision.side == "buy", Decision.outcome.is_not(None))
        )
        if (d.outcome or {}).get("status") == "closed"
    ]
    untagged = db.scalar(
        select(func.count()).select_from(Decision).where(Decision.tagged_at.is_(None))
    )
    return build_review(judged, buys, watches(db, prices, today), untagged or 0, today, days)
