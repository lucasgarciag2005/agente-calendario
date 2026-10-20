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


def gemini() -> str:
    from app.llm import client, list_available_models

    disponibles = set(list_available_models())
    cadena = settings.modelos
    faltan = [m for m in cadena if m not in disponibles]
    if faltan:
        raise RuntimeError(f"tu key no conoce: {', '.join(faltan)}")

    # El primero que conteste es el que se va a usar. Los demas son respaldo
    # para cuando ese se quede sin cuota del dia.
    for i, m in enumerate(cadena):
        try:
            resp = client().models.generate_content(model=m, contents="Responde solo: ok")
            resto = len(cadena) - i - 1
            return f"{m} -> {(resp.text or '').strip()[:12]} ({resto} de respaldo)"
        except Exception as exc:
            if "RESOURCE_EXHAUSTED" in str(exc):
                continue
            raise
    raise RuntimeError(f"los {len(cadena)} modelos sin cuota por hoy; vuelve manana")


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
    print("Cadena de modelos: " + " -> ".join(settings.modelos) + "\n")

    check("Base de datos", base_datos)
    check("Gemini", gemini)
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
