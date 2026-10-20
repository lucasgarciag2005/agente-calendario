"""Un mail que pide plata y no pasa la verificacion no se analiza ni se agenda."""
import os, sys, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.update({"GEMINI_API_KEY": "x", "TELEGRAM_BOT_TOKEN": "x",
                   "DATABASE_URL": "sqlite:///./data/sec-test.db"})
(ROOT / "data" / "sec-test.db").unlink(missing_ok=True)

from app.db import init_db, session_scope
from app.store import seed_default_topic
init_db()
with session_scope() as s: seed_default_topic(s)

from app import pipeline, telegram, gmail_client, security
import app.pipeline as P

AUTH_OK = "mx.google.com; spf=pass smtp.mailfrom=gozego.com; dkim=pass header.i=@gozego.com; dmarc=pass header.from=gozego.com"
AUTH_MAL = "mx.google.com; spf=fail smtp.mailfrom=raro.ru; dkim=none; dmarc=fail header.from=gozego.com"

print("1. el veredicto por separado")
v = security.evaluar(AUTH_OK, "Make your payment online", "pay your rent")
print(f"   autenticado + plata -> estado={v.estado} alerta={v.alerta}")
assert v.estado == security.OK and not v.alerta

v = security.evaluar(AUTH_MAL, "Make your payment online", "pay your rent now")
print(f"   falsificado + plata -> estado={v.estado} alerta={v.alerta}")
assert v.estado == security.SOSPECHOSO and v.alerta

v = security.evaluar(AUTH_MAL, "Westgate housing update", "tu habitacion es la D304")
print(f"   falsificado sin plata -> estado={v.estado} alerta={v.alerta}")
assert v.estado == security.SOSPECHOSO and not v.alerta, "no alertar si no hay plata"

print("\n2. el flujo completo con un mail falsificado")
enviados = []
telegram.send = lambda text, keyboard=None, chat_id=None: (enviados.append(text), 1)[1]
visto_por_el_modelo = []
def fake(texto, topics):
    visto_por_el_modelo.append(texto)
    raise AssertionError("no deberia analizarse un mail sospechoso")
P.analyze_email = fake

email = gmail_client.Email(
    gmail_id="scam-1", thread_id="th-scam",
    subject="URGENTE: pago del alquiler de Slopeside Village",
    sender="Zego Billing <billing@gozego.com>", to="yo@gmail.com",
    date=dt.datetime.now(dt.timezone.utc),
    body="Transferi USD 1200 al CBU 0170099220000012345678 antes del viernes.",
    auth_results=AUTH_MAL,
)
gmail_client.list_message_ids = lambda query, limit=60: ["scam-1"]
gmail_client.get_email = lambda gid: email

stats = pipeline.poll_emails()
print("   stats:", {k: v for k, v in stats.items() if v})
assert stats["sospechosos"] == 1 and stats["propuestas"] == 0
assert visto_por_el_modelo == [], "no debe llegar al modelo"

print("\n3. la alerta que te llega:")
print("-" * 64); print(enviados[0]); print("-" * 64)
assert "CUIDADO" in enviados[0]
assert "0170099220000012345678" not in enviados[0], "no repetir el CBU falso"

print("4. no se agendo nada")
from app.models import Proposal
with session_scope() as s:
    assert s.query(Proposal).count() == 0
print("   OK")

print("\nSECURITY TEST OK")

# --- un mail sin header de autenticacion no debe alertar ---
v = security.evaluar("", "pago del alquiler", "transferi 1200")
print("\n5. sin header de autenticacion ->", v.estado, "| alerta:", v.alerta)
assert v.estado == security.DESCONOCIDO and not v.alerta
print("   OK: no alerta, porque Gmail estampa ese header del lado del servidor")
