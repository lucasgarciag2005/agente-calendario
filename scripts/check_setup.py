"""Chequea que las cuatro conexiones del agente funcionen. No manda nada.

    python scripts/check_setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

ok_all = True


def check(nombre: str, fn) -> None:
    global ok_all
    try:
        detalle = fn()
        print(f"  OK   {nombre}: {detalle}")
    except Exception as exc:
        ok_all = False
        print(f"  FALLA {nombre}: {type(exc).__name__}: {exc}")


def gmail() -> str:
    from app import gmail_client

    return gmail_client.check_connection() + " (solo lectura)"


def calendar() -> str:
    from app import calendar_client

    return "calendario listo: " + calendar_client.ensure_calendar("work_travel")


def claude() -> str:
    from app.llm import client

    resp = client().messages.create(
        model=settings.anthropic_model,
        max_tokens=16,
        messages=[{"role": "user", "content": "Responde solo: ok"}],
    )
    texto = next((b.text for b in resp.content if b.type == "text"), "")
    return f"{settings.anthropic_model} -> {texto.strip()[:20]}"


def bot() -> str:
    from app import telegram

    me = telegram.call("getMe", {})
    chat = telegram.resolve_chat_id() or "(falta: mandale /start al bot)"
    return f"@{me.get('username')} | chat: {chat}"


def base_datos() -> str:
    from app.db import init_db, session_scope
    from app.store import active_topics, seed_default_topic

    init_db()
    with session_scope() as s:
        seed_default_topic(s)
    with session_scope() as s:
        return f"{len(active_topics(s))} tema(s) activo(s)"


def main() -> None:
    print(f"\nZona horaria: {settings.timezone}")
    print(f"Modelo: {settings.anthropic_model}\n")

    check("Base de datos", base_datos)
    check("Claude", claude)
    check("Gmail", gmail)
    check("Google Calendar", calendar)
    check("Bot de Telegram", bot)

    if ok_all:
        print("\nTodo conectado. Podes levantar el servidor.\n")
    else:
        print("\nHay algo sin configurar. Revisa el .env y el README.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
