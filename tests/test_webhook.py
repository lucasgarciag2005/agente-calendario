"""El webhook trabaja antes de contestar, y un reintento no duplica nada."""
import os, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.update({"GEMINI_API_KEY": "x", "TELEGRAM_BOT_TOKEN": "x",
                   "TELEGRAM_WEBHOOK_SECRET": "s",
                   "DATABASE_URL": "sqlite:///./data/wh-test.db"})
(ROOT / "data" / "wh-test.db").unlink(missing_ok=True)

from fastapi.testclient import TestClient
from app import handlers
from app.main import app

procesados = []
handlers.handle_update = lambda u: procesados.append(u["update_id"])
H = {"X-Telegram-Bot-Api-Secret-Token": "s"}

def upd(uid, texto):
    return {"update_id": uid, "message": {"chat": {"id": 1}, "text": texto}}

with TestClient(app) as c:
    r = c.post("/telegram/webhook", json=upd(500, "hola"), headers=H)
    print("1. primer envio ->", r.status_code, r.json())
    assert r.status_code == 200
    print("   procesado ANTES de contestar:", procesados == [500])
    assert procesados == [500], "el trabajo debe estar hecho al responder"

    r = c.post("/telegram/webhook", json=upd(500, "hola"), headers=H)
    print("2. reintento del mismo update ->", r.json())
    assert r.json().get("repetido") is True
    assert procesados == [500], "no debe procesarse dos veces"

    r = c.post("/telegram/webhook", json=upd(501, "otra cosa"), headers=H)
    print("3. update distinto ->", r.json())
    assert procesados == [500, 501]

    salud = c.get("/health").json()
    print("4. /health informa el ultimo error de envio:", salud["ultimo_error_envio"])
    assert "ultimo_error_envio" in salud

print("\nWEBHOOK TEST OK")

# --- un mensaje sin texto debe contestar, no callarse ---
import app.handlers as H
from app import telegram as T
enviados = []
T.send = lambda text, keyboard=None, chat_id=None: (enviados.append(text), 1)[1]
import importlib
importlib.reload(H)
H.telegram.send = T.send

H._handle_message({"chat": {"id": 1}, "voice": {"duration": 3}})
print("5. audio ->", enviados[-1][:60])
assert "audios" in enviados[-1]

H._handle_message({"chat": {"id": 1}, "photo": []})
print("6. foto  ->", enviados[-1][:60])
assert "fotos" in enviados[-1]

# una foto con epigrafe si se procesa como texto
leidos = []
H.parse_chat_message = lambda t, topics: (_ for _ in ()).throw(RuntimeError("stub"))
H._handle_message({"chat": {"id": 1}, "photo": [], "caption": "el 3 de noviembre tengo la entrevista"})
print("7. foto con epigrafe -> se intenta interpretar el texto")
assert "fotos" not in enviados[-1]

print("\nSIN-TEXTO OK")
