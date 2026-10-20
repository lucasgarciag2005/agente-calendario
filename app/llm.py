"""Las dos llamadas a Claude: clasificar un mail y entender lo que le escribis.

Las dos usan structured outputs, asi que la respuesta siempre valida contra el
esquema y no hace falta parsear texto libre.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.models import Topic

log = logging.getLogger(__name__)

Confianza = Literal["alta", "media", "baja"]
CalendarKey = Literal["work_travel", "clases", "viajes", "general"]
DiaSemana = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


class LLMRefusal(RuntimeError):
    """Claude declino responder. Se avisa por Telegram y el mail queda sin tocar."""


@lru_cache
def client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError("Falta ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


# --------------------------------------------------------------------------- #
# Analisis de un mail
# --------------------------------------------------------------------------- #


class Deadline(BaseModel):
    titulo: str = Field(description="Que hay que hacer, en imperativo y corto")
    fecha: str = Field(description="Fecha limite en formato YYYY-MM-DD")
    hora: str = Field(default="", description="HH:MM si el mail la dice, si no vacio")
    confianza: Confianza = Field(
        description="alta si la fecha esta escrita explicita en el mail, "
        "baja si la inferiste"
    )
    cita: str = Field(
        default="",
        description="El fragmento textual del mail de donde sacaste la fecha",
    )


class EmailAnalysis(BaseModel):
    relevante: bool = Field(description="True solo si el mail es de un tema vigilado")
    tema: str = Field(default="", description="El slug del tema, o vacio")
    resumen: str = Field(description="Una o dos frases en espanol sobre que dice el mail")
    requiere_accion: bool = Field(description="True si vos tenes que hacer algo")
    accion: str = Field(default="", description="Que tenes que hacer, corto y concreto")
    fechas_limite: list[Deadline] = Field(default_factory=list)
    urgencia: Confianza = Field(default="baja")


EMAIL_SYSTEM = """\
Sos el asistente personal de un estudiante argentino. Tu unico trabajo en esta
llamada es leer UN mail y decidir si le importa, segun una lista de temas que el
definio. No hagas nada mas.

Reglas:
- "relevante" es True solo si el mail trata de alguno de los temas listados.
  Promociones, newsletters, redes sociales, resumenes de tarjeta y notificaciones
  automaticas no son relevantes, aunque mencionen una palabra del tema.
- "requiere_accion" es True solo si el que tiene que hacer algo es EL, no el
  remitente. Un mail que confirma que ya pago no requiere accion.
- En "fechas_limite" pone solo fechas en las que el tiene que haber hecho algo.
  No pongas la fecha del viaje, ni la fecha del mail, ni fechas que ya pasaron.
- Las fechas van en YYYY-MM-DD. Si el mail dice algo relativo ("en 10 dias",
  "el viernes que viene"), calculalo contra la fecha de hoy que te paso abajo.
- "confianza" alta es solo para fechas escritas de forma explicita en el mail.
  Si la dedujiste del contexto, poné media o baja.
- En "cita" copia textual el fragmento del mail que contiene la fecha. Si no hay
  un fragmento asi, dejalo vacio y bajá la confianza.
