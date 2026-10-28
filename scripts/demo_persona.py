"""'김껄' 페르소나 데모: 실제 시세 위에 규칙으로 만든 2년 반치 매매 + 이유·감정·계획 + 상담 판정.

IONQ 데모(demo_ionq.py)는 한 종목·두 규칙이라 엔진을 조정할 표본이 못 된다. 여기서는 15개 종목에
사람 행동을 흉내 낸 다섯 습관을 돌려 매수 60~100건을 만든다. 결과를 보고 고르지 않도록 진입·청산
규칙만 정하고, 체결가는 그날 실제 종가를 쓴다.

습관 (진입 → 청산)
- A 추격: 5일 +15%·RSI 65↑ → FOMO 매수. 손절 계획은 30%만. -12% 불안 매도 / +25% 익절 / 25일 지루함.
     -20%면 한 번 물타기(손실 만회 심리)
- B 저가: 52주 고점 -35%·20일 -15% → 차분히 매수, 손절 -15%·목표 +30%·60일.
     30%는 손절을 안 지키고 -25%까지 버팀
- C 기술: 50일선 위로 돌파(직전 10일은 아래) → 확신, 손절 -8%·목표 +20%·40일.
     5일 연속 50일선 아래면 '이유 소멸' 매도
- D 추천: 밈 종목을 무작위 날(1/120)에 커뮤니티 추천으로 매수, 계획 없음.
     -10% 불안 / +15% 익절 / 30일 지루함
- E 장기: AAPL·MSFT를 분기 첫 거래일에 사서 안 판다
2025-01부터는 앱을 쓰기 시작한 것으로 보고, 그 뒤 매수의 70%·매도의 50%를 '상담 후 실행'(판정 포함),
사지 않은 신호의 35%를 '상담 후 보류'로 만든다. 판정은 그 시점 이전에 끝난 매매만 보므로
지금 한꺼번에 돌려도 된다.

사용법 (DB 이름에 demo가 들어가야 함):
  docker compose exec db createdb -U ggeol ggeol_demo_persona
  export DATABASE_URL=postgresql+psycopg://ggeol:ggeol@localhost:5432/ggeol_demo_persona
  uv run alembic upgrade head
  uv run python scripts/demo_persona.py seed
"""

import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yfinance as yf
from sqlalchemy import make_url, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Decision  # noqa: E402
from app.services.consult import judge  # noqa: E402
from app.services.importer.csv_parser import ParsedFill  # noqa: E402
from app.services.importer.pipeline import import_fills  # noqa: E402
from app.services.jobs import RECOMPUTE, record_run  # noqa: E402
from app.services.market.prices import BENCHMARK, PriceService  # noqa: E402
from app.services.market.snapshot import build_snapshot  # noqa: E402
from app.services.recompute import recompute_tickers, track_skipped  # noqa: E402

TICKERS = [
    "TSLA", "PLTR", "AMD", "IONQ", "RGTI", "COIN", "SOFI", "AAPL", "MSFT", "HOOD",
    "RKLB", "ARM", "META", "GOOGL", "AMZN",
]  # fmt: skip
MEME = {"IONQ", "RGTI", "SOFI", "HOOD", "RKLB", "PLTR", "COIN", "ARM"}
LONG_TERM = ["AAPL", "MSFT"]
HISTORY_FROM = "2023-01-01"
TRADE_FROM = date(2024, 1, 2)
APP_FROM = date(2025, 1, 1)  # 이날부터 상담을 거친 것으로
BUDGET = 1500
FEE_RATE = 0.0025
MAX_OPEN = 6
SEED = 7

TEXTS = {
    "A": [
        "며칠째 급등, 지금 안 타면 놓칠 것 같아서",
        "테마가 뜨겁다. 더 간다",
        "커뮤니티 다 이 얘기뿐이라",
    ],
    "A2": ["물렸는데 평단 낮추면 금방 회복할 듯", "여기서 더 빠지겠어? 물타기"],
    "B": ["고점 대비 반토막, 이 정도면 싸다", "20일째 빠졌으니 반등 자리로 봄"],
    "C": ["50일선 돌파, 추세 전환으로 판단", "거래량 실린 돌파라 확신"],
    "D": ["유튜브에서 강력 추천", "단톡방에서 다들 산다길래", "지인이 내부 소식 있다고"],
    "E": ["분기 적립, 10년 볼 종목"],
}
SELL_EMOTION = {
    "fear": "fear", "take_profit": "calm", "boredom": "calm", "stop_loss": "calm",
    "target_hit": "confident", "thesis_broken": "calm", "need_cash": "calm",
}  # fmt: skip
SELL_TEXT = {
    "fear": "더 떨어질까 무서워서 던짐", "take_profit": "적당히 먹었으니 정리",
    "boredom": "안 움직여서 지겨움", "stop_loss": "손절 기준 도달, 규칙대로",
    "target_hit": "목표가 와서 익절", "thesis_broken": "추세 깨짐, 산 이유 사라짐",
    "need_cash": "기간 다 돼서 다른 종목으로",
}  # fmt: skip


