import hmac
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.web import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, password: Annotated[str, Form()]):
    if not hmac.compare_digest(password, settings.app_password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "비밀번호가 틀렸습니다"}, status_code=401
        )
    request.session["auth"] = True
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
