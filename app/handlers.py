"""Que hace el agente con cada mensaje o boton que llega de Telegram."""

from __future__ import annotations

import logging
import re
import unicodedata

from sqlalchemy import select

from app import pipeline, telegram
from app.db import session_scope
from app.llm import LLMRefusal, parse_chat_message
from app.models import Topic
from app.store import active_topics, kv_set

log = logging.getLogger(__name__)

AYUDA = (
    "Soy tu agente. Esto es lo que hago:\n\n"
    "• Reviso tu mail cada rato y, si hay algo de los temas que vigilo con "
    "una fecha limite, te lo mando aca para que me confirmes si lo agendo.\n"
    "• Escribime cualquier cosa para agendar y te la paso al calendario, "
    "por ejemplo <i>tengo clase de ingles los lunes y miercoles de 18 a 20 hasta "
    "el 20 de diciembre</i> o <i>del 5 al 20 de diciembre estoy de viaje</i>.\n"
    "• Te aviso 7, 3 y 1 dia antes de cada vencimiento.\n\n"
    "Comandos: /agenda /pendientes /revisar /temas /estado\n\n"
    "<i>Tu mail lo leo nada mas: no puedo responder, archivar ni borrar nada.</i>"
)


def slugify(text: str) -> str:
    normal = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normal).strip("-").lower()
    return slug[:60] or "tema"


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #


def handle_update(update: dict) -> None:
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception:
        log.exception("Fallo procesando el update de Telegram")
        telegram.send("Algo se rompio procesando eso. Mira los logs.")


def _handle_callback(callback: dict) -> None:
    data = callback.get("data", "")
    callback_id = callback.get("id", "")

    action, _, raw_id = data.partition(":")
    if not raw_id.isdigit():
        telegram.answer_callback(callback_id, "No entiendo ese boton.")
        return

    proposal_id = int(raw_id)
    if action == "ok":
        result = pipeline.approve(proposal_id)
    elif action == "no":
        result = pipeline.reject(proposal_id)
    elif action == "done":
        result = pipeline.mark_done(proposal_id)
    elif action == "undo":
        result = pipeline.undo(proposal_id)
    else:
        result = "No entiendo ese boton."

    telegram.answer_callback(callback_id, result)


def _handle_message(message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or message.get("caption") or "").strip()
    if not chat_id:
        return

    if not text:
        # Antes se volvia en silencio: mandabas un audio o una foto y el
        # agente no contestaba nada, que por fuera es indistinguible de que
        # este caido.
        tipo = next(
            (k for k in ("voice", "audio", "photo", "video", "video_note",
                         "document", "sticker", "location", "contact", "poll")
             if k in message),
            "ese tipo de mensaje",
        )
        nombres = {
            "voice": "los audios", "audio": "los audios", "photo": "las fotos",
            "video": "los videos", "video_note": "los videos",
            "document": "los archivos", "sticker": "los stickers",
            "location": "las ubicaciones", "contact": "los contactos",
            "poll": "las encuestas",
        }
        telegram.send(
            f"Por ahora no entiendo {nombres.get(tipo, tipo)}. "
            "Escribime en texto lo que queres agendar.",
            chat_id=chat_id,
        )
        return

    if text.startswith("/"):
        _handle_command(text, chat_id)
        return

    with session_scope() as s:
        topics = active_topics(s)

    try:
        intent = parse_chat_message(text, topics)
    except LLMRefusal:
        telegram.send("No puedo procesar ese mensaje.", chat_id=chat_id)
        return
    except Exception:
        log.exception("Fallo interpretando el mensaje")
        telegram.send("No pude interpretar eso. Probá de otra forma.", chat_id=chat_id)
        return

    if intent.intencion == "agendar" and intent.eventos:
        creadas = 0
        for event in intent.eventos:
            if pipeline.proposal_from_chat(event) is not None:
                creadas += 1
        if creadas == 0:
            telegram.send(
                "Entendi que queres agendar algo pero no me quedo clara la fecha. "
                "Decime el dia concreto.",
                chat_id=chat_id,
            )
        return

    if intent.intencion == "ver_agenda":
        telegram.send(pipeline.format_agenda(), chat_id=chat_id)
        return

    if intent.intencion == "ver_pendientes":
        telegram.send(pipeline.format_pending(), chat_id=chat_id)
        return

    if intent.intencion == "revisar_mail":
        telegram.send("Voy a revisar el mail...", chat_id=chat_id)
        stats = pipeline.poll_emails()
        telegram.send(
            f"Listo. Mire {stats['nuevos']} mail(s) nuevo(s), "
            f"{stats['relevantes']} del tema, {stats['propuestas']} propuesta(s).",
            chat_id=chat_id,
        )
        return

    if intent.intencion == "agregar_tema" and intent.tema.nombre:
        _add_topic(intent.tema, chat_id)
        return

    telegram.send(telegram.esc(intent.respuesta), chat_id=chat_id)


