"""Prueba el servidor sin credenciales reales: lifespan, health y webhook."""
import os, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

os.environ.update({
    "ANTHROPIC_API_KEY": "sk-ant-fake",
    "TELEGRAM_BOT_TOKEN": "123:fake",
    "TELEGRAM_WEBHOOK_SECRET": "secreto-de-prueba",
    "DATABASE_URL": "sqlite:///./data/smoke-test.db",
    "PUBLIC_URL": "",
})

from fastapi.testclient import TestClient
from app import handlers
from app.main import app

recibidos = []
handlers.handle_update = lambda update: recibidos.append(update)

with TestClient(app) as c:
    r = c.get("/health")
    print("1. /health ->", r.status_code, r.json())
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "poll_emails" in r.json()["jobs"] and "send_reminders" in r.json()["jobs"]

    r = c.post("/telegram/webhook", json={"message": {"text": "hola"}},
               headers={"X-Telegram-Bot-Api-Secret-Token": "mal"})
    print("2. webhook con secreto incorrecto ->", r.status_code)
    assert r.status_code == 403

    r = c.post("/telegram/webhook", json={"message": {"text": "hola"}})
    print("3. webhook sin secreto ->", r.status_code)
    assert r.status_code == 403

    update = {"message": {"chat": {"id": 42}, "text": "/start"}}
    r = c.post("/telegram/webhook", json=update,
               headers={"X-Telegram-Bot-Api-Secret-Token": "secreto-de-prueba"})
    print("4. webhook con secreto correcto ->", r.status_code, r.json())
    assert r.status_code == 200

    r = c.post("/admin/poll", headers={"X-Admin-Secret": "mal"})
    print("5. /admin/poll con secreto incorrecto ->", r.status_code)
    assert r.status_code == 403

print("6. updates que llegaron al handler:", recibidos)
assert recibidos == [update]

# La base se creo y tiene el tema por defecto
from app.db import session_scope
from app.store import active_topics
with session_scope() as s:
    temas = active_topics(s)
    print("7. temas activos:", [(t.slug, len(t.keywords)) for t in temas])
    assert any(t.slug == "work-and-travel" for t in temas)

print("\nSMOKE TEST OK")
