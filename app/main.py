import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import ROOT_DIR, settings
from app.db import SessionLocal
from app.routers import auth, consult, positions, report, trades
from app.web import templates

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
if settings.insecure_defaults:
    log.warning(
        "기본 비밀값을 쓰고 있습니다. .env에서 바꾸세요: %s", ", ".join(settings.insecure_defaults)
    )

app = FastAPI(title="껄무새 탈출")
PUBLIC_PREFIXES = ("/login", "/static", "/health")


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not request.url.path.startswith(PUBLIC_PREFIXES) and not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# add_middleware는 나중에 등록한 것이 바깥에서 실행된다: 세션이 로그인 검사보다 먼저 풀려야 함
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, https_only=False)
app.mount("/static", StaticFiles(directory=ROOT_DIR / "app" / "static"), name="static")
app.include_router(auth.router)
app.include_router(trades.router)
app.include_router(consult.router)
app.include_router(positions.router)
app.include_router(report.router)


@app.get("/")
def index():
    return RedirectResponse("/consult", status_code=303)


@app.get("/health")
def health():
    """컨테이너 헬스체크용. DB 연결까지 확인한다."""
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
    except Exception as e:  # DB가 죽었을 때 503
        log.exception("health check failed")
        return JSONResponse({"ok": False, "db": str(e)}, status_code=503)
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/health"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    message = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else None
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status": exc.status_code, "message": message},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("unhandled error: %s %s", request.method, request.url.path)
    return templates.TemplateResponse(
        request, "error.html", {"status": 500, "message": str(exc)}, status_code=500
    )