def _add_topic(tema, chat_id: str) -> None:
    slug = slugify(tema.nombre)
    with session_scope() as s:
        existing = s.scalar(select(Topic).where(Topic.slug == slug))
        if existing:
            existing.description = tema.descripcion or existing.description
            existing.keywords = sorted(
                set(existing.keywords or []) | set(tema.palabras_clave)
            )
            existing.active = True
            nombre = existing.name
        else:
            s.add(
                Topic(
                    slug=slug,
                    name=tema.nombre,
                    description=tema.descripcion,
                    keywords=tema.palabras_clave,
                    senders=[],
                )
            )
            nombre = tema.nombre

    telegram.send(
        f"Listo, de ahora en mas vigilo <b>{telegram.esc(nombre)}</b> en el mail.",
        chat_id=chat_id,
    )


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #


def _handle_command(text: str, chat_id: str) -> None:
    command = text.split()[0].lstrip("/").split("@")[0].lower()

    if command in ("start", "help", "ayuda"):
        with session_scope() as s:
            kv_set(s, "telegram_chat_id", chat_id)
        telegram.send(AYUDA, chat_id=chat_id)
        return

    if command == "agenda":
        telegram.send(pipeline.format_agenda(), chat_id=chat_id)
        return

    if command == "pendientes":
        telegram.send(pipeline.format_pending(), chat_id=chat_id)
        return

    if command == "revisar":
        telegram.send("Reviso el mail y te cuento.", chat_id=chat_id)
        stats = pipeline.poll_emails()
        telegram.send(
            f"Listo. {stats['nuevos']} mail(s) nuevo(s), "
            f"{stats['relevantes']} del tema, {stats['propuestas']} propuesta(s).",
            chat_id=chat_id,
        )
        return

    if command == "temas":
        telegram.send(_format_topics(), chat_id=chat_id)
        return

    if command == "estado":
        telegram.send(_format_status(), chat_id=chat_id)
        return

    telegram.send(AYUDA, chat_id=chat_id)


def _format_topics() -> str:
    with session_scope() as s:
        topics = list(s.scalars(select(Topic).order_by(Topic.created_at)))

    if not topics:
        return "No hay temas configurados."

    lines = ["\U0001f50e <b>Temas que vigilo</b>", ""]
    for topic in topics:
        marca = "✅" if topic.active else "⏸"
        lines.append(f"{marca} <b>{telegram.esc(topic.name)}</b>")
        if topic.description:
            lines.append(f"   <i>{telegram.esc(topic.description[:200])}</i>")
        if topic.keywords:
            lines.append(
                f"   claves: {telegram.esc(', '.join(topic.keywords[:12]))}"
            )
        lines.append("")
    lines.append("Para sumar otro, escribime algo como "
                 "<i>de ahora en mas segui tambien los mails de la facultad</i>.")
    return "\n".join(lines)


def _format_status() -> str:
    from app import calendar_client, gmail_client
    from app.store import kv_get

    lines = ["\U0001f6e0 <b>Estado</b>", ""]

    try:
        correo = gmail_client.check_connection()
        lines.append(f"✅ Gmail (solo lectura): {telegram.esc(correo)}")
    except Exception as exc:
        lines.append(f"❌ Gmail: {telegram.esc(str(exc)[:150])}")

    try:
        calendar_client.ensure_calendar("work_travel")
        lines.append("✅ Google Calendar conectado")
    except Exception as exc:
        lines.append(f"❌ Calendar: {telegram.esc(str(exc)[:150])}")

    with session_scope() as s:
        topics = active_topics(s)
        cursor = kv_get(s, "last_poll_epoch")

    lines.append(f"✅ Temas activos: {len(topics)}")

    if cursor:
        import datetime as dt

        from app.config import settings

        last = dt.datetime.fromtimestamp(int(cursor), tz=settings.tz)
        lines.append(f"\U0001f553 Ultima revision: {last:%d/%m %H:%M}")
    else:
        lines.append("\U0001f553 Todavia no revise el mail")

    return "\n".join(lines)