- Escribi "resumen" y "accion" en espanol rioplatense, directo, sin saludos.
"""


def analyze_email(email_text: str, topics: list[Topic]) -> EmailAnalysis:
    today = dt.datetime.now(settings.tz).date()
    temas = "\n".join(
        f'- slug "{t.slug}" ({t.name}): {t.description}' for t in topics
    ) or "- (no hay temas configurados)"

    system = (
        f"{EMAIL_SYSTEM}\n"
        f"Fecha de hoy: {today.isoformat()} ({today.strftime('%A')}).\n"
        f"Zona horaria: {settings.timezone}.\n\n"
        f"Temas que vigila:\n{temas}\n"
    )

    resp = client().messages.parse(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": f"<mail>\n{email_text}\n</mail>"}],
        output_format=EmailAnalysis,
    )
    if resp.stop_reason == "refusal":
        raise LLMRefusal(getattr(resp.stop_details, "explanation", "") or "sin detalle")
    return resp.parsed_output


# --------------------------------------------------------------------------- #
# Lo que le escribis por Telegram
# --------------------------------------------------------------------------- #


class ChatEvent(BaseModel):
    titulo: str
    calendario: CalendarKey = Field(
        description="clases para cursada, viajes para dias de viaje, "
        "work_travel para tramites del programa, general para el resto"
    )
    fecha_inicio: str = Field(description="YYYY-MM-DD")
    hora_inicio: str = Field(default="", description="HH:MM, vacio si es todo el dia")
    fecha_fin: str = Field(default="", description="YYYY-MM-DD, vacio si es un solo dia")
    hora_fin: str = Field(default="", description="HH:MM, vacio si no aplica")
    todo_el_dia: bool = Field(default=True)
    dias_semana: list[DiaSemana] = Field(
        default_factory=list,
        description="Para algo semanal, los dias. Vacio si no se repite",
    )
    repetir_hasta: str = Field(
        default="", description="YYYY-MM-DD hasta cuando se repite, vacio si no aplica"
    )
    es_fecha_limite: bool = Field(
        default=False, description="True si es un vencimiento y no una actividad"
    )
    notas: str = Field(default="")


class NuevoTema(BaseModel):
    nombre: str = Field(default="")
    descripcion: str = Field(default="")
    palabras_clave: list[str] = Field(default_factory=list)


class ChatIntent(BaseModel):
    intencion: Literal[
        "agendar", "ver_agenda", "ver_pendientes", "agregar_tema", "revisar_mail", "charla"
    ]
    respuesta: str = Field(description="Que le contestas, una o dos frases")
    eventos: list[ChatEvent] = Field(default_factory=list)
    tema: NuevoTema = Field(default_factory=NuevoTema)


CHAT_SYSTEM = """\
Sos el asistente personal de un estudiante argentino, por chat. El te escribe
cosas para agendar o te pregunta por su agenda. Interpretá el mensaje y devolvé
la intencion.

Intenciones:
- "agendar": te esta diciendo algo que va al calendario (una clase, un viaje, un
  tramite, un vencimiento). Llena "eventos", uno por cada cosa distinta.
- "ver_agenda": quiere saber que tiene en los proximos dias.
- "ver_pendientes": quiere la lista de cosas sin hacer.
- "agregar_tema": quiere que de ahora en mas vigiles los mails de otro asunto.
  Llena "tema".
- "revisar_mail": quiere que revises la casilla ahora mismo.
- "charla": cualquier otra cosa. Contestale corto en "respuesta".

Reglas para fechas:
- Todo en YYYY-MM-DD y horas en HH:MM de 24 horas.
- Las fechas relativas ("manana", "el lunes", "en dos semanas") se calculan
  contra la fecha de hoy que te paso abajo.
- Si dice un rango de dias sin horario ("del 5 al 20 de diciembre estoy de
  viaje"), es "todo_el_dia" True con "fecha_inicio" y "fecha_fin".
- Si dice algo semanal ("los lunes y miercoles de 18 a 20"), llena
  "dias_semana" y las horas, y poné "todo_el_dia" en False. Si no aclara hasta
  cuando, dejá "repetir_hasta" vacio.
- Si falta un dato clave como la fecha, no inventes: poné intencion "charla" y
  preguntale en "respuesta" lo que falta.
- Hablale en espanol rioplatense, directo, sin saludos ni formulas.
"""


def parse_chat_message(text: str, topics: list[Topic]) -> ChatIntent:
    now = dt.datetime.now(settings.tz)
    temas = ", ".join(t.name for t in topics) or "(ninguno)"

    system = (
        f"{CHAT_SYSTEM}\n"
        f"Fecha y hora de ahora: {now.isoformat(timespec='minutes')} "
        f"({now.strftime('%A')}).\n"
        f"Zona horaria: {settings.timezone}.\n"
        f"Temas que ya vigilas: {temas}.\n"
    )

    resp = client().messages.parse(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": text}],
        output_format=ChatIntent,
    )
    if resp.stop_reason == "refusal":
        raise LLMRefusal(getattr(resp.stop_details, "explanation", "") or "sin detalle")
    return resp.parsed_output
