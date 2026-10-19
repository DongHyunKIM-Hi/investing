from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ggeol:ggeol@localhost:5432/ggeol"
    app_password: str = "change-me"
    session_secret: str = "dev-secret-change-me"
    anthropic_api_key: str = ""
    consult_model: str = "claude-opus-5-5"
    consult_effort: str = "low"  # 대화형 상담은 짧은 응답 위주라 낮은 effort로 충분
    upload_dir: Path = ROOT_DIR / "data" / "uploads"


settings = Settings()
