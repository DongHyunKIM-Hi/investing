from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ggeol:ggeol@localhost:5432/ggeol"
    app_password: str = "change-me"
    session_secret: str = "dev-secret-change-me"
    anthropic_api_key: str = ""
    secure_cookies: bool = False  # HTTPS 뒤에 둘 때 True (세션 쿠키에 Secure 플래그)
    consult_model: str = "claude-opus-5-5"
    consult_effort: str = "low"  # 대화형 상담은 짧은 응답 위주라 낮은 effort로 충분
    upload_dir: Path = ROOT_DIR / "data" / "uploads"

    @property
    def insecure_defaults(self) -> list[str]:
        """바꾸지 않은 기본 비밀값. 로그인 화면과 시작 로그에 경고로 보여 준다."""
        names = []
        if self.app_password in ("", "change-me"):
            names.append("APP_PASSWORD")
        if self.session_secret in ("", "dev-secret-change-me"):
            names.append("SESSION_SECRET")
        return names


settings = Settings()
