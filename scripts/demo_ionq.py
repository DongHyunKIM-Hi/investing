"""IONQ 데모 데이터: 규칙으로 고른 가상의 매매 이력 + 이유 태그.

결과를 보고 고르지 않도록 진입 규칙만 정하고, 체결가는 실제 IONQ 종가를 쓴다.
- 추격 매수: 직전 5일 +20% 이상 급등 신호 다음 거래일에 매수, 15거래일 뒤 매도 (FOMO)
- 저가 매수: 52주 고점 대비 -45% 이하 & 직전 20일 -20% 이하 신호 다음 거래일 매수, 30거래일 뒤 매도
- 신호 사이 간격: 추격 40일, 저가 60일 (같은 파동에서 중복 진입 방지)
- 보유 중에 뜬 신호는 건너뛴다 (한 번에 한 포지션)

사용법:
  uv run python scripts/demo_ionq.py make-csv          # samples/sample_ionq_kr.csv 생성
  docker compose exec db createdb -U ggeol ggeol_demo
  export DATABASE_URL=postgresql+psycopg://ggeol:ggeol@localhost:5432/ggeol_demo
  uv run alembic upgrade head
  uv run python scripts/demo_ionq.py seed              # 샘플 CSV 2개 가져오기 + IONQ 이유 태깅
"""

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
from sqlalchemy import make_url, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Decision  # noqa: E402
from app.services.importer.csv_parser import parse_fills, read_csv  # noqa: E402
from app.services.importer.pipeline import import_fills  # noqa: E402
from app.services.importer.presets import guess_mapping  # noqa: E402
from app.services.market.prices import PriceService  # noqa: E402
from app.services.recompute import recompute_tickers  # noqa: E402

START = date(2024, 1, 1)
BUDGET = 2000  # 1회 매수 금액(달러)
FEE_RATE = 0.0025
CSV_PATH = ROOT / "samples" / "sample_ionq_kr.csv"
SAMPLES = [ROOT / "samples" / "sample_trades_kr.csv", CSV_PATH]


def _signals(mask: pd.Series, gap_days: int) -> list[date]:
    picked: list[date] = []
    for d in mask[mask].index:
        if not picked or (d - picked[-1]).days >= gap_days:
            picked.append(d)
    return picked


def make_csv() -> None:
    raw = yf.Ticker("IONQ").history(start="2022-06-01", auto_adjust=False)
    close = pd.Series(raw["Close"].to_numpy(), index=[ts.date() for ts in raw.index])
    r5, r20 = close / close.shift(5) - 1, close / close.shift(20) - 1
    from_high = close / close.rolling(252, min_periods=60).max() - 1
    recent = pd.Series(close.index >= START, index=close.index)

    entries = sorted(
        [(d, 15) for d in _signals((r5 >= 0.20) & recent, 40)]
        + [(d, 30) for d in _signals((from_high <= -0.45) & (r20 <= -0.20) & recent, 60)]
    )
    days = list(close.index)
    rows: list[tuple[date, str, float, int]] = []
    busy_until = -1  # 한 번에 한 포지션만: 겹치면 FIFO가 다른 매매의 매도를 짝지어 버린다
    for sig, hold in entries:
        i = days.index(sig) + 1  # 신호는 장 마감 후 확인 → 다음 거래일에 매수
        if i >= len(days) or i <= busy_until:
            continue
        price = float(close.iloc[i])
        qty = max(1, int(BUDGET / price))
        rows.append((days[i], "해외주식매수", price, qty))
        busy_until = i + hold
        if i + hold < len(days):  # 아직 매도 시점이 안 왔으면 보유 중
            rows.append((days[i + hold], "해외주식매도", float(close.iloc[i + hold]), qty))

    rows.sort(key=lambda r: r[0], reverse=True)  # 증권사처럼 최신순
    lines = ["거래일자,종목명,매매구분,체결단가,체결수량,수수료,제세금"]
    for d, side, price, qty in rows:
        fee = round(price * qty * FEE_RATE, 2)
        lines.append(f"{d:%Y.%m.%d},아이온큐(IONQ),{side},{price:.2f},{qty},{fee},0")
    CSV_PATH.write_bytes(("\n".join(lines) + "\n").encode("cp949"))
    print(f"{CSV_PATH.relative_to(ROOT)}: 체결 {len(rows)}건")


def _demo_tags(ctx: dict) -> tuple[list[str], str, str] | None:
    """매수 규칙을 그대로 되짚어 '그때 왜 샀는지'를 붙인다 (데모용 가정)."""
    if (ctx.get("ret_5d") or 0) >= 0.18:
        return ["momentum_chase", "theme"], "fomo", "양자 테마 며칠째 급등, 더 오를 것 같아서"
    if (ctx.get("from_52w_high") or 0) <= -0.40:
        return ["dip_buy"], "calm", "고점 대비 반토막, 이 정도면 싸다고 봄"
    return None


def seed() -> None:
    db_name = make_url(settings.database_url).database or ""
    if "demo" not in db_name:
        sys.exit(f"데모 DB가 아닙니다 ({db_name}). DATABASE_URL을 ..._demo로 지정하세요.")

    with SessionLocal() as db:
        tickers: set[str] = set()
        for path in SAMPLES:
            df = read_csv(path.read_bytes())
            mapping = guess_mapping(list(df.columns))
            assert mapping is not None, path
            summary = import_fills(db, parse_fills(df, mapping).fills)
            tickers |= summary.tickers
            print(f"{path.name}: 새 체결 {summary.new_fills}, 중복 {summary.duplicates}")
        for w in recompute_tickers(db, tickers, PriceService(db)):
            print("경고:", w)

        tagged = 0
        for d in db.scalars(
            select(Decision).where(Decision.ticker == "IONQ", Decision.side == "buy")
        ):
            if d.tagged_at is None and (tags := _demo_tags(d.context or {})):
                d.reason_tags, d.emotion, d.reason_text = tags
                d.tagged_at = datetime.now(UTC)
                tagged += 1
        db.commit()
        print(f"IONQ 매수 {tagged}건 이유 태깅")


if __name__ == "__main__":
    commands = {"make-csv": make_csv, "seed": seed}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
