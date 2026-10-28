"""결정 전후 가격 흐름을 작은 SVG로 그린다 (순수 함수, JS 없음).

숫자 표만으로는 '그때 상황'이 안 떠오른다. 결정일 앞뒤 종가 선 위에 체결(▲매수 ▼매도)과
상담 때 적은 목표가·손절가를 얹어, 계획과 실제가 얼마나 어긋났는지 한눈에 보게 한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

WIDTH, HEIGHT = 640, 180
PAD_L, PAD_R, PAD_T, PAD_B = 44, 8, 10, 20
# 목표가·손절가가 가격 범위에서 이 비율 넘게 벗어나면 그리지 않는다 (차트가 납작해진다)
LEVEL_SLACK = 0.25
# 국내 관례: 매수(상승 쪽) 빨강, 매도 파랑
COLORS = {
    "buy": "#d12f2f",
    "sell": "#2f6fd1",
    "target": "#0f8a4f",
    "stop": "#d12f2f",
    "avg": "#888",
}


@dataclass(frozen=True)
class Mark:
    day: date
    price: float
    kind: str  # buy | sell


@dataclass(frozen=True)
class Level:
    price: float
    label: str
    kind: str  # target | stop | avg


def _fmt(p: float) -> str:
    return f"{p:,.2f}" if p < 1000 else f"{p:,.0f}"


def render_chart(
    bars: Sequence[tuple[date, float]],
    marks: Sequence[Mark] = (),
    levels: Sequence[Level] = (),
    decided_on: date | None = None,
) -> str:
    """bars: (거래일, 종가) 날짜순. 2개 미만이면 빈 문자열."""
    if len(bars) < 2:
        return ""
    days = [d for d, _ in bars]
    closes = [p for _, p in bars]
    lo, hi = min(closes), max(closes)
    shown = [lv for lv in levels if lo * (1 - LEVEL_SLACK) <= lv.price <= hi * (1 + LEVEL_SLACK)]
    y_lo = min([lo, *(lv.price for lv in shown)])
    y_hi = max([hi, *(lv.price for lv in shown)])
    if y_hi == y_lo:
        y_hi = y_lo * 1.01 or 1.0
    inner_w = WIDTH - PAD_L - PAD_R
    inner_h = HEIGHT - PAD_T - PAD_B

    def x_of(i: int) -> float:
        return PAD_L + inner_w * i / (len(bars) - 1)

    def y_of(p: float) -> float:
        return PAD_T + inner_h * (1 - (p - y_lo) / (y_hi - y_lo))

    def x_day(d: date) -> float | None:
        """d 또는 그 뒤 첫 거래일의 x. 범위 밖이면 None."""
        if d < days[0] or d > days[-1]:
            return None
        i = next(i for i, day in enumerate(days) if day >= d)
        return x_of(i)

    out = [
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="{days[0]}부터 {days[-1]}까지 종가 흐름">'
    ]
    # 축 레이블: 가격 위·아래, 날짜 처음·끝
    right = 'class="axis" text-anchor="end"'
    out.append(f'<text x="{PAD_L - 4}" y="{PAD_T + 4}" {right}>{_fmt(y_hi)}</text>')
    out.append(f'<text x="{PAD_L - 4}" y="{HEIGHT - PAD_B}" {right}>{_fmt(y_lo)}</text>')
    out.append(f'<text x="{PAD_L}" y="{HEIGHT - 4}" class="axis">{days[0]}</text>')
    out.append(f'<text x="{WIDTH - PAD_R}" y="{HEIGHT - 4}" {right}>{days[-1]}</text>')
    # 목표가·손절가·평단가
    for lv in shown:
        y = y_of(lv.price)
        color = COLORS.get(lv.kind, "#888")
        out.append(
            f'<line x1="{PAD_L}" x2="{WIDTH - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-dasharray="4 3" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{WIDTH - PAD_R}" y="{y - 3:.1f}" class="level" fill="{color}" '
            f'text-anchor="end">{escape(lv.label)} {_fmt(lv.price)}</text>'
        )
    # 결정일 세로선
    if decided_on is not None and (x := x_day(decided_on)) is not None:
        out.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T}" y2="{HEIGHT - PAD_B}" '
            'stroke="#999" stroke-dasharray="2 3" stroke-width="1"/>'
        )
    # 종가 선
    points = " ".join(f"{x_of(i):.1f},{y_of(p):.1f}" for i, p in enumerate(closes))
    out.append(
        f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    )
    # 체결 표시
    for m in marks:
        if (x := x_day(m.day)) is None:
            continue
        y = y_of(m.price)
        color = COLORS.get(m.kind, "#888")
        if m.kind == "buy":
            shape = f"{x:.1f},{y - 6:.1f} {x - 5:.1f},{y + 4:.1f} {x + 5:.1f},{y + 4:.1f}"
        else:
            shape = f"{x:.1f},{y + 6:.1f} {x - 5:.1f},{y - 4:.1f} {x + 5:.1f},{y - 4:.1f}"
        title = f"<title>{m.day} {_fmt(m.price)}</title>"
        out.append(f'<polygon points="{shape}" fill="{color}">{title}</polygon>')
    out.append("</svg>")
    return "".join(out)
