import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Decision, Fill
from app.services.importer.csv_parser import ColumnMapping, parse_fills, read_csv
from app.services.importer.pipeline import import_fills
from app.services.importer.presets import guess_mapping
from app.services.market.prices import PriceService
from app.services.recompute import recompute_tickers
from app.taxonomy import EMOTIONS, reasons_for
from app.web import templates

router = APIRouter(prefix="/trades")
DB = Annotated[Session, Depends(get_db)]


def _csv_list(raw: str) -> list[str]:
    return [v.strip() for v in raw.split(",") if v.strip()]


def _upload_path(token: str):
    if not token.isalnum():  # 경로 조작 방지
        raise HTTPException(400, "잘못된 업로드 토큰")
    return settings.upload_dir / f"{token}.csv"


@router.get("", response_class=HTMLResponse)
def list_trades(request: Request, db: DB):
    decisions = db.scalars(
        select(Decision).order_by(Decision.decided_on.desc(), Decision.id.desc())
    ).all()
    untagged = db.scalar(
        select(func.count()).select_from(Decision).where(Decision.tagged_at.is_(None))
    )
    return templates.TemplateResponse(
        request, "trades/list.html", {"decisions": decisions, "untagged": untagged}
    )


@router.get("/import", response_class=HTMLResponse)
def import_form(request: Request):
    return templates.TemplateResponse(request, "trades/import.html", {"error": None})


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(request: Request, file: UploadFile):
    data = await file.read()
    try:
        df = read_csv(data)
    except Exception as e:
        return templates.TemplateResponse(
            request, "trades/import.html", {"error": f"CSV를 읽지 못했습니다: {e}"}
        )
    token = secrets.token_hex(16)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    _upload_path(token).write_bytes(data)
    columns = list(df.columns)
    return templates.TemplateResponse(
        request,
        "trades/mapping.html",
        {
            "token": token,
            "filename": file.filename,
            "columns": columns,
            "rows": df.head(5).to_dict("records"),
            "total": len(df),
            "mapping": guess_mapping(columns),
            "error": None,
        },
    )


@router.post("/import/commit", response_class=HTMLResponse)
def import_commit(
    request: Request,
    db: DB,
    token: Annotated[str, Form()],
    date_col: Annotated[str, Form()],
    ticker_col: Annotated[str, Form()],
    side_col: Annotated[str, Form()],
    price_col: Annotated[str, Form()],
    quantity_col: Annotated[str, Form()],
    buy_values: Annotated[str, Form()],
    sell_values: Annotated[str, Form()],
    date_format: Annotated[str, Form()] = "",
    fee_columns: Annotated[list[str] | None, Form()] = None,
):
    path = _upload_path(token)
    if not path.exists():
        raise HTTPException(404, "업로드 파일이 만료되었습니다. 다시 올려주세요.")
    mapping = ColumnMapping(
        date=date_col,
        date_format=date_format.strip() or None,
        ticker=ticker_col,
        side=side_col,
        price=price_col,
        quantity=quantity_col,
        buy_values=_csv_list(buy_values),
        sell_values=_csv_list(sell_values),
        fee_columns=fee_columns or [],
    )
    df = read_csv(path.read_bytes())
    try:
        parsed = parse_fills(df, mapping)
    except ValueError as e:
        return templates.TemplateResponse(
            request,
            "trades/mapping.html",
            {
                "token": token,
                "filename": path.name,
                "columns": list(df.columns),
                "rows": df.head(5).to_dict("records"),
                "total": len(df),
                "mapping": mapping,
                "error": str(e),
            },
        )
    summary = import_fills(db, parsed.fills)
    warnings = recompute_tickers(db, summary.tickers, PriceService(db))
    db.commit()
    path.unlink(missing_ok=True)
    return templates.TemplateResponse(
        request,
        "trades/result.html",
        {"summary": summary, "skipped": parsed.skipped, "warnings": warnings},
    )


@router.post("/recompute", response_class=HTMLResponse)
def recompute_all(request: Request, db: DB):
    tickers = db.scalars(select(Fill.ticker).distinct()).all()
    warnings = recompute_tickers(db, tickers, PriceService(db))
    db.commit()
    return templates.TemplateResponse(
        request, "trades/_recompute_done.html", {"count": len(tickers), "warnings": warnings}
    )


@router.get("/tagging", response_class=HTMLResponse)
def tagging(request: Request, db: DB):
    decisions = db.scalars(
        select(Decision)
        .where(Decision.tagged_at.is_(None))
        .order_by(Decision.decided_on.desc())
        .limit(30)
    ).all()
    return templates.TemplateResponse(request, "trades/tagging.html", {"decisions": decisions})


@router.post("/{decision_id}/tags", response_class=HTMLResponse)
def save_tags(
    request: Request,
    db: DB,
    decision_id: int,
    reasons: Annotated[list[str] | None, Form()] = None,
    emotion: Annotated[str, Form()] = "unknown",
    reason_text: Annotated[str, Form()] = "",
):
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(404)
    allowed = reasons_for(decision.side)
    decision.reason_tags = [r for r in (reasons or ["unknown"]) if r in allowed]
    decision.emotion = emotion if emotion in EMOTIONS else "unknown"
    decision.reason_text = reason_text.strip() or None
    decision.tagged_at = datetime.now(UTC)
    db.commit()
    return templates.TemplateResponse(request, "trades/_tagged.html", {"d": decision})