@dataclass
class Trade:
    ticker: str
    side: str
    day: date
    price: float
    qty: int
    rule: str  # 매수: A A2 B C D E / 매도: 청산 이유 키
    tags: list[str] = field(default_factory=list)
    emotion: str = "calm"
    conviction: int = 3
    stop: float | None = None
    target: float | None = None
    horizon: int | None = None
    text: str = ""


@dataclass
class Position:
    rule: str
    entry_i: int
    entry: float
    qty: int
    stop: float | None
    target: float | None
    horizon: int
    honors_stop: bool = True
    averaged: bool = False
    below_ma50: int = 0


def _features(close: pd.Series) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    return pd.DataFrame(
        {
            "close": close,
            "r5": close / close.shift(5) - 1,
            "r20": close / close.shift(20) - 1,
            "from_high": close / close.rolling(252, min_periods=60).max() - 1,
            "ma50": close.rolling(50).mean(),
            "rsi": rsi,
        }
    )


def _load() -> dict[str, pd.DataFrame]:
    out = {}
    for t in TICKERS:
        tk = yf.Ticker(t)
        raw = tk.history(start=HISTORY_FROM, auto_adjust=False)
        if raw.empty:
            print(f"{t}: 시세 없음, 제외")
            continue
        splits = [ts.date() for ts in pd.DatetimeIndex(tk.splits.index)]
        if any(d >= date(2023, 6, 1) for d in splits):
            # yfinance 종가는 분할 보정값이라 '원본 체결가'로 넣으면 앱이 한 번 더 보정한다
            print(f"{t}: 기간 중 분할 있음, 제외 ({splits[-1]})")
            continue
        close = pd.Series(raw["Close"].to_numpy(), index=[ts.date() for ts in raw.index])
        out[t] = _features(close)
    return out


