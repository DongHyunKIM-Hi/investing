"""배치 작업 실행 기록: 화면 버튼이든 cron이든 재계산이 언제 마지막으로 돌았는지 남긴다."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobRun

RECOMPUTE = "recompute"
STALE_AFTER = timedelta(days=3)  # 주말을 넘겨도 사흘 안에는 한 번 돌아야 한다
KEEP = 200  # 오래된 기록은 지운다


def record_run(db: Session, name: str, warnings: list[str]) -> JobRun:
    run = JobRun(
        name=name,
        ran_at=datetime.now(UTC),
        ok=not warnings,
        message=" / ".join(warnings)[:2000] or None,
    )
    db.add(run)
    db.flush()
    old = db.scalars(
        select(JobRun).where(JobRun.name == name).order_by(JobRun.ran_at.desc()).offset(KEEP)
    ).all()
    for row in old:
        db.delete(row)
    return run


def last_run(db: Session, name: str) -> JobRun | None:
    return db.scalars(
        select(JobRun).where(JobRun.name == name).order_by(JobRun.ran_at.desc()).limit(1)
    ).first()


def is_stale(run: JobRun | None, now: datetime) -> bool:
    return run is None or now - run.ran_at > STALE_AFTER
