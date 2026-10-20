"""Configuracion central, leida de variables de entorno o del archivo .env."""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Gemini, por el tier gratuito de Google AI Studio.
    gemini_api_key: str = ""
    # Analizar un mail es la decision cara de equivocar: si lee mal una fecha
    # limite, te perdes un tramite. Va con el modelo mas capaz que tengas.
    # Cadena de respaldo, en orden de preferencia. El tier gratuito da unas 20
    # consultas por dia POR MODELO, asi que cuando se agota uno el siguiente
    # arranca con su cuota intacta. Sin esto el agente se queda mudo 8 horas.
    # Los modelos "pro" no tienen cuota gratuita, por eso no estan.
    gemini_models: str = (
        "gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash,"
        "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    )

    @property
    def modelos(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

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
