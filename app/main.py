import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import ROOT_DIR, settings
from app.routers import auth, trades

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="껄무새 탈출")
PUBLIC_PREFIXES = ("/login", "/static")


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


@app.get("/")
def index():
    return RedirectResponse("/trades", status_code=303)
