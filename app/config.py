"""Configuracion central, leida de variables de entorno o del archivo .env."""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # Google
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    # calendar.app.created = solo los calendarios que crea el agente.
    # Si diera problemas, el ancho es .../auth/calendar (ver README).
    google_calendar_scope: str = "https://www.googleapis.com/auth/calendar.app.created"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""

    # App
    timezone: str = "America/Argentina/Buenos_Aires"
    database_url: str = "sqlite:///./data/agent.db"
    poll_interval_minutes: int = 15
    initial_lookback_days: int = 14
    daily_digest_hour: int = 9
    public_url: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
