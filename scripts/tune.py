"""유사도 가중치 후보를 백테스트로 비교한다 (알려진 한계: 가중치는 직관값).

각 매매 시점에 그 이전에 청산된 매매만 보고 판정했을 때, 가중치 설정별로
- 판정이 나온 비율(coverage), 보정 승률의 Brier 점수(낮을수록 좋음, 0.25 = 동전 던지기),
- 추천 승률 - 비추천 승률(spread, 높을수록 판정이 갈라짐)
을 표로 보여 준다. 데이터가 적으면 차이가 우연일 수 있으니 표본 수를 같이 본다.

  uv run python scripts/tune.py           # 매수, 가중치 후보
  uv run python scripts/tune.py sell      # 매도
  uv run python scripts/tune.py buy grid  # 후보 대신 0.05 단위 격자 탐색 (느림)
  uv run python scripts/tune.py buy rules # 판정 규칙(임계값·K·사전분포·중앙값 규칙) 변형 비교

고르면 similarity.py의 WEIGHTS·TOP_K·MIN_SIMILARITY, verdict.py의 RECOMMEND_P·AGAINST_P·
AGAINST_MEDIAN·PRIOR를 손으로 바꾼다 (자동 반영하지 않음).
"""

import itertools
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import Decision  # noqa: E402
from app.services.engine import verdict as V  # noqa: E402
from app.services.engine.backtest import backtest, score, summarize  # noqa: E402
from app.services.engine.case import case_from_decision  # noqa: E402
from app.services.engine.similarity import MIN_SIMILARITY, TOP_K, WEIGHTS, find_similar  # noqa: E402
from app.services.engine.verdict import decide, median  # noqa: E402

KEYS = ["tags", "context", "emotion", "plan", "ticker"]
CANDIDATES: dict[str, dict[str, float]] = {
    "현재": dict(WEIGHTS),
    "이유 중심": {"tags": 0.55, "context": 0.25, "emotion": 0.10, "plan": 0.05, "ticker": 0.05},
    "상황 중심": {"tags": 0.25, "context": 0.50, "emotion": 0.10, "plan": 0.05, "ticker": 0.10},
    "종목 무시": {"tags": 0.45, "context": 0.40, "emotion": 0.10, "plan": 0.05, "ticker": 0.00},
    "감정 강조": {"tags": 0.35, "context": 0.30, "emotion": 0.25, "plan": 0.05, "ticker": 0.05},
    "종목 강조": {"tags": 0.30, "context": 0.30, "emotion": 0.10, "plan": 0.05, "ticker": 0.25},
}


def grid() -> dict[str, dict[str, float]]:
    out = {}
    steps = [x / 20 for x in range(0, 21)]
    for tags, context, emotion, ticker in itertools.product(steps, steps, steps, steps):
        plan = round(1 - tags - context - emotion - ticker, 2)
        if plan < 0 or plan > 0.2 or tags + context < 0.5:
            continue
        w = {"tags": tags, "context": context, "emotion": emotion, "plan": plan, "ticker": ticker}
        out["/".join(f"{w[k]:.2f}" for k in KEYS)] = w
    return out


# 판정 규칙 변형: (이름, find_similar 인자, verdict 상수 덮어쓰기)
RULES: list[tuple[str, dict, dict]] = [
    ("현재", {}, {}),
    ("중앙값 규칙 없음", {}, {"AGAINST_MEDIAN": -9.0}),
    ("0.55/0.45", {}, {"RECOMMEND_P": 0.55, "AGAINST_P": 0.45}),
    ("0.65/0.35", {}, {"RECOMMEND_P": 0.65, "AGAINST_P": 0.35}),
    ("사전분포 1", {}, {"PRIOR": 1.0}),
    ("사전분포 4", {}, {"PRIOR": 4.0}),
    ("K=5", {"k": 5}, {}),
    ("K=20", {"k": 20}, {}),
    ("유사도 0.25↑", {"min_similarity": 0.25}, {}),
    ("유사도 0.5↑", {"min_similarity": 0.5}, {}),
]


