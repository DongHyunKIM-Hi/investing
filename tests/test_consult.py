from datetime import date

from app.services.engine.checks import Plan, check_plan
from app.services.llm.consult import (
    RECORD_TOOL,
    SYSTEM_PROMPT,
    RecordInput,
    situation_text,
    to_turn,
    tool_result,
)
from app.services.llm.keywords import suggest_tags
from app.taxonomy import BUY_REASONS

TODAY = date(2026, 10, 19)
CTX = {"last_close": 30.0, "ret_5d": 0.22, "ret_20d": 0.5, "rsi14": 78, "as_of": "2026-10-16"}


def plan(**kw) -> Plan:
    base = dict(
        tags=frozenset({"theme"}),
        emotion=None,
        conviction=3,
        target_price=40.0,
        stop_price=27.0,
        horizon_days=30,
    )
    return Plan(**{**base, **kw})


def texts(flags) -> str:
    return " | ".join(f.text for f in flags)


def test_no_stop_is_high_flag_first():
    flags = check_plan(plan(stop_price=None), {"last_close": 30.0}, TODAY)
    assert flags[0].level == "high" and "손절가 없이" in flags[0].text


def test_chase_warning_unless_admitted():
    assert "추격 매수" in texts(check_plan(plan(), CTX, TODAY))
    admitted = check_plan(plan(tags=frozenset({"momentum_chase"})), CTX, TODAY)
    assert "추격 매수" not in texts(admitted)
    assert "과열" in texts(admitted)


def test_price_sanity_and_reward_risk():
    assert "현재가(30.00) 이상" in texts(check_plan(plan(stop_price=31.0), CTX, TODAY))
    assert "손익비" in texts(check_plan(plan(target_price=32.0, stop_price=27.0), CTX, TODAY))
    assert "손익비" not in texts(check_plan(plan(target_price=40.0, stop_price=27.0), CTX, TODAY))


def test_emotion_and_earnings_flags():
    ctx = {**CTX, "next_earnings": "2026-11-05"}
    flags = texts(check_plan(plan(emotion="fomo"), ctx, TODAY))  # 보유 30일 안에 실적
    assert "조급한" in flags and "실적발표가 17일" in flags
    assert "실적" not in texts(check_plan(plan(horizon_days=7), ctx, TODAY))


def test_keyword_suggestion():
    text = "양자컴퓨터 뉴스 보고 며칠째 급등하길래 지금 아니면 놓칠 것 같아서"
    tags, emotion = suggest_tags(text)
    assert {"theme", "news", "momentum_chase"} <= set(tags)
    assert emotion == "fomo"
    assert suggest_tags("") == ([], None)
    assert "dip_buy" in suggest_tags("고점 대비 반토막 나서 싸 보여서")[0]


def test_to_turn_text_only():
    turn = to_turn([{"type": "thinking", "thinking": ""}, {"type": "text", "text": "왜 사세요?"}])
    assert turn.text == "왜 사세요?" and turn.record is None and turn.tool_use_id is None


def test_to_turn_record():
    content = [
        {"type": "text", "text": "기록할게요."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "record_decision",
            "input": {
                "reason_tags": ["theme", "momentum_chase"],
                "emotion": "fomo",
                "conviction": 4,
                "target_price": 45,
                "stop_price": None,
                "horizon_days": 30,
                "reason_summary": "양자 테마 급등",
            },
        },
    ]
    turn = to_turn(content)
    assert isinstance(turn.record, RecordInput) and turn.tool_use_id == "toolu_1"
    assert turn.record.stop_price is None and turn.record.conviction == 4


def test_to_turn_invalid_record_reports_error():
    bad = {"type": "tool_use", "id": "t", "name": "record_decision", "input": {"conviction": 9}}
    turn = to_turn([bad])
    assert turn.record is None and turn.error and turn.tool_use_id == "t"
    assert tool_result("t", turn.error, True)[0]["is_error"] is True


def test_prompt_and_tool_use_taxonomy_keys():
    schema = RECORD_TOOL["input_schema"]["properties"]
    assert schema["reason_tags"]["items"]["enum"] == list(BUY_REASONS)
    assert "momentum_chase: 급등 추격" in SYSTEM_PROMPT and "{" not in SYSTEM_PROMPT
    text = situation_text("IONQ", CTX)
    assert "$30.00" in text and "+22.0%" in text
