"""Que tan lejos puede viajar el texto de cada remitente.

Algunos mails traen datos que no tiene sentido mandarle a un modelo: el alta de
empleado de ADP con numero de legajo, direccion y datos bancarios. Para esos,
el agente te avisa que llegaron y los leas vos. El cuerpo nunca sale de tu
maquina.
"""

from __future__ import annotations

# El agente lee el mail completo y extrae las fechas limite. Es el modo normal.
FULL = "full"
# Solo el asunto viaja al modelo. El cuerpo nunca sale. Sirve cuando el asunto
# ya dice lo importante ("Action required: complete your I-9 by Friday").
SUBJECT_ONLY = "subject_only"
# No se manda nada. Solo te avisa que llego y lo abris vos en Gmail.
NOTIFY_ONLY = "notify_only"

# Se evalua en orden: gana la primera coincidencia. El patron se busca dentro
# del remitente completo, asi que sirve tanto un dominio como una direccion.
SENDER_RULES: list[tuple[str, str]] = [
    ("adp.com", NOTIFY_ONLY),
]


def mode_for(sender: str) -> str:
    """Devuelve FULL, SUBJECT_ONLY o NOTIFY_ONLY para ese remitente."""
    low = (sender or "").lower()
    for pattern, mode in SENDER_RULES:
        if pattern.lower() in low:
            return mode
    return FULL
