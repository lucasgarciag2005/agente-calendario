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


def claim_update(update_id: int) -> bool:
    """Reserva un update de Telegram. False si ya se habia procesado.

    Telegram reintenta un update si tardamos en contestar, y ahora que el
    trabajo se hace antes de responder, tardar es normal. Sin esta reserva un
    reintento agendaria el mismo evento dos veces.
    """
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal

    s = SessionLocal()
    try:
        s.add(KeyValue(key=f"tg:{update_id}", value=""))
        s.commit()
        return True
    except IntegrityError:
        s.rollback()
        return False
    finally:
        s.close()


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
    # Terminos especificos del programa, no palabras sueltas. "visa", "vuelo",
    # "host" o "empleador" por si solas traen promos de aerolineas, recibos de
    # tarjeta Visa y alertas de LinkedIn, y cada falso positivo cuesta un
    # analisis. Lo que de verdad afina esto es cargar el remitente de la
    # agencia en `senders` apenas lo sepas.
    "keywords": [
        # La agencia, el empleador y el sistema de sueldos: especificos, casi
        # sin ruido. Van tambien como palabra y no solo como remitente, asi
        # entra un mail que los menciona aunque venga de otra direccion.
        "weusa",
        "westgate",
        "slopeside",  # el alojamiento en Park City
        "adp",
        # Terminos del programa que no aparecen en ningun otro contexto.
        "work and travel",
        "work & travel",
        "DS-160",
        "DS 160",
        "DS-2019",
        "DS 2019",
        "SEVIS",
        "I-901",
        "exchange visitor",
        "entrevista consular",
        "cita consular",
        # "visa" sola trae todos los recibos de tarjeta, asi que solo cuenta
        # si viene con una palabra del tramite. Gmail interpreta el espacio
        # como Y, de modo que esto es: visa Y (alguna de estas).
        "(visa (consulado OR embajada OR entrevista OR cita OR sponsor"
        " OR appointment OR interview OR J-1 OR DS-160))",
    ],
    # Los remitentes que ya aparecieron en la casilla. Un mail de ellos entra
    # aunque no diga ninguna palabra clave: eso salva los hilos tipo "Re: Re:"
    # y los cambios de ultimo momento.
    "senders": [
        "weusa.com.ar",  # la agencia
        "westgateresorts.com",  # el empleador
        "adp.com",  # sueldos
        "gozego.com",  # pagos del alojamiento
        "docusign.net",  # contratos para firmar
    ],
    "senders_only": False,
}


def seed_default_topic(s: Session) -> None:
    existing = s.scalar(select(Topic).where(Topic.slug == DEFAULT_TOPIC["slug"]))
    if existing is None:
        s.add(Topic(**DEFAULT_TOPIC))
