"""Lectura de Gmail. Solo lectura: no hay un solo metodo que escriba.

La API se construye con un token de scope gmail.readonly, asi que incluso un
bug no podria modificar, archivar ni borrar nada en la casilla.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from googleapiclient.discovery import build

from app.google_auth import assert_gmail_is_read_only, build_credentials

MAX_BODY_CHARS = 6000


@dataclass
class Email:
    gmail_id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    date: dt.datetime | None
    body: str
    # Lo que los servidores de Google verificaron al recibir el mail.
    auth_results: str = ""

    def as_prompt_text(self) -> str:
        fecha = self.date.isoformat() if self.date else "desconocida"
        return (
            f"De: {self.sender}\n"
            f"Para: {self.to}\n"
            f"Fecha: {fecha}\n"
            f"Asunto: {self.subject}\n\n"
            f"{self.body}"
        )

    def as_subject_only(self) -> str:
        """Igual que arriba pero sin el cuerpo, para remitentes sensibles."""
        fecha = self.date.isoformat() if self.date else "desconocida"
        return (
            f"De: {self.sender}\n"
            f"Fecha: {fecha}\n"
            f"Asunto: {self.subject}\n\n"
            "(El cuerpo de este mail no se comparte por privacidad. "
            "Trabaja solo con el asunto.)"
        )

    def gmail_link(self) -> str:
        return f"https://mail.google.com/mail/u/0/#inbox/{self.thread_id}"


def _service():
    creds = build_credentials()
    assert_gmail_is_read_only(creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")


def _collect_body(payload: dict) -> str:
    """Recorre las partes del mail y devuelve texto plano."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(_decode(data))
            elif mime == "text/html":
                html.append(_decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)

    if plain:
        text = "\n".join(plain)
    elif html:
        text = BeautifulSoup("\n".join(html), "html.parser").get_text("\n")
    else:
        text = ""

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()[:MAX_BODY_CHARS]


def _header(headers: list[dict], name: str) -> str:
    target = name.lower()
    for h in headers:
        if h.get("name", "").lower() == target:
            return h.get("value", "")
    return ""


# Pestanas de Gmail que nunca traen un tramite: promociones de aerolineas,
# avisos de LinkedIn, novedades de foros. Sacarlas del prefiltro ahorra el
# analisis de decenas de mails por mes.
EXCLUDED_CATEGORIES = ["promotions", "social", "forums"]


def build_query(
    keywords: list[str],
    senders: list[str],
    after_epoch: int | None,
    senders_only: bool = False,
) -> str:
    """Prefiltro barato del lado de Gmail: solo bajamos mails candidatos.

    Con senders_only solo entran los mails de esos remitentes. Es el modo mas
    preciso y el mas barato, porque cada mail que pasa el filtro es uno que de
    verdad hay que analizar.
    """
    if senders_only and senders:
        clause = "(" + " OR ".join(f"from:{s}" for s in senders) + ")"
        parts = [clause]
        if after_epoch:
            parts.append(f"after:{after_epoch}")
        parts.append("-in:spam")
        parts.append("-in:trash")
        return " ".join(parts)

    # Las comillas fuerzan coincidencia exacta. Sin ellas Gmail parte los
    # terminos con guion ("DS-160" -> DS sin 160) y ensucia los resultados.
    # Un termino que ya viene entre parentesis se pasa tal cual: sirve para
    # exigir que una palabra ambigua venga acompanada, por ejemplo
    # "(visa (consulado OR entrevista))".
    terms = [k if k.startswith("(") else f'"{k}"' for k in keywords]
    parts: list[str] = []

    # Un remitente conocido entra aunque caiga en Promociones; una palabra
    # clave suelta, no.
    sender_clause = " OR ".join(f"from:{s}" for s in senders)
    keyword_clause = " OR ".join(terms)
    if keyword_clause:
        negatives = " ".join(f"-category:{c}" for c in EXCLUDED_CATEGORIES)
        keyword_clause = f"({keyword_clause}) {negatives}"

    if sender_clause and keyword_clause:
        parts.append(f"{{ ({sender_clause}) ({keyword_clause}) }}")
    elif sender_clause:
        parts.append(f"({sender_clause})")
    elif keyword_clause:
        parts.append(keyword_clause)

    if after_epoch:
        parts.append(f"after:{after_epoch}")
    parts.append("-in:spam")
    parts.append("-in:trash")
    return " ".join(parts)


def list_message_ids(query: str, limit: int = 60) -> list[str]:
    svc = _service()
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < limit:
        resp = (
            svc.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(100, limit - len(ids)),
                pageToken=page_token,
                includeSpamTrash=False,
            )
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def get_email(gmail_id: str) -> Email:
    svc = _service()
    msg = (
        svc.users()
        .messages()
        .get(userId="me", id=gmail_id, format="full")
        .execute()
    )
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    internal = msg.get("internalDate")
    date = (
        dt.datetime.fromtimestamp(int(internal) / 1000, tz=dt.timezone.utc)
        if internal
        else None
    )
    return Email(
        gmail_id=msg["id"],
        thread_id=msg.get("threadId", ""),
        subject=_header(headers, "Subject"),
        sender=_header(headers, "From"),
        to=_header(headers, "To"),
        date=date,
        body=_collect_body(payload),
        auth_results=_header(headers, "Authentication-Results"),
    )


def check_connection() -> str:
    """Devuelve la direccion de la casilla, para verificar el setup."""
    svc = _service()
    profile = svc.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "?")
