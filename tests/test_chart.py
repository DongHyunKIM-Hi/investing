from datetime import date, timedelta

from app.services.chart import Level, Mark, render_chart

START = date(2026, 1, 5)


def bars(prices: list[float]) -> list[tuple[date, float]]:
    return [(START + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_needs_two_bars():
    assert render_chart([]) == ""
    assert render_chart(bars([10.0])) == ""


def test_polyline_marks_and_levels():
    svg = render_chart(
        bars([10, 11, 12, 11, 13]),
        marks=[
            Mark(START + timedelta(days=1), 11.0, "buy"),
            Mark(START + timedelta(days=4), 13.0, "sell"),
        ],
        levels=[
            Level(14.0, "목표", "target"),
            Level(9.5, "손절", "stop"),
            Level(50.0, "먼 목표", "target"),
        ],
        decided_on=START + timedelta(days=1),
    )
    assert svg.startswith('<svg class="chart"') and svg.endswith("</svg>")
    assert svg.count("<polygon") == 2
    assert "목표 14.00" in svg and "손절 9.50" in svg
    assert "먼 목표" not in svg  # 가격 범위에서 너무 멀면 생략
    assert svg.count("stroke-dasharray") == 3  # 목표·손절·결정일
    assert svg.count(",") >= 5  # polyline 점


def test_marks_outside_range_are_skipped_and_flat_series_ok():
    svg = render_chart(bars([10, 10, 10]), marks=[Mark(date(2025, 1, 1), 10.0, "buy")])
    assert "<polygon" not in svg
    assert "NaN" not in svg and "inf" not in svg


def test_labels_escape_markup():
    svg = render_chart(bars([1, 2]), levels=[Level(1.5, "a<b", "avg")])
    assert "a&lt;b" in svg
