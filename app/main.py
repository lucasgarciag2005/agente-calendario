"""Servidor del agente: webhook de Telegram + las tareas programadas."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app import handlers, pipeline, telegram
from app.config import settings
from app.db import init_db, session_scope
from app.store import seed_default_topic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("agente")

scheduler = BackgroundScheduler(timezone=settings.timezone)


def _safe_poll() -> None:
    try:
        pipeline.poll_emails()
    except Exception:
        log.exception("La revision programada del mail fallo")
    # Los recordatorios van pegados a la revision, no solo al horario fijo.
    # En un hosting que duerme, el disparo de las 9:00 puede no ocurrir nunca
    # porque el proceso no esta vivo a esa hora; asi, cualquier revision del
    # dia los manda. Repetirlos no hace dano: send_reminders es idempotente.
    _safe_reminders()


def _safe_reminders() -> None:
    try:
        pipeline.send_reminders()
    except Exception:
        log.exception("El envio de recordatorios fallo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session_scope() as s:
        seed_default_topic(s)

    scheduler.add_job(
        _safe_poll,
        IntervalTrigger(minutes=settings.poll_interval_minutes),
        id="poll_emails",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _safe_reminders,
        CronTrigger(hour=settings.daily_digest_hour, minute=0),
        id="send_reminders",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "Agente arriba. Revision cada %s min, recordatorios a las %s:00 (%s).",
        settings.poll_interval_minutes,
        settings.daily_digest_hour,
        settings.timezone,
    )

    if settings.public_url and settings.telegram_bot_token:
        try:
            telegram.set_webhook(settings.public_url)
            telegram.set_commands()
            log.info("Webhook de Telegram registrado en %s", settings.public_url)
        except Exception:
            log.exception("No pude registrar el webhook de Telegram")

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Agente Work and Travel", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    """Estado del agente y que configuracion ve el proceso.

    De los secretos informa solo si estan cargados, nunca su contenido: sirve
    para darse cuenta de que falta una variable sin exponer ninguna.
    """
    secretos = {
        "GEMINI_API_KEY": settings.gemini_api_key,
        "GOOGLE_CLIENT_ID": settings.google_client_id,
        "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
        "GOOGLE_REFRESH_TOKEN": settings.google_refresh_token,
        "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
        "TELEGRAM_CHAT_ID": settings.telegram_chat_id,
        "TELEGRAM_WEBHOOK_SECRET": settings.telegram_webhook_secret,
        "DATABASE_URL": settings.database_url,
    }
    return {
        "ok": True,
        "jobs": [job.id for job in scheduler.get_jobs()],
        "chat_conectado": bool(telegram.resolve_chat_id()),
        "faltan": sorted(k for k, v in secretos.items() if not v),
        "cargadas": sorted(k for k, v in secretos.items() if v),
        "base": "postgres" if "postgres" in settings.database_url else "sqlite",
        "public_url": settings.public_url or "(vacio)",
        "modelos": len(settings.modelos),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if settings.telegram_webhook_secret and (
        x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="secreto invalido")

    update = await request.json()
    # Telegram reintenta si tardamos; contestamos ya y trabajamos atras.
    background.add_task(handlers.handle_update, update)
    return {"ok": True}


@app.post("/admin/poll")
def admin_poll(
    background: BackgroundTasks,
    x_admin_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    """Para disparar una revision desde un cron externo si el host se duerme."""
    if not settings.telegram_webhook_secret or (
        x_admin_secret != settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="secreto invalido")
    background.add_task(_safe_poll)
    return {"ok": True}
