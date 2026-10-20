"""Corre esto UNA VEZ en tu PC para obtener el GOOGLE_REFRESH_TOKEN.

Abre el navegador, te pide permiso para leer tu Gmail y para escribir en tu
Calendar, y al final imprime las tres variables que van al .env.

    python scripts/google_oauth.py

Necesita que exista `client_secret.json` en la raiz del proyecto (el que bajas
de Google Cloud Console), o que GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET ya esten
en el .env.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.google_auth import SCOPES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SECRET_FILE = ROOT / "client_secret.json"


def client_config() -> dict:
    if SECRET_FILE.exists():
        data = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
        print(f"Usando {SECRET_FILE.name}")
        return data

    from app.config import settings

    client_id = settings.google_client_id or input("GOOGLE_CLIENT_ID: ").strip()
    client_secret = settings.google_client_secret or input("GOOGLE_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        sys.exit(
            "Falta el client id/secret. Bajá client_secret.json de Google Cloud "
            "Console y ponelo en la raiz del proyecto."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def main() -> None:
    config = client_config()

    print("\nPermisos que se van a pedir:")
    for scope in SCOPES:
        etiqueta = "LEER tu Gmail (sin poder modificar nada)" if "gmail" in scope else "ver y crear eventos en tu Calendar"
        print(f"  - {scope}\n      -> {etiqueta}")
    print()

    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    # access_type=offline + prompt=consent es lo que hace que Google devuelva
    # un refresh token y no solo uno de acceso que dura una hora.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Abriendo el navegador para que des permiso...",
        success_message="Listo, ya podes cerrar esta pestana.",
    )

    if not creds.refresh_token:
        sys.exit(
            "Google no devolvio refresh token. Entra a "
            "https://myaccount.google.com/permissions, quitale el acceso a esta "
            "app y volve a correr el script."
        )

    key_id = config["installed"]["client_id"] if "installed" in config else config["web"]["client_id"]
    key_secret = (
        config["installed"]["client_secret"] if "installed" in config else config["web"]["client_secret"]
    )

    print("\n" + "=" * 70)
    print("Copia estas tres lineas en tu .env (y en las variables del hosting):")
    print("=" * 70)
    print(f"GOOGLE_CLIENT_ID={key_id}")
    print(f"GOOGLE_CLIENT_SECRET={key_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 70)
    print(
        "\nOJO: si en Google Cloud Console la app quedo en estado 'Testing', este\n"
        "token se vence en 7 dias. Pasala a 'In production' (OAuth consent screen\n"
        "-> Publish app) para que no caduque. Ver el README.\n"
    )

    if os.name == "nt":
        print("Si no te abrio el navegador, copia la URL que salio arriba a mano.")


if __name__ == "__main__":
    main()
