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


def find_secret_file() -> Path | None:
    """Google lo baja como client_secret_1234-abc.apps.googleusercontent.com.json.

    Aceptamos cualquier nombre que empiece con client_secret asi no hace falta
    renombrarlo a mano. El .gitignore ya excluye client_secret*.json.
    """
    candidates = sorted(ROOT.glob("client_secret*.json"))
    return candidates[0] if candidates else None


def client_config() -> dict:
    secret_file = find_secret_file()
    if secret_file:
        data = json.loads(secret_file.read_text(encoding="utf-8"))
        print(f"Usando {secret_file.name}")
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


def write_env(values: dict[str, str]) -> Path:
    """Mete o reemplaza estas claves en el .env, dejando el resto intacto."""
    env = ROOT / ".env"
    if not env.exists():
        env.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    pending = dict(values)
    out: list[str] = []
    for line in env.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        key = line.split("=", 1)[0].strip() if "=" in line and not stripped.startswith("#") else None
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{key}={value}" for key, value in pending.items())

    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    return env


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

    block = config.get("installed") or config["web"]

    path = write_env(
        {
            "GOOGLE_CLIENT_ID": block["client_id"],
            "GOOGLE_CLIENT_SECRET": block["client_secret"],
            "GOOGLE_REFRESH_TOKEN": creds.refresh_token,
        }
    )

    # A proposito no imprimimos los valores: quedarian en la consola y en
    # cualquier log. Van directo al .env, que el .gitignore ya excluye.
    print(f"\nListo. Escribi 3 variables en {path.name}:")
    print("  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN")
    print(f"\nPermisos concedidos: {', '.join(creds.scopes or SCOPES)}")
    print(
        "\nOJO: si en Google Cloud Console la app quedo en estado 'Testing', este\n"
        "token se vence en 7 dias. Pasala a 'In production' para que no caduque.\n"
    )


if __name__ == "__main__":
    main()
