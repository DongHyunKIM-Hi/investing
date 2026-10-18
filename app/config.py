from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ggeol:ggeol@localhost:5432/ggeol"
    app_password: str = "change-me"
    session_secret: str = "dev-secret-change-me"
    anthropic_api_key: str = ""
    upload_dir: Path = ROOT_DIR / "data" / "uploads"


settings = Settings()
