"""Acceso al almacen clave-valor y a los temas vigilados."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KeyValue, Topic


def kv_get(s: Session, key: str, default: str = "") -> str:
    row = s.get(KeyValue, key)
    return row.value if row else default


def kv_set(s: Session, key: str, value: str) -> None:
    row = s.get(KeyValue, key)
    if row:
        row.value = value
    else:
        s.add(KeyValue(key=key, value=value))


def active_topics(s: Session) -> list[Topic]:
    return list(s.scalars(select(Topic).where(Topic.active.is_(True))))


DEFAULT_TOPIC = {
    "slug": "work-and-travel",
    "name": "Work and Travel",
    "description": (
        "Todo lo relacionado al programa Work and Travel de diciembre: visa J-1, "
        "entrevista consular, formulario DS-160, SEVIS, DS-2019, pasaporte, seguro "
        "medico, pasajes y vuelos, empleador o host, alojamiento, pagos a la agencia, "
        "sponsor, documentacion que haya que presentar y plazos del programa."
    ),
    "keywords": [
        "work and travel",
        "work & travel",
        "J-1",
        "J1 visa",
        "DS-160",
        "DS-2019",
        "SEVIS",
        "visa",
        "consulado",
        "embajada",
        "entrevista consular",
        "sponsor",
        "pasaje",
        "vuelo",
        "seguro medico",
        "alojamiento",
        "empleador",
        "host",
    ],
    "senders": [],
}


def seed_default_topic(s: Session) -> None:
    existing = s.scalar(select(Topic).where(Topic.slug == DEFAULT_TOPIC["slug"]))
    if existing is None:
        s.add(Topic(**DEFAULT_TOPIC))
