"""Escritura y lectura en Google Calendar.

El agente trabaja sobre calendarios propios ("Agente - ...") y nunca toca
eventos de tus otros calendarios. Asi podes ocultarlos o borrarlos de una sin
perder nada tuyo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings
from app.google_auth import build_credentials

# clave interna -> (nombre visible en Google Calendar, color)
CALENDARS: dict[str, tuple[str, str]] = {
    "work_travel": ("Agente - Work and Travel", "6"),  # naranja
    "clases": ("Agente - Clases", "9"),  # azul
    "viajes": ("Agente - Viajes", "10"),  # verde
    "general": ("Agente - General", "8"),  # gris
}

DEADLINE_REMINDERS = [
    {"method": "popup", "minutes": 7 * 24 * 60},
    {"method": "popup", "minutes": 2 * 24 * 60},
    {"method": "popup", "minutes": 3 * 60},
]


@dataclass
class CalEvent:
    event_id: str
    calendar_id: str
    summary: str
    start: str
    end: str
    all_day: bool
    html_link: str


def _service():
    return build("calendar", "v3", credentials=build_credentials(), cache_discovery=False)


def _find_existing(svc, name: str) -> str | None:
    """Busca un calendario del agente de una corrida anterior.

    Es solo un atajo para no duplicar calendarios si se perdio la base. Con el
    permiso chico (calendar.app.created) listar no esta permitido y Google
    devuelve 403: en ese caso seguimos de largo y creamos el calendario.
    """
    page_token = None
    try:
        while True:
            resp = svc.calendarList().list(pageToken=page_token).execute()
            for item in resp.get("items", []):
                if item.get("summary") == name:
                    return item["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                return None
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            return None
        raise


def ensure_calendar(key: str) -> str:
    """Devuelve el id del calendario del agente, creandolo la primera vez."""
    from app.db import session_scope
    from app.store import kv_get, kv_set

    name, color = CALENDARS.get(key, CALENDARS["general"])
    kv_key = f"calendar_id:{key}"

    with session_scope() as s:
        cached = kv_get(s, kv_key)
    if cached:
        return cached

    svc = _service()

    found = _find_existing(svc, name)
    if found:
        with session_scope() as s:
            kv_set(s, kv_key, found)
        return found

    created = svc.calendars().insert(
        body={"summary": name, "timeZone": settings.timezone}
    ).execute()
    calendar_id = created["id"]

    try:
        svc.calendarList().patch(
            calendarId=calendar_id, body={"colorId": color}
        ).execute()
    except HttpError:
        pass  # el color es cosmetico; si falla seguimos

    with session_scope() as s:
        kv_set(s, kv_key, calendar_id)
    return calendar_id


def _time_fields(
    start: dt.datetime, end: dt.datetime | None, all_day: bool
) -> tuple[dict, dict]:
    if all_day:
        start_date = start.astimezone(settings.tz).date()
        end_date = (end.astimezone(settings.tz).date() if end else start_date)
        # En Google el fin de un evento de dia completo es exclusivo.
        return (
            {"date": start_date.isoformat()},
            {"date": (end_date + dt.timedelta(days=1)).isoformat()},
        )
    end = end or (start + dt.timedelta(hours=1))
    return (
        {"dateTime": start.astimezone(settings.tz).isoformat(), "timeZone": settings.timezone},
        {"dateTime": end.astimezone(settings.tz).isoformat(), "timeZone": settings.timezone},
    )


def create_event(
    calendar_key: str,
    summary: str,
    start: dt.datetime,
    end: dt.datetime | None = None,
    all_day: bool = True,
    description: str = "",
    recurrence: str | None = None,
    is_deadline: bool = False,
) -> CalEvent:
    calendar_id = ensure_calendar(calendar_key)
    start_field, end_field = _time_fields(start, end, all_day)

    body: dict = {
        "summary": summary,
        "description": description,
        "start": start_field,
        "end": end_field,
    }
    if recurrence:
        body["recurrence"] = [recurrence]
    if is_deadline:
        body["reminders"] = {"useDefault": False, "overrides": DEADLINE_REMINDERS}

    created = _service().events().insert(calendarId=calendar_id, body=body).execute()
    return CalEvent(
        event_id=created["id"],
        calendar_id=calendar_id,
        summary=created.get("summary", summary),
        start=str(created.get("start", {})),
        end=str(created.get("end", {})),
        all_day=all_day,
        html_link=created.get("htmlLink", ""),
    )


def delete_event(calendar_id: str, event_id: str) -> None:
    """Solo se usa para deshacer algo que el propio agente creo."""
    try:
        _service().events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp.status not in (404, 410):
            raise


def upcoming(days: int = 7) -> list[CalEvent]:
    """Eventos de los calendarios del agente en los proximos N dias."""
    svc = _service()
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=days)
    out: list[CalEvent] = []

    for key in CALENDARS:
        try:
            calendar_id = ensure_calendar(key)
        except HttpError:
            continue
        resp = (
            svc.events()
            .list(
                calendarId=calendar_id,
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )
        for item in resp.get("items", []):
            start = item.get("start", {})
            out.append(
                CalEvent(
                    event_id=item["id"],
                    calendar_id=calendar_id,
                    summary=item.get("summary", "(sin titulo)"),
                    start=start.get("dateTime") or start.get("date", ""),
                    end=item.get("end", {}).get("dateTime")
                    or item.get("end", {}).get("date", ""),
                    all_day="date" in start,
                    html_link=item.get("htmlLink", ""),
                )
            )

    out.sort(key=lambda e: e.start)
    return out
