from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str
    jwt_secret: str
    session_hours: int = 12
    cookie_secure: bool = False
    demo_password: str = ""
    environment: str = "development"


settings = Settings()