def simulate(data: dict[str, pd.DataFrame], rng: random.Random) -> tuple[list[Trade], list[Trade]]:
    """(체결, 사지 않은 신호). 사지 않은 신호는 상담 후 보류로 쓴다."""
    days = sorted({d for df in data.values() for d in df.index if d >= TRADE_FROM})
    open_pos: dict[str, Position] = {}
    trades: list[Trade] = []
    skipped: list[Trade] = []
    quarter_done: set[tuple[str, int, int]] = set()

    def buy(t: str, i: int, rule: str, row: pd.Series, qty_override: int | None = None) -> Trade:
        price = float(row["close"])
        qty = qty_override or max(1, int(BUDGET / price))
        tr = Trade(t, "buy", data[t].index[i], price, qty, rule)
        if rule == "A":
            tr.tags, tr.emotion, tr.conviction = ["momentum_chase", "theme"], "fomo", 4
            tr.target, tr.horizon = round(price * 1.3, 2), 20
            if rng.random() < 0.3:
                tr.stop = round(price * 0.9, 2)
        elif rule == "A2":
            tr.tags, tr.emotion, tr.conviction = ["averaging_down"], "revenge", 3
        elif rule == "B":
            tr.tags, tr.emotion, tr.conviction = ["dip_buy"], "calm", 3
            tr.stop, tr.target, tr.horizon = round(price * 0.85, 2), round(price * 1.3, 2), 60
        elif rule == "C":
            tr.tags, tr.emotion, tr.conviction = ["technical"], "confident", 4
            tr.stop, tr.target, tr.horizon = round(price * 0.92, 2), round(price * 1.2, 2), 40
        elif rule == "D":
            tr.tags = ["community_tip"] + (["theme"] if rng.random() < 0.5 else [])
            tr.emotion = "fomo" if rng.random() < 0.6 else "confident"
            tr.conviction = rng.choice([2, 3])
        elif rule == "E":
            tr.tags, tr.emotion, tr.conviction, tr.horizon = ["long_term"], "confident", 5, 365
        tr.text = rng.choice(TEXTS[rule])
        return tr

    def sell(t: str, i: int, pos: Position, reason: str) -> Trade:
        price = float(data[t]["close"].iloc[i])
        tr = Trade(t, "sell", data[t].index[i], price, pos.qty, reason)
        tr.tags, tr.emotion, tr.text = [reason], SELL_EMOTION[reason], SELL_TEXT[reason]
        return tr

    for d in days:
        for t, df in data.items():
            if d not in df.index:
                continue
            i = df.index.get_loc(d)
            row = df.iloc[i]
            if pd.isna(row["ma50"]) or pd.isna(row["rsi"]):
                continue
            price = float(row["close"])
            pos = open_pos.get(t)
            # ---- 청산 ----
            if pos is not None:
                ret = price / pos.entry - 1
                held = i - pos.entry_i
                reason = None
                if pos.rule == "A":
                    if ret <= -0.2 and not pos.averaged:
                        tr = buy(t, i, "A2", row, pos.qty)
                        trades.append(tr)
                        pos.entry = (pos.entry * pos.qty + price * tr.qty) / (pos.qty + tr.qty)
                        pos.qty += tr.qty
                        pos.averaged = True
                        continue
                    if pos.stop and price <= pos.stop:
                        reason = "stop_loss"
                    elif ret <= -0.12:
                        reason = "fear"
                    elif ret >= 0.25:
                        reason = "take_profit"
                    elif held >= 25:
                        reason = "boredom"
                elif pos.rule == "B":
                    if pos.stop and price <= pos.stop and pos.honors_stop:
                        reason = "stop_loss"
                    elif ret <= -0.25:
                        reason = "fear"
                    elif pos.target and price >= pos.target:
                        reason = "target_hit"
                    elif held >= pos.horizon:
                        reason = "need_cash"
                elif pos.rule == "C":
                    pos.below_ma50 = pos.below_ma50 + 1 if price < row["ma50"] else 0
                    if pos.stop and price <= pos.stop:
                        reason = "stop_loss"
                    elif pos.target and price >= pos.target:
                        reason = "target_hit"
                    elif pos.below_ma50 >= 5:
                        reason = "thesis_broken"
                    elif held >= pos.horizon:
                        reason = "need_cash"
                elif pos.rule == "D":
                    if ret <= -0.10:
                        reason = "fear"
                    elif ret >= 0.15:
                        reason = "take_profit"
                    elif held >= 30:
                        reason = "boredom"
                if reason:
                    trades.append(sell(t, i, pos, reason))
                    del open_pos[t]
                continue
            # ---- 진입 ----
            if t in LONG_TERM:
                key = (t, d.year, (d.month - 1) // 3)
                if key not in quarter_done:
                    quarter_done.add(key)
                    trades.append(buy(t, i, "E", row))
                continue
            rule = None
            if row["r5"] >= 0.15 and row["rsi"] >= 65:
                rule = "A"
            elif row["from_high"] <= -0.35 and row["r20"] <= -0.15:
                rule = "B"
            elif (
                price > row["ma50"]
                and i >= 10
                and (df["close"].iloc[i - 10 : i] <= df["ma50"].iloc[i - 10 : i]).all()
                and row["r20"] > 0
            ):
                rule = "C"
            elif t in MEME and rng.random() < 1 / 120:
                rule = "D"
            if rule is None:
                continue
            active = sum(1 for k in open_pos if k not in LONG_TERM)
            hesitated = d >= APP_FROM and rng.random() < 0.35
            if active >= MAX_OPEN or hesitated:
                if d >= APP_FROM:
                    skipped.append(buy(t, i, rule, row))
                continue
            tr = buy(t, i, rule, row)
            trades.append(tr)
            open_pos[t] = Position(
                rule, i, tr.price, tr.qty, tr.stop, tr.target, tr.horizon or 0,
                honors_stop=rng.random() >= 0.3,
            )  # fmt: skip
    return trades, skipped


def _parsed(trades: list[Trade]) -> list[ParsedFill]:
    seq: dict[tuple[str, date], int] = {}
    out = []
    for tr in trades:
        k = (tr.ticker, tr.day)
        seq[k] = seq.get(k, -1) + 1
        price = Decimal(f"{tr.price:.2f}")
        out.append(
            ParsedFill(
                ticker=tr.ticker,
                side=tr.side,
                trade_date=tr.day,
                seq=seq[k],
                price=price,
                quantity=Decimal(tr.qty),
                fee=Decimal(f"{float(price) * tr.qty * FEE_RATE:.2f}"),
                dedup_key=f"persona:{tr.ticker}:{tr.day}:{tr.side}:{seq[k]}",
            )
        )
    return out


def seed() -> None:
    db_name = make_url(settings.database_url).database or ""
    if "demo" not in db_name:
        sys.exit(f"데모 DB가 아닙니다 ({db_name}). DATABASE_URL을 ..._demo로 지정하세요.")
    rng = random.Random(SEED)
    data = _load()
    trades, skipped = simulate(data, rng)
    buys = [t for t in trades if t.side == "buy"]
    sells = len(trades) - len(buys)
    print(f"체결 {len(trades)}건 (매수 {len(buys)}, 매도 {sells}), 안 산 신호 {len(skipped)}건")

    with SessionLocal() as db:
        prices = PriceService(db)
        summary = import_fills(db, _parsed(trades))
        print(f"새 체결 {summary.new_fills}, 결정 {summary.new_decisions}")
        for w in recompute_tickers(db, summary.tickers, prices):
            print("경고:", w)
        db.flush()

        # 이유·감정·계획 붙이기 (같은 날 같은 종목·방향 체결은 결정 하나로 묶였다)
        by_key: dict[tuple[str, str, date], Trade] = {}
        for tr in trades:
            by_key.setdefault((tr.ticker, tr.side, tr.day), tr)
        now = datetime.now(UTC)
        consults: list[Decision] = []
        for d in db.scalars(select(Decision)).all():
            tr = by_key.get((d.ticker, d.side, d.decided_on))
            if tr is None:
                continue
            d.reason_tags, d.emotion, d.conviction = tr.tags, tr.emotion, tr.conviction
            d.reason_text, d.tagged_at = tr.text, now
            p = 0.7 if d.side == "buy" else 0.5
            if d.decided_on >= APP_FROM and tr.rule != "E" and rng.random() < p:
                d.source, d.user_action = "consult", "executed"
                d.target_price = Decimal(str(tr.target)) if tr.target else None
                d.stop_price = Decimal(str(tr.stop)) if tr.stop else None
                d.horizon_days = tr.horizon
                consults.append(d)
        db.flush()

        # 사지 않은 신호 → 상담 후 보류
        today = date.today()
        for tr in skipped:
            bars = prices.bars(tr.ticker, tr.day - timedelta(days=400), today)
            spy = prices.bars(BENCHMARK, tr.day - timedelta(days=400), today)["adj_close"]
            ctx = build_snapshot(bars["adj_close"], spy, tr.day) or {}
            d = Decision(
                ticker=tr.ticker, side="buy", source="consult", decided_on=tr.day,
                reason_tags=tr.tags, emotion=tr.emotion, conviction=tr.conviction,
                reason_text=tr.text, tagged_at=now, context=ctx, similar_ids=[],
                target_price=Decimal(str(tr.target)) if tr.target else None,
                stop_price=Decimal(str(tr.stop)) if tr.stop else None,
                horizon_days=tr.horizon, user_action="skipped",
            )  # fmt: skip
            db.add(d)
            consults.append(d)
        db.flush()
        for w in track_skipped(db, prices):
            print("경고:", w)

        # 판정: 각 상담 시점 이전에 끝난 매매만 보므로 순서와 무관하게 지금 돌려도 된다
        for d in sorted(consults, key=lambda d: (d.decided_on, d.id)):
            judge(db, d)
        # 판정을 거스른 결정 몇 건에는 메모
        for d in consults:
            if d.verdict == "against" and d.user_action == "executed" and rng.random() < 0.6:
                d.action_note = rng.choice(
                    ["이번엔 다를 것 같아서", "실적 발표 전이라 일단", "소액이라 괜찮다고 봄"]
                )
        record_run(db, RECOMPUTE, [])
        db.commit()
        n_exec = sum(1 for d in consults if d.user_action == "executed")
        print(f"상담 {len(consults)}건 판정 (실행 {n_exec}, 보류 {len(consults) - n_exec})")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "seed":
        sys.exit(__doc__)
    seed()
