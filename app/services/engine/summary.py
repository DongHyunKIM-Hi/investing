"""판정 화면 맨 위 한 줄 결론 (순수 함수).

숫자 카드와 패턴·경고 목록을 다 읽지 않아도 "그래서 뭐라는 건지"가 2~3문장으로 보이게 한다.
승률 → 가장 나쁜 패턴 → 가장 급한 경고 순으로 붙인다. LLM 없이 규칙으로 만든다.
"""

from collections.abc import Sequence

from app.services.engine.checks import Flag
from app.services.engine.verdict import MIN_SAMPLES, Pattern, Verdict

BAD_PATTERN = 0.4  # 이 승률 이하인 패턴은 '특히' 로 짚는다


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def _first(flags: Sequence[Flag], level: str) -> Flag | None:
    return next((f for f in flags if f.level == level), None)


def headline(v: Verdict, pats: Sequence[Pattern], flags: Sequence[Flag], side: str) -> str:
    r = v.raw
    sentences: list[str] = []
    verb = "샀을" if side == "buy" else "팔았을"

    if v.label == "insufficient":
        thing = "매수" if side == "buy" else "매도"
        sentences.append(
            f"비교할 만큼 비슷한 과거 {thing}가 {v.n}건뿐이에요 (최소 {MIN_SAMPLES}건). "
            "아직 내 전적으로 판정할 수 없으니, 이번 결정을 잘 기록해 두는 게 전부예요."
        )
    elif side == "buy":
        p = f"보정 승률 {v.win_prob * 100:.0f}%"
        med = f", 보통 {_pct(r.median_return)}로 끝났어요" if r.median_return is not None else ""
        if v.label == "recommend":
            sentences.append(f"비슷하게 {verb} 때 {r.n}건 중 {r.wins}건 수익({p}){med}.")
        elif v.label == "against":
            sentences.append(f"비슷하게 {verb} 때 {r.n}건 중 {r.wins}건만 수익({p}){med}.")
        else:
            tail = (
                f"고, 보통 {_pct(r.median_return)}로 끝났어요."
                if r.median_return is not None
                else "예요."
            )
            sentences.append(f"비슷하게 {verb} 때 {r.n}건 중 {r.wins}건 수익({p})으로 반반이{tail}")
    else:
        rose = r.n - r.wins
        post = -r.median_return if r.median_return is not None else None
        med = f" 판 뒤 20일 중앙값은 {_pct(post)}." if post is not None else ""
        if v.label == "against":
            sentences.append(f"비슷하게 {verb} 때 {r.n}건 중 {rose}건은 판 뒤 더 올랐어요.{med}")
        elif v.label == "recommend":
            sentences.append(
                f"비슷하게 {verb} 때 {r.n}건 중 {r.wins}건은 판 뒤 안 올랐어요 "
                f"(잘 판 비율 {v.win_prob * 100:.0f}%).{med}"
            )
        else:
            sentences.append(
                f"비슷하게 {verb} 때 {r.n}건 중 {rose}건은 판 뒤 더 올랐어요. 반반이에요.{med}"
            )

    worst = next(
        (
            p
            for p in pats
            if p.stats.n >= MIN_SAMPLES
            and p.stats.win_rate is not None
            and p.stats.win_rate <= BAD_PATTERN
        ),
        None,
    )
    if worst is not None and v.label != "recommend":
        sentences.append(f"특히 {worst.sentence}.")

    good = _first(flags, "good")
    high = _first(flags, "high")
    if good is not None:
        sentences.append(good.text)
    elif high is not None:
        sentences.append(("다만 " if v.label == "recommend" else "지금은 ") + high.text)
    return " ".join(sentences)
