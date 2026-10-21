"""매수·매도 전 상담 대화 (Claude API).

한 번의 사용자 발화 = API 호출 1번. Claude가 이유·계획을 충분히 들었다고 판단하면
record_decision 도구를 호출하고, 그 입력이 곧 구조화된 결정 기록이 된다.
대화 기록은 API content 블록 그대로 저장·재전송한다.
thinking 블록은 수정하면 안 되므로 기록은 append-only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, Field, ValidationError

from app.taxonomy import BUY_REASONS, EMOTIONS, SELL_REASONS

RECORD_TOOL_NAME = "record_decision"
MAX_TOKENS = 8000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

BuyReason = Literal[
    "earnings",
    "news",
    "momentum_chase",
    "dip_buy",
    "averaging_down",
    "community_tip",
    "theme",
    "technical",
    "long_term",
    "unknown",
]
SellReason = Literal[
    "target_hit",
    "stop_loss",
    "fear",
    "boredom",
    "need_cash",
    "thesis_broken",
    "take_profit",
    "unknown",
]
Emotion = Literal["calm", "confident", "fomo", "fear", "revenge", "unknown"]


class RecordInput(BaseModel):
    """record_decision 도구 입력. strict 스키마로 받지만 숫자 범위는 여기서 한 번 더 검증한다."""

    reason_tags: list[BuyReason] = Field(min_length=1)
    emotion: Emotion
    conviction: int = Field(ge=1, le=5)
    target_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    horizon_days: int | None = Field(default=None, gt=0)
    reason_summary: str


class SellRecordInput(BaseModel):
    """매도 상담의 record_decision 도구 입력."""

    reason_tags: list[SellReason] = Field(min_length=1)
    emotion: Emotion
    conviction: int = Field(ge=1, le=5)
    avg_cost: float | None = Field(default=None, gt=0)  # 보유 기록이 없을 때 사용자가 말한 평단가
    reason_summary: str


Record = RecordInput | SellRecordInput


def _nullable(t: str, description: str) -> dict[str, Any]:
    return {"type": [t, "null"], "description": description}


RECORD_TOOL: dict[str, Any] = {
    "name": RECORD_TOOL_NAME,
    "description": (
        "사용자의 매수 이유·감정·계획을 기록하고 상담을 마친다. "
        "이유와 계획(목표가·손절가·기간)을 물어봤고, "
        "사용자가 답했거나 정하지 않겠다고 했을 때 호출한다. "
        "호출하면 사용자의 과거 매매 전적과 비교한 판정 화면으로 넘어간다."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "reason_tags",
            "emotion",
            "conviction",
            "target_price",
            "stop_price",
            "horizon_days",
            "reason_summary",
        ],
        "properties": {
            "reason_tags": {
                "type": "array",
                "items": {"type": "string", "enum": list(BUY_REASONS)},
                "description": "매수 이유 분류. 해당하는 것을 모두. 사용자가 말한 내용 기준",
            },
            "emotion": {
                "type": "string",
                "enum": list(EMOTIONS),
                "description": "대화에서 드러난 지배적 감정",
            },
            "conviction": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5],
                "description": "확신도 1(낮음)~5(높음). 사용자가 말한 값, 없으면 대화로 추정",
            },
            "target_price": _nullable("number", "목표가(달러). 정하지 않았으면 null"),
            "stop_price": _nullable("number", "손절가(달러). 정하지 않았으면 null"),
            "horizon_days": _nullable("integer", "예상 보유 기간(일). 정하지 않았으면 null"),
            "reason_summary": {
                "type": "string",
                "description": "사용자가 말한 매수 이유를 1~2문장 한국어로 요약 (사용자 표현 유지)",
            },
        },
    },
}

RECORD_SELL_TOOL: dict[str, Any] = {
    "name": RECORD_TOOL_NAME,
    "description": (
        "사용자의 매도 이유·감정을 기록하고 상담을 마친다. "
        "왜 팔려는지 들었고, 살 때 계획이 있으면 그와 비교해 봤을 때 호출한다. "
        "호출하면 사용자의 과거 매도 전적(판 뒤 주가 흐름)과 비교한 판정 화면으로 넘어간다."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reason_tags", "emotion", "conviction", "avg_cost", "reason_summary"],
        "properties": {
            "reason_tags": {
                "type": "array",
                "items": {"type": "string", "enum": list(SELL_REASONS)},
                "description": "매도 이유 분류. 해당하는 것을 모두. 사용자가 말한 내용 기준",
            },
            "emotion": {
                "type": "string",
                "enum": list(EMOTIONS),
                "description": "대화에서 드러난 지배적 감정",
            },
            "conviction": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5],
                "description": "파는 결정에 대한 확신도 1(낮음)~5(높음)",
            },
            "avg_cost": _nullable(
                "number",
                "보유 기록이 없다고 안내된 경우에만: 사용자가 말한 평균 매수가(달러). 아니면 null",
            ),
            "reason_summary": {
                "type": "string",
                "description": "사용자가 말한 매도 이유를 1~2문장 한국어로 요약 (사용자 표현 유지)",
            },
        },
    },
}

_EMOTION_LINES = "\n".join(f"- {k}: {v}" for k, v in EMOTIONS.items())


def _prompt(file: str, reasons: dict[str, str]) -> str:
    # 프롬프트 문구는 코드와 분리해 두고 다듬는다
    lines = "\n".join(f"- {k}: {v}" for k, v in reasons.items())
    return (
        (Path(__file__).parent / "prompts" / file)
        .read_text(encoding="utf-8")
        .format(tool=RECORD_TOOL_NAME, reasons=lines, emotions=_EMOTION_LINES)
    )


SYSTEM_PROMPT = _prompt("consult_system.md", BUY_REASONS)
SELL_SYSTEM_PROMPT = _prompt("consult_sell_system.md", SELL_REASONS)
PROMPTS = {"buy": SYSTEM_PROMPT, "sell": SELL_SYSTEM_PROMPT}
TOOLS = {"buy": RECORD_TOOL, "sell": RECORD_SELL_TOOL}


def _pct(v: float | None) -> str:
    return "알 수 없음" if v is None else f"{v * 100:+.1f}%"


def _money(v: float | None) -> str:
    return "없음" if v is None else f"${v:g}"


def position_text(ctx: Mapping[str, Any]) -> list[str]:
    """매도 상담: 지금 보유 정보와 살 때 남긴 계획."""
    if ctx.get("avg_cost") is None:
        return ["보유 정보: 기록 없음 (CSV에 없는 매수분). 평균 매수가를 물어보세요."]
    lines = [
        f"보유 정보: 평단 ${ctx['avg_cost']:.2f}, 평가 손익 {_pct(ctx.get('position_return'))}"
        + (
            f", {ctx['position_qty']:g}주, 첫 매수 {ctx['opened_on']} ({ctx['held_days']}일 보유)"
            if ctx.get("opened_on")
            else " (사용자가 알려 준 평단가)"
        )
    ]
    if plan := ctx.get("buy_plan"):
        reasons = (
            ", ".join(str(BUY_REASONS.get(t, t)) for t in plan.get("reason_tags", []))
            or "기록 없음"
        )
        lines.append(
            f"살 때 계획({plan.get('decided_on')} 상담): 이유 {reasons}, "
            f"목표가 {_money(plan.get('target_price'))}, 손절가 {_money(plan.get('stop_price'))}, "
            f"보유 기간 {str(plan['horizon_days']) + '일' if plan.get('horizon_days') else '없음'}"
        )
        if plan.get("reason_text"):
            lines.append(f'살 때 말한 이유: "{plan["reason_text"]}"')
    else:
        lines.append("살 때 계획: 상담 기록 없음")
    return lines


def situation_text(ticker: str, ctx: Mapping[str, Any], side: str = "buy") -> str:
    """상담 시스템 프롬프트에 붙일 '지금 이 종목 상황'."""
    price = ctx.get("last_close")
    lines = [
        f"상담 종목: {ticker}",
        f"기준일: {ctx.get('as_of', '알 수 없음')} 종가 기준 (오늘 장중 가격은 모름)",
        f"현재가(직전 종가): {'알 수 없음' if price is None else f'${price:.2f}'}",
        f"직전 5일 수익률: {_pct(ctx.get('ret_5d'))}",
        f"직전 20일 수익률: {_pct(ctx.get('ret_20d'))}",
        f"52주 고점 대비: {_pct(ctx.get('from_52w_high'))}",
        f"50일선 대비: {_pct(ctx.get('vs_ma50'))}, 200일선 대비: {_pct(ctx.get('vs_ma200'))}",
        f"RSI(14): {ctx.get('rsi14', '알 수 없음')}",
        f"시장(SPY) 20일 수익률: {_pct(ctx.get('spy_ret_20d'))}",
    ]
    if ctx.get("next_earnings"):
        lines.append(f"다음 실적발표 예정일: {ctx['next_earnings']}")
    if side == "sell":
        lines += position_text(ctx)
    return "\n".join(lines)


@dataclass(frozen=True)
class Turn:
    """Claude 응답 1턴을 해석한 결과."""

    content: list[dict[str, Any]]  # 저장·재전송용 원본 블록
    text: str  # 화면에 보여줄 텍스트
    record: Record | None = None
    tool_use_id: str | None = None
    error: str | None = None  # 도구 입력 검증 실패 등


def to_turn(content: list[dict[str, Any]], side: str = "buy") -> Turn:
    text = "\n\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
    tool = next(
        (b for b in content if b.get("type") == "tool_use" and b.get("name") == RECORD_TOOL_NAME),
        None,
    )
    if tool is None:
        return Turn(content, text)
    try:
        model = RecordInput if side == "buy" else SellRecordInput
        record = model.model_validate(tool.get("input") or {})
    except ValidationError as e:
        return Turn(content, text, tool_use_id=tool["id"], error=str(e))
    return Turn(content, text, record=record, tool_use_id=tool["id"])


def tool_result(tool_use_id: str, message: str, is_error: bool = False) -> list[dict[str, Any]]:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": message}
    return [{**block, "is_error": is_error}]


class ConsultError(Exception):
    pass


class ConsultLLM(Protocol):
    def reply(
        self, situation: str, messages: Sequence[Mapping[str, Any]], side: str = "buy"
    ) -> Turn: ...


class ClaudeConsultLLM:
    def __init__(self, api_key: str, model: str, effort: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.effort = effort

    def reply(
        self, situation: str, messages: Sequence[Mapping[str, Any]], side: str = "buy"
    ) -> Turn:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                # 고정 지침은 캐시, 종목 상황은 상담마다 다르므로 뒤에 둔다
                system=[
                    {
                        "type": "text",
                        "text": PROMPTS[side],
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": situation},
                ],
                tools=[TOOLS[side]],  # type: ignore[list-item]
                messages=list(messages),  # type: ignore[arg-type]
                output_config={"effort": self.effort},  # type: ignore[typeddict-item]
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise ConsultError("ANTHROPIC_API_KEY가 올바르지 않습니다.") from e
        except anthropic.RateLimitError as e:
            raise ConsultError("요청이 많아 잠시 막혔어요. 조금 뒤 다시 보내 주세요.") from e
        except anthropic.APIStatusError as e:
            raise ConsultError(f"Claude API 오류 ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ConsultError("Claude API에 연결하지 못했습니다. 네트워크를 확인하세요.") from e

        if response.stop_reason == "refusal":
            raise ConsultError(
                "이번 메시지에는 응답을 받지 못했어요. 표현을 바꾸거나 폼으로 입력해 주세요."
            )
        return to_turn([block.to_dict() for block in response.content], side)


def get_llm(api_key: str, model: str, effort: str) -> ConsultLLM | None:
    return ClaudeConsultLLM(api_key, model, effort) if api_key else None
