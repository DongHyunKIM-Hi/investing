from app.services.engine.checks import Flag
from app.services.engine.summary import headline
from app.services.engine.verdict import Pattern, Stats, Verdict


def verdict(label: str, n: int, wins: int, median: float | None, p: float = 0.5) -> Verdict:
    raw = Stats(
        n=n, wins=wins, win_rate=wins / n if n else None, avg_return=median, median_return=median
    )
    return Verdict(label, n, float(n), p, p - 0.1, p + 0.1, raw, median, None, None, None)


def pattern(label: str, n: int, wins: int, side: str = "buy") -> Pattern:
    return Pattern("tag", "x", label, Stats(n, wins, wins / n, -0.1, -0.1), side)


def test_insufficient_mentions_sample_size_and_minimum():
    text = headline(verdict("insufficient", 1, 1, 0.2), [], [], "buy")
    assert "1건뿐" in text and "최소 3건" in text


def test_against_adds_worst_pattern_and_high_flag():
    pats = [pattern("급등 추격", 5, 1), pattern("테마·유행", 4, 3)]
    flags = [Flag("mid", "RSI 75"), Flag("high", "손절가 없이 사려고 해요.")]
    text = headline(verdict("against", 7, 2, -0.08, 0.32), pats, flags, "buy")
    assert text.startswith(
        "비슷하게 샀을 때 7건 중 2건만 수익(보정 승률 32%), 보통 -8.0%로 끝났어요."
    )
    assert "특히 '급등 추격' 이유로 산 매매 5건 중 1건 수익" in text
    assert "테마" not in text  # 승률 75%는 나쁜 패턴이 아니다
    assert text.endswith("지금은 손절가 없이 사려고 해요.")


def test_recommend_skips_bad_pattern_but_keeps_high_flag_as_caveat():
    text = headline(
        verdict("recommend", 6, 5, 0.12, 0.7),
        [pattern("물타기", 3, 1)],
        [Flag("high", "급등")],
        "buy",
    )
    assert "6건 중 5건 수익" in text and "특히" not in text and "다만 급등" in text


def test_small_pattern_is_ignored():
    text = headline(verdict("caution", 4, 2, 0.0, 0.5), [pattern("물타기", 2, 0)], [], "buy")
    assert "반반" in text and "특히" not in text


def test_sell_against_shows_rebounds_and_good_flag_wins_over_high():
    flags = [Flag("high", "급락 직후예요."), Flag("good", "살 때 정한 손절가 아래예요.")]
    text = headline(verdict("against", 5, 1, -0.09, 0.3), [], flags, "sell")
    assert text.startswith(
        "비슷하게 팔았을 때 5건 중 4건은 판 뒤 더 올랐어요. 판 뒤 20일 중앙값은 +9.0%."
    )
    assert text.endswith("살 때 정한 손절가 아래예요.") and "급락" not in text


def test_sell_recommend():
    text = headline(verdict("recommend", 4, 4, 0.06, 0.75), [], [], "sell")
    assert "4건 중 4건은 판 뒤 안 올랐어요 (잘 판 비율 75%)" in text and "-6.0%" in text
