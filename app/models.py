from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

Money = Numeric(18, 6)


class Decision(Base):
    """매매 결정 1건: 왜(이유·감정) + 계획 + 당시 상황(context) + 결과(outcome)."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    source: Mapped[str] = mapped_column(String(16))  # consult | csv | manual
    decided_on: Mapped[date] = mapped_column(Date, index=True)

    reason_text: Mapped[str | None] = mapped_column(Text)
    reason_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    emotion: Mapped[str | None] = mapped_column(String(16))
    conviction: Mapped[int | None]  # 1~5
    target_price: Mapped[Decimal | None] = mapped_column(Money)
    stop_price: Mapped[Decimal | None] = mapped_column(Money)
    horizon_days: Mapped[int | None]
    tagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # 결정 시점 시장 지표
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # 결과 지표

    # recommend | caution | against | insufficient
    verdict: Mapped[str | None] = mapped_column(String(16))
    win_prob: Mapped[float | None]
    similar_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    # 판정 당시 근거(표본 수·신뢰구간·패턴·경고). 데이터가 늘어도 그때 본 판정을 보존한다
    verdict_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    user_action: Mapped[str] = mapped_column(
        String(16), default="executed"
    )  # executed|skipped|pending

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    fills: Mapped[list["Fill"]] = relationship(back_populates="decision")
    messages: Mapped[list["ConsultMessage"]] = relationship(
        back_populates="decision", order_by="ConsultMessage.id", cascade="all, delete-orphan"
    )


class ConsultMessage(Base):
    """상담 대화 1턴. content는 Claude API 메시지 content 그대로(블록 목록) 저장해 재전송한다."""

    __tablename__ = "consult_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    decision: Mapped[Decision] = relationship(back_populates="messages")


class Fill(Base):
    """실제 체결 1건 (CSV 한 줄 또는 수동 입력)."""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(4))
    trade_date: Mapped[date] = mapped_column(Date)
    seq: Mapped[int] = mapped_column(default=0)  # 같은 날 체결 순서
    price: Mapped[Decimal] = mapped_column(Money)
    quantity: Mapped[Decimal] = mapped_column(Money)
    fee: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    decision: Mapped[Decision | None] = relationship(back_populates="fills")


class LotMatch(Base):
    """FIFO로 매칭된 매수 체결 ↔ 매도 체결 수량."""

    __tablename__ = "lot_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    buy_fill_id: Mapped[int] = mapped_column(ForeignKey("fills.id", ondelete="CASCADE"))
    sell_fill_id: Mapped[int] = mapped_column(ForeignKey("fills.id", ondelete="CASCADE"))
    quantity: Mapped[Decimal] = mapped_column(Money)


class PriceBar(Base):
    """일봉 캐시. close는 실제 체결가와 비교용, adj_close는 기간 수익률 계산용."""

    __tablename__ = "price_bars"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float]
    adj_close: Mapped[float]
    volume: Mapped[int] = mapped_column(BigInteger)


class PriceCoverage(Base):
    """티커별로 이미 받아 둔 시세 기간 (휴장일 때문에 bar 유무만으로는 판단 불가)."""

    __tablename__ = "price_coverage"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
