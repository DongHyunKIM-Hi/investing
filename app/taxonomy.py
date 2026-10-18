"""매매 결정을 분류하는 고정 어휘. 유사 전적 매칭의 기준이 되므로 코드(key)는 바꾸지 않는다."""

BUY_REASONS: dict[str, str] = {
    "earnings": "실적·펀더멘털",
    "news": "뉴스·이슈",
    "momentum_chase": "급등 추격",
    "dip_buy": "저가 매수(많이 빠져서)",
    "averaging_down": "물타기",
    "community_tip": "지인·커뮤니티·유튜브 추천",
    "theme": "테마·유행",
    "technical": "차트·기술적 신호",
    "long_term": "장기 보유 확신",
    "unknown": "기억 안 남",
}

SELL_REASONS: dict[str, str] = {
    "target_hit": "목표가 도달",
    "stop_loss": "손절 기준 도달",
    "fear": "떨어질까 불안해서",
    "boredom": "안 움직여서 지루해서",
    "need_cash": "다른 종목·현금 필요",
    "thesis_broken": "산 이유가 사라짐",
    "take_profit": "적당히 올라서 익절",
    "unknown": "기억 안 남",
}

EMOTIONS: dict[str, str] = {
    "calm": "차분함",
    "confident": "확신",
    "fomo": "놓칠까 조급함(FOMO)",
    "fear": "불안·공포",
    "revenge": "손실 만회 심리",
    "unknown": "기억 안 남",
}


def reasons_for(side: str) -> dict[str, str]:
    return BUY_REASONS if side == "buy" else SELL_REASONS
