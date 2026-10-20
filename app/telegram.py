"""Cliente de la Bot API de Telegram: por aca te escribe y lo escribis."""

from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

TIMEOUT = 20.0


class TelegramError(RuntimeError):
    pass


def esc(text: str) -> str:
    """Escapa texto para el parse_mode HTML de Telegram."""
    return html.escape(text or "", quote=False)


def _url(method: str) -> str:
    if not settings.telegram_bot_token:
        raise TelegramError("Falta TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(_url(method), json=payload, timeout=TIMEOUT)
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', data)}")
    return data.get("result") or {}


def buttons(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    """Teclado inline. Cada boton es (texto, callback_data)."""
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in rows
        ]
    }


def resolve_chat_id() -> str:
    """El chat al que le escribe el agente: del .env o del /start que mandaste."""
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    from app.db import session_scope
    from app.store import kv_get

    with session_scope() as s:
        return kv_get(s, "telegram_chat_id")


def send(
    text: str,
    keyboard: dict[str, Any] | None = None,
    chat_id: str | None = None,
) -> int | None:
    target = chat_id or resolve_chat_id()
    if not target:
        log.warning("No hay chat de Telegram todavia; mandale /start al bot.")
        return None

    payload: dict[str, Any] = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard

    try:
        return call("sendMessage", payload).get("message_id")
    except TelegramError as exc:
        log.error("No se pudo mandar el mensaje: %s", exc)
        return None


def edit(message_id: int, text: str, chat_id: str | None = None) -> None:
    """Reescribe un mensaje ya mandado y le saca los botones."""
    target = chat_id or resolve_chat_id()
    if not target:
        return
    try:
        call(
            "editMessageText",
            {
                "chat_id": target,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
    except TelegramError as exc:
        log.warning("No se pudo editar el mensaje %s: %s", message_id, exc)


def answer_callback(callback_id: str, text: str = "") -> None:
    try:
        call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except TelegramError as exc:
        log.warning("answerCallbackQuery fallo: %s", exc)


def set_webhook(public_url: str) -> dict[str, Any]:
    url = public_url.rstrip("/") + "/telegram/webhook"
    return call(
        "setWebhook",
        {
            "url": url,
            "secret_token": settings.telegram_webhook_secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        },
    )


def set_commands() -> None:
    call(
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "Conectar este chat con el agente"},
                {"command": "agenda", "description": "Que tengo los proximos 7 dias"},
                {"command": "pendientes", "description": "Cosas sin hacer"},
                {"command": "revisar", "description": "Revisar el mail ahora"},
                {"command": "temas", "description": "Temas que vigila en el mail"},
                {"command": "estado", "description": "Chequear que todo este conectado"},
            ]
        },
    )
