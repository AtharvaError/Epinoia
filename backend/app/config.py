from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "change-me"
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str
    jwt_secret: str
    session_hours: int = 12
    cookie_secure: bool = False
    demo_password: str = ""
    environment: str = "development"
    anthropic_api_key: str = ""  # Empty runs the assistant in offline mode.
    telegram_bot_token: str = ""  # Empty disables the Telegram bot.
    telegram_as_email: str = ""  # The FestOS user whose permissions and festival the bot answers with.
    telegram_allowed_chat_ids: str = ""  # Comma-separated chat ids allowed to talk to the bot. Empty = anyone (logged).

    @model_validator(mode="after")
    def production_is_safe(self):
        """Refuse to start in production on settings that would quietly weaken it."""
        if self.environment == "production":
            if len(self.jwt_secret) < MIN_SECRET_LENGTH or PLACEHOLDER in self.jwt_secret:
                raise ValueError(f"JWT_SECRET must be at least {MIN_SECRET_LENGTH} random characters in production")
            if PLACEHOLDER in self.database_url:
                raise ValueError("DATABASE_URL still has the example password")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production, which also requires HTTPS")
        return self


settings = Settings()
