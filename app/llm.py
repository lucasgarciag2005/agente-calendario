"""Las dos llamadas al modelo: clasificar un mail y entender lo que le escribis.

Usa Gemini por el tier gratuito de Google AI Studio. Las dos llamadas piden
salida estructurada contra un esquema Pydantic, asi que la respuesta siempre
valida y no hay que parsear texto libre.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from functools import lru_cache
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.models import Topic

log = logging.getLogger(__name__)

Confianza = Literal["alta", "media", "baja"]
CalendarKey = Literal["work_travel", "clases", "viajes", "general"]
DiaSemana = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _normalizar(*campos: str, mayuscula: bool = False):
    """Acepta que el modelo devuelva "Alta" donde el esquema pide "alta".

    Algunos modelos capitalizan los valores de enumeracion y eso rompe la
    validacion de Pydantic, que distingue mayusculas. Lo vimos en la prueba.
    """

    @field_validator(*campos, mode="before")
    @classmethod
    def _v(cls, value):
        if isinstance(value, str):
            return value.upper() if mayuscula else value.lower()
        if isinstance(value, list):
            return [v.upper() if mayuscula else v.lower() if isinstance(v, str) else v for v in value]
        return value

    return _v


class LLMRefusal(RuntimeError):
    """El modelo no devolvio una respuesta utilizable.

    Puede ser un bloqueo por filtros de seguridad o una respuesta cortada. En
    cualquier caso se avisa por Telegram y el mail queda sin tocar.
    """


@lru_cache
def client() -> genai.Client:
    if not settings.gemini_api_key:
        raise RuntimeError(
            "Falta GEMINI_API_KEY. Sacala gratis en https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=settings.gemini_api_key)


# El tier gratuito devuelve 503 cuando el modelo esta saturado y 429 cuando te
# pasaste de cuota. Las dos son transitorias y bastante comunes, asi que no
# vale la pena perder un mail por eso.
# Pocos reintentos y cortos a proposito: con cinco modelos en la cadena,
# cambiar de modelo es mas barato que esperar a que uno saturado se libere.
# Con 4 intentos y 4s de base se perdian 28 segundos antes de pasar al
# siguiente, y vos esperabas media hora por un "hola".
REINTENTOS = 2
ESPERA_BASE = 1.5


def _cuota_diaria_agotada(exc: Exception) -> bool:
    """Un 429 de cuota diaria. Esperar no sirve: Google dice de volver en horas."""
    texto = str(exc)
    return "RESOURCE_EXHAUSTED" in texto.upper() and (
        "PerDay" in texto or "per day" in texto.lower()
    )


def _es_transitorio(exc: Exception) -> bool:
    """Saturacion momentanea o limite por minuto: esperar un poco si sirve."""
    if _cuota_diaria_agotada(exc):
        return False
    codigo = getattr(exc, "code", None)
    if codigo in (429, 503, 500):
        return True
    texto = str(exc).upper()
    return any(s in texto for s in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL"))


def _llamar(model: str, user_text: str, config) -> object:
    """Una llamada a un modelo, con reintentos para los errores pasajeros."""
    for intento in range(1, REINTENTOS + 1):
        try:
            return client().models.generate_content(
                model=model, contents=user_text, config=config
            )
        except Exception as exc:
            if intento == REINTENTOS or not _es_transitorio(exc):
                raise
            espera = ESPERA_BASE * (2 ** (intento - 1))
            log.warning(
                "%s no disponible (intento %s/%s), reintento en %.0fs: %s",
                model, intento, REINTENTOS, espera, str(exc)[:110],
            )
            time.sleep(espera)
    raise RuntimeError("inalcanzable")


def _generate(system: str, user_text: str, schema: type[BaseModel]):
    """Salida estructurada, recorriendo la cadena de modelos de respaldo.

    La cuota gratuita se cuenta por modelo, asi que cuando uno se queda sin
    saldo del dia pasamos al siguiente en vez de perder el mail.
    """
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        # Es extraccion de datos, no escritura: queremos la lectura mas
        # literal posible del mail, no una interpretacion creativa.
        temperature=0.0,
    )

    modelos = settings.modelos
    ultimo: Exception | None = None

    for i, model in enumerate(modelos):
        try:
            response = _llamar(model, user_text, config)
            if i:
                log.info("Resuelto con el modelo de respaldo %s", model)
            break
        except Exception as exc:
            ultimo = exc
            # Pasamos al siguiente modelo tanto si este se quedo sin cuota del
            # dia como si sigue caido despues de los reintentos. Un error que
            # no sea de disponibilidad (un esquema mal armado, por ejemplo) se
            # propaga enseguida: probarlo en otro modelo no lo va a arreglar.
            if not (_cuota_diaria_agotada(exc) or _es_transitorio(exc)):
                raise
            if i + 1 >= len(modelos):
                raise
            motivo = "sin cuota por hoy" if _cuota_diaria_agotada(exc) else "no responde"
            log.warning("%s %s, paso a %s", model, motivo, modelos[i + 1])
            continue
    else:
        raise ultimo or RuntimeError("sin modelos disponibles")

    parsed = response.parsed
    if parsed is None:
        motivo = "sin candidatos"
        if getattr(response, "candidates", None):
            motivo = str(getattr(response.candidates[0], "finish_reason", "?"))
        elif getattr(response, "prompt_feedback", None):
            motivo = str(getattr(response.prompt_feedback, "block_reason", "?"))
        raise LLMRefusal(f"el modelo no devolvio datos validos ({motivo})")

    return parsed


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

    _norm = _normalizar("confianza")


class EmailAnalysis(BaseModel):
    relevante: bool = Field(description="True solo si el mail es de un tema vigilado")
    tema: str = Field(default="", description="El slug del tema, o vacio")
    resumen: str = Field(description="Una o dos frases en espanol sobre que dice el mail")
    requiere_accion: bool = Field(description="True si vos tenes que hacer algo")
    accion: str = Field(default="", description="Que tenes que hacer, corto y concreto")
    fechas_limite: list[Deadline] = Field(default_factory=list)
    urgencia: Confianza = Field(default="baja")

    _norm = _normalizar("urgencia")


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

    return _generate(system, f"<mail>\n{email_text}\n</mail>", EmailAnalysis)


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

    _norm = _normalizar("calendario")
    _norm_dias = _normalizar("dias_semana", mayuscula=True)


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

    _norm = _normalizar("intencion")


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

    return _generate(system, text, ChatIntent)


def list_available_models() -> list[str]:
    """Para chequear con que modelos responde tu key."""
    out = []
    for m in client().models.list():
        actions = getattr(m, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            out.append((m.name or "").removeprefix("models/"))
    return sorted(out)
