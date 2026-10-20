"""Credenciales de Google y los unicos permisos que el agente pide.

GMAIL_SCOPE es de solo lectura a proposito: con este permiso la API de Google
rechaza cualquier intento de modificar, archivar, marcar o borrar un mail.
No es una regla del codigo que se pueda saltear por error, es el token.
"""

from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import settings

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_SCOPE = settings.google_calendar_scope
SCOPES = [GMAIL_SCOPE, CALENDAR_SCOPE]

# Con calendar.app.created el agente no puede siquiera listar tus calendarios:
# solo ve los que creo el. Es el permiso mas chico que le alcanza para trabajar.
NARROW_CALENDAR = CALENDAR_SCOPE.endswith("calendar.app.created")

TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleCredentialsMissing(RuntimeError):
    pass


def build_credentials() -> Credentials:
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", settings.google_client_id),
            ("GOOGLE_CLIENT_SECRET", settings.google_client_secret),
            ("GOOGLE_REFRESH_TOKEN", settings.google_refresh_token),
        )
        if not value
    ]
    if missing:
        raise GoogleCredentialsMissing(
            "Faltan variables de Google: "
            + ", ".join(missing)
            + ". Corre `python scripts/google_oauth.py` para obtenerlas."
        )

    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def assert_gmail_is_read_only(creds: Credentials) -> None:
    """Corta el arranque si el token trae permisos de escritura sobre el mail.

    Si alguien amplia los scopes por descuido, preferimos no arrancar antes que
    correr con permiso para tocar la casilla.
    """
    granted = set(creds.scopes or SCOPES)
    forbidden = {
        s
        for s in granted
        if s.startswith("https://www.googleapis.com/auth/gmail")
        and s != GMAIL_SCOPE
    }
    if forbidden:
        raise RuntimeError(
            "El token de Google tiene permisos de Gmail que no son de solo lectura: "
            + ", ".join(sorted(forbidden))
            + ". Volve a generar el token con scripts/google_oauth.py."
        )
