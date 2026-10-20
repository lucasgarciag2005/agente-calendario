"""Tablas de la base de datos."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UtcDateTime(TypeDecorator):
    """Guarda siempre en UTC y devuelve siempre con zona horaria.

    SQLite no sabe guardar el huso, asi que sin esto un datetime vuelve de la
    base "desnudo" y cualquier .astimezone() lo lee como hora local del servidor.
    En un host en UTC eso corre las fechas tres horas y un vencimiento de la
    madrugada cae el dia anterior.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"datetime sin zona horaria: {value!r}")
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Topic(Base):
    """Un tema que el agente vigila en el mail. Arranca con 'work-and-travel'."""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Palabras clave para el prefiltro de Gmail (barato, sin LLM).
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Remitentes que siempre son de este tema (ej. "@ciee.org").
    senders: Mapped[list[str]] = mapped_column(JSON, default=list)
    # True = mirar SOLO esos remitentes e ignorar las palabras clave. Es el
    # modo barato y preciso: no llega un mail que no sea de ellos, asi que no
    # se gasta un analisis en basura.
    senders_only: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class SeenMessage(Base):
    """Mails ya analizados, para no procesar dos veces ni volver a notificar."""

    __tablename__ = "seen_messages"

    gmail_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    sender: Mapped[str] = mapped_column(Text, default="")
    internal_date: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    relevant: Mapped[bool] = mapped_column(Boolean, default=False)
    topic_slug: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    processed_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class Proposal(Base):
    """Algo que el agente quiere agendar y espera tu OK por Telegram."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "email" si salio de un mail, "chat" si se lo pediste vos.
    source: Mapped[str] = mapped_column(String(16), default="email")
    gmail_id: Mapped[str | None] = mapped_column(String(64))
    topic_slug: Mapped[str | None] = mapped_column(String(64))

    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    calendar_key: Mapped[str] = mapped_column(String(32), default="work_travel")

    start_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    end_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    all_day: Mapped[bool] = mapped_column(Boolean, default=True)
    # Regla RRULE de Google (clases semanales, etc.). None = evento unico.
    recurrence: Mapped[str | None] = mapped_column(Text)
    # Si es una fecha limite, mandamos recordatorios antes.
    is_deadline: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(16), default="pending")
    calendar_event_id: Mapped[str | None] = mapped_column(String(200))
    calendar_id: Mapped[str | None] = mapped_column(String(200))
    # Que recordatorios ya se mandaron, ej. [7, 3, 1].
    reminders_sent: Mapped[list[int]] = mapped_column(JSON, default=list)

    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    decided_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)


class KeyValue(Base):
    """Estado chico del agente: cursor del mail, ids de calendarios, chat id."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class PendingInput(Base):
    """Cuando el agente te pregunta algo y espera tu proxima respuesta de texto."""

    __tablename__ = "pending_input"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("proposals.id"))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
