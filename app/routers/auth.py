import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.web import templates

router = APIRouter()


FAIL_DELAY = 1.5  # 틀릴 때마다 기다리게 해서 무차별 대입을 느리게 한다 (1인용이라 이 정도로 충분)


def _page(request: Request, error: str | None, status: int = 200):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "insecure": settings.insecure_defaults},
        status_code=status,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _page(request, None)


@router.post("/login")
def login(request: Request, password: Annotated[str, Form()]):
    if not hmac.compare_digest(password.encode(), settings.app_password.encode()):
        time.sleep(FAIL_DELAY)
        return _page(request, "비밀번호가 틀렸습니다", 401)
    request.session["auth"] = True
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