def rules(history: list, side: str) -> None:
    """같은 가중치로 판정 규칙만 바꿔 보며 라벨별 실제 성적을 비교한다."""
    resolved = [c for c in history if c.win is not None]
    defaults = {k: getattr(V, k) for k in ("RECOMMEND_P", "AGAINST_P", "AGAINST_MEDIAN", "PRIOR")}
    print(f"{'변형':<12} {'판정':>7} {'Brier':>6} {'spread':>7}  라벨: n/승률/중앙값")
    for name, sim_kw, consts in RULES:
        for k, v in {**defaults, **consts}.items():
            setattr(V, k, v)
        kw = {"k": TOP_K, "min_similarity": MIN_SIMILARITY, **sim_kw}
        rows = [(c, decide(find_similar(c, history, **kw))) for c in resolved]
        judged = [(c, v) for c, v in rows if v.label != "insufficient"]
        brier = (
            sum((v.win_prob - (1.0 if c.win else 0.0)) ** 2 for c, v in judged) / len(judged)
            if judged
            else None
        )
        cells, rate = [], {}
        for label in ("recommend", "caution", "against", "insufficient"):
            cs = [c for c, v in rows if v.label == label]
            wr = sum(1 for c in cs if c.win) / len(cs) if cs else None
            rate[label] = wr
            md = median(c.ret for c in cs)
            wr_s = f"{wr * 100:3.0f}%" if wr is not None else "  –"
            md_s = f"{md * 100:+5.1f}" if md is not None else "    –"
            cells.append(f"{label[:3]} {len(cs):>3}/{wr_s}/{md_s}")
        rec, agn = rate["recommend"], rate["against"]
        spread = f"{(rec - agn) * 100:+4.0f}%" if rec is not None and agn is not None else "    –"
        brier_s = f"{brier:.3f}" if brier is not None else "    –"
        head = f"{name:<12} {len(judged):>3}/{len(resolved):<3} {brier_s:>6} {spread:>7}"
        print(f"{head}  {'  '.join(cells)}")
    for k, v in defaults.items():
        setattr(V, k, v)


def _cell(s) -> str:
    return f"{s.n:>2}/{s.win_rate * 100:3.0f}%" if s.win_rate is not None else f"{s.n:>2}/  –"


def main(side: str, mode: str) -> None:
    with SessionLocal() as db:
        decisions = db.scalars(select(Decision).where(Decision.side == side)).all()
    history = [case_from_decision(d) for d in decisions]
    resolved = [c for c in history if c.win is not None]
    tagged = sum(1 for c in resolved if c.tags)
    print(f"{side}: 결정 {len(history)}건, 승패 확정 {len(resolved)}건 (이유 태깅 {tagged}건)")
    if len(resolved) < 10:
        print("표본이 10건 미만이라 비교가 의미 없어요. 더 쌓인 뒤 다시 돌리세요.")
    if mode == "rules":
        rules(history, side)
        return
    candidates = grid() if mode == "grid" else CANDIDATES
    rows = []
    for name, w in candidates.items():
        bt = backtest(history, w)
        rows.append((name, w, score(bt), summarize(bt)))
    rows.sort(key=lambda r: (r[2].brier if r[2].brier is not None else 9, -r[2].coverage))
    keys = "/".join(KEYS)
    head = f"{'설정':<10} {'가중치 ' + keys:<34} {'판정':>6} {'Brier':>6} {'spread':>7}"
    print(head + "  추천 n/승률  비추천 n/승률")
    for name, w, sc, by in rows[:25]:
        ws = "/".join(f"{w[k]:.2f}" for k in KEYS)
        brier = f"{sc.brier:.3f}" if sc.brier is not None else "–"
        spread = f"{sc.spread * 100:+.0f}%" if sc.spread is not None else "–"
        judged = f"{sc.judged}/{sc.total}"
        rec, agn = _cell(by["recommend"]), _cell(by["against"])
        print(f"{name:<10} {ws:<34} {judged:>6} {brier:>6} {spread:>7}  {rec:<12} {agn}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else "buy", args[1] if len(args) > 1 else "candidates")
