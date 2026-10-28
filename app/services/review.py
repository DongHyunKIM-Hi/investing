"""주간 회고: 지난 며칠의 상담·청산·점검 알림을 한 장으로 (순수 함수).

매일 보는 화면이 '지금 뭘 할지'라면, 회고는 '지난주에 내가 뭘 했는지'다. 특히 판정을 거스른
결정(비추천인데 샀다, 추천인데 안 샀다)은 그 자리에서 되짚어야 다음 주에 같은 짓을 덜 한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.engine.case import Case
from app.services.engine.verdict import VERDICTS, Stats, stats
from app.services.scorecard import ACTIONS, Judged
from app.services.watch import Watch

DAYS = 7
TOP = 5
URGENT = 2  # watch.urgency 가 이 값 이하(high·good·mid)만 회고에 넣는다


@dataclass(frozen=True)
class Review:
    since: date  # 포함
    until: date  # 포함
    consults: list[Judged]  # 기간에 판정 받은 상담, 최근 순
    by_verdict: dict[str, int]  # VERDICTS 순, 0건 라벨 포함
    by_action: dict[str, int]  # ACTIONS 순
    overrides: list[Judged]  # 판정을 거스른 결정
    closed: list[Case]  # 기간에 청산된 매수, 수익률 나쁜 순
    closed_stats: Stats
    alerts: list[Watch]  # 지금 점검할 보유 종목
    untagged: int

    @property
    def empty(self) -> bool:
        return not (self.consults or self.closed or self.alerts)


def build_review(
    judged: Sequence[Judged],
    buys: Sequence[Case],
    watches: Sequence[Watch],
    untagged: int,
    today: date,
    days: int = DAYS,
) -> Review:
    since = today - timedelta(days=days - 1)
    consults = sorted(
        (j for j in judged if since <= j.decided_on <= today),
        key=lambda j: (j.decided_on, j.id),
        reverse=True,
    )
    closed = sorted(
        (c for c in buys if c.closed_on is not None and since <= c.closed_on <= today),
        key=lambda c: c.ret if c.ret is not None else 0.0,
    )
    return Review(
        since=since,
        until=today,
        consults=consults,
        by_verdict={k: sum(1 for j in consults if j.verdict == k) for k in VERDICTS},
        by_action={k: sum(1 for j in consults if j.action == k) for k in ACTIONS},
        overrides=[j for j in consults if j.followed is False],
        closed=closed,
        closed_stats=stats(closed),
        alerts=[w for w in watches if w.urgency <= URGENT],
        untagged=untagged,
    )


def _pct(v: float | None) -> str:
    return f"{v * 100:+.1f}%" if v is not None else "–"


def override_text(j: Judged) -> str:
    """'비추천인데 샀음' 같은 한 줄. 매도는 '매도 추천인데 안 팔았음'."""
    verdict = VERDICTS[j.verdict]
    if j.side == "buy":
        did = "샀음" if j.action == "executed" else "안 샀음"
        return f"{verdict}인데 {did}"
    did = "팔았음" if j.action == "executed" else "안 팔았음"
    return f"매도 {verdict}인데 {did}"


def review_lines(r: Review) -> list[str]:
    """CLI·cron 메일용 텍스트."""
    lines = [f"주간 회고 {r.since} ~ {r.until}"]
    if r.empty:
        lines.append("  이번 주는 상담도 청산도 없었어요.")
        return lines
    if r.consults:
        verdicts = " · ".join(f"{VERDICTS[k]} {n}" for k, n in r.by_verdict.items() if n)
        actions = " · ".join(f"{ACTIONS[k]} {n}" for k, n in r.by_action.items() if n)
        lines.append(f"상담 {len(r.consults)}건: {verdicts} / {actions}")
        for j in r.consults:
            side = "매수" if j.side == "buy" else "매도"
            prob = f"{j.win_prob * 100:.0f}%" if j.win_prob is not None else "–"
            verdict = f"{VERDICTS[j.verdict]}({prob})"
            lines.append(f"  {j.decided_on} {j.ticker} {side} {verdict} {ACTIONS[j.action]}")
    if r.overrides:
        lines.append(f"판정을 거스른 결정 {len(r.overrides)}건:")
        for j in r.overrides:
            lines.append(f"  {j.ticker} {j.decided_on}: {override_text(j)}")
    if r.closed:
        s = r.closed_stats
        typical = f"중앙값 {_pct(s.median_return)}, 평균 {_pct(s.avg_return)}"
        lines.append(f"청산 {s.n}건: 수익 {s.wins}건, {typical}")
        for c in r.closed[:TOP]:
            lines.append(f"  {c.ticker} {c.closed_on} {_pct(c.ret)}")
    if r.alerts:
        lines.append(f"점검할 보유 종목 {len(r.alerts)}개:")
        for w in r.alerts:
            lines.append(f"  {w.ticker} {_pct(w.position_return)}")
            for a in w.alerts:
                if a.level != "info":
                    lines.append(f"    [{a.level}] {a.text}")
    if r.untagged:
        lines.append(f"이유가 비어 있는 결정 {r.untagged}건 (태깅하면 판정 표본이 늘어요)")
    return lines
