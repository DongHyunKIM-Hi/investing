"""API 키가 없을 때 쓰는 규칙 기반 태그 추천. 자유 서술에서 키워드로 이유·감정을 고른다."""

import re

REASON_KEYWORDS: dict[str, tuple[str, ...]] = {
    "earnings": ("실적", "매출", "흑자", "가이던스", "펀더멘털", "재무", "eps"),
    "news": ("뉴스", "발표", "기사", "공시", "계약", "수주", "인수"),
    "momentum_chase": ("급등", "많이 올", "계속 올", "치솟", "상한", "떡상", "달리", "신고가"),
    "dip_buy": (
        "떨어",
        "빠졌",
        "빠져",
        "폭락",
        "저점",
        "싸졌",
        "싸 보",
        "싸다",
        "싸서",
        "저렴",
        "반토막",
        "눌림",
    ),
    "averaging_down": ("물타", "평단", "단가 낮"),
    "community_tip": (
        "유튜브",
        "커뮤니티",
        "친구",
        "지인",
        "추천",
        "리딩",
        "카톡",
        "레딧",
        "종토방",
    ),
    "theme": ("테마", "양자", "ai", "인공지능", "유행", "섹터", "로봇", "우주"),
    "technical": ("차트", "골든크로스", "이평", "지지", "돌파", "rsi", "볼린저", "거래량"),
    "long_term": ("장기", "10년", "5년", "모아", "적립", "노후"),
}

SELL_REASON_KEYWORDS: dict[str, tuple[str, ...]] = {
    "target_hit": ("목표가", "목표 도달", "목표에", "목표했던"),
    "stop_loss": ("손절", "손절가", "스탑"),
    "fear": ("불안", "무섭", "걱정", "겁", "더 떨어질", "폭락할", "하락장"),
    "boredom": ("지루", "안 움직", "횡보", "답답", "제자리", "물려"),
    "need_cash": ("현금", "돈이 필요", "다른 종목", "갈아타", "옮기", "생활비"),
    "thesis_broken": (
        "이유가 사라",
        "산 이유",
        "전망이 바뀌",
        "악재",
        "실적 부진",
        "가이던스 하향",
    ),
    "take_profit": ("익절", "수익 실현", "적당히", "벌었으니", "챙기"),
}

EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fomo": ("놓칠", "늦기 전", "지금 아니면", "나만", "조급", "fomo", "포모"),
    "revenge": ("만회", "복구", "본전", "손실 메", "잃은"),
    "fear": ("불안", "무섭", "걱정", "겁"),
    "confident": ("확신", "무조건", "틀림없", "자신"),
    "calm": ("차분", "계획대로", "분할로", "천천히"),
}


def _hits(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def suggest_tags(text: str, side: str = "buy") -> tuple[list[str], str | None]:
    """(이유 태그 목록, 감정). 못 찾으면 ([], None)."""
    t = re.sub(r"\s+", " ", text.lower())
    table = REASON_KEYWORDS if side == "buy" else SELL_REASON_KEYWORDS
    reasons = [key for key, words in table.items() if _hits(t, words)]
    emotion = next((key for key, words in EMOTION_KEYWORDS.items() if _hits(t, words)), None)
    return reasons, emotion
