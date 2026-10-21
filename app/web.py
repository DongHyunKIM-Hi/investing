from fastapi.templating import Jinja2Templates
from jinja2 import Undefined

from app.config import ROOT_DIR
from app.taxonomy import BUY_REASONS, EMOTIONS, SELL_REASONS, reasons_for


def _missing(value: object) -> bool:
    # outcome JSON에 키가 없으면 템플릿에서는 Undefined로 들어온다
    return value is None or isinstance(value, Undefined)


def pct(value: float | None, digits: int = 1) -> str:
    return "–" if _missing(value) or value is None else f"{value * 100:+.{digits}f}%"


def tone(value: float | None) -> str:
    if _missing(value) or value is None:
        return "muted"
    return "up" if value > 0 else "down" if value < 0 else "muted"


templates = Jinja2Templates(directory=ROOT_DIR / "app" / "templates")
templates.env.filters["pct"] = pct
templates.env.filters["tone"] = tone
templates.env.globals.update(
    BUY_REASONS=BUY_REASONS, SELL_REASONS=SELL_REASONS, EMOTIONS=EMOTIONS, reasons_for=reasons_for
)
