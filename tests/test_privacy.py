"""Verifica que el cuerpo de un remitente privado nunca llegue al modelo."""
import os, sys, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.update({"GEMINI_API_KEY": "x", "TELEGRAM_BOT_TOKEN": "x",
                   "DATABASE_URL": "sqlite:///./data/privacy-test.db"})
(ROOT / "data" / "privacy-test.db").unlink(missing_ok=True)

from app.db import init_db, session_scope
from app.store import seed_default_topic
init_db()
with session_scope() as s: seed_default_topic(s)

from app import pipeline, telegram, gmail_client, privacy
import app.pipeline as P
from app.models import SeenMessage

SECRETO = "Tu legajo es 4471829301 y el CBU para el sueldo es 0170099220000012345678."

print("1. modos por remitente")
assert privacy.mode_for("ADP <donotreply@adp.com>") == privacy.NOTIFY_ONLY
assert privacy.mode_for("Erin <Erin.Anderson@westgateresorts.com>") == privacy.FULL
print("   adp.com ->", privacy.mode_for("x@adp.com"))
print("   westgateresorts.com ->", privacy.mode_for("x@westgateresorts.com"))

enviados = []
telegram.send = lambda text, keyboard=None, chat_id=None: (enviados.append(text), 1)[1]

# Lo que de verdad importa: registrar TODO lo que se le pasa al modelo.
visto_por_el_modelo = []
def fake_analyze(texto, topics):
    visto_por_el_modelo.append(texto)
    raise AssertionError("no deberia llamarse para un remitente privado")
P.analyze_email = fake_analyze

email = gmail_client.Email(
    gmail_id="adp-1", thread_id="th-adp",
    subject="Welcome Lucas Garcia Garcia", sender="<donotreply@adp.com>",
    to="yo@gmail.com", date=dt.datetime.now(dt.timezone.utc), body=SECRETO,
    auth_results="mx.google.com; spf=pass smtp.mailfrom=ciee.org; dkim=pass header.i=@ciee.org; dmarc=pass header.from=ciee.org",
)
gmail_client.list_message_ids = lambda query, limit=60: ["adp-1"]
gmail_client.get_email = lambda gid: email

print("2. revisar el mail de ADP")
stats = pipeline.poll_emails()
print("   stats:", stats)
assert stats["privados"] == 1 and stats["propuestas"] == 0

print("3. el modelo no recibio NADA")
assert visto_por_el_modelo == [], visto_por_el_modelo

print("4. que se mando a Telegram:")
print("-" * 60); print(enviados[0]); print("-" * 60)
assert "4471829301" not in enviados[0], "se filtro el legajo"
assert "0170099220000012345678" not in enviados[0], "se filtro el CBU"
assert "Welcome Lucas Garcia Garcia" in enviados[0]

print("5. el cuerpo tampoco quedo guardado en la base")
with session_scope() as s:
    row = s.get(SeenMessage, "adp-1")
    guardado = f"{row.summary} {row.analysis}"
    assert "4471829301" not in guardado and "0170099220000012345678" not in guardado
    print("   summary:", row.summary, "| analysis:", row.analysis)

print("6. no lo vuelve a notificar")
antes = len(enviados)
pipeline.poll_emails()
assert len(enviados) == antes
print("   OK")

print("\nPRIVACY TEST OK")
