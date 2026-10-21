"""Prueba el flujo mail -> propuesta -> calendario, con las partes externas stubbeadas."""
import os, sys, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.update({
    "ANTHROPIC_API_KEY": "sk-ant-fake",
    "TELEGRAM_BOT_TOKEN": "123:fake",
    "TELEGRAM_WEBHOOK_SECRET": "s",
    "DATABASE_URL": "sqlite:///./data/flow-test.db",
    # Sin esto el test solo pasa despues de las 9: send_reminders no manda
    # nada antes de esa hora, y entonces fallaba segun cuando se corriera.
    "DAILY_DIGEST_HOUR": "0",
})
for f in (ROOT/"data"/"flow-test.db",):
    f.unlink(missing_ok=True)

from app.db import init_db, session_scope
from app.store import seed_default_topic
init_db()
with session_scope() as s: seed_default_topic(s)

from app import pipeline, telegram, gmail_client, calendar_client, llm
from app.models import Proposal, SeenMessage

# ---- 1. El prefiltro de Gmail ----
q = gmail_client.build_query(["work and travel", "DS-160"], ["noreply@ciee.org"], 1760000000)
print("1. query:", q)
assert '"work and travel"' in q and "from:noreply@ciee.org" in q and "-in:trash" in q

# ---- 2. El candado de solo-lectura ----
from app.google_auth import assert_gmail_is_read_only, GMAIL_SCOPE, CALENDAR_SCOPE
class FakeCreds:
    def __init__(self, scopes): self.scopes = scopes
assert_gmail_is_read_only(FakeCreds([GMAIL_SCOPE, CALENDAR_SCOPE]))
print("2a. token de solo lectura: aceptado")
try:
    assert_gmail_is_read_only(FakeCreds(["https://www.googleapis.com/auth/gmail.modify"]))
    raise AssertionError("deberia haber cortado")
except RuntimeError as e:
    print("2b. token con permiso de escritura: RECHAZADO ->", str(e)[:60], "...")

# ---- 3. Stubs de lo externo ----
enviados = []
telegram.send = lambda text, keyboard=None, chat_id=None: (enviados.append((text, keyboard)), 999)[1]
telegram.edit = lambda mid, text, chat_id=None: enviados.append(("EDIT", text))

hoy = dt.datetime.now(pipeline.settings.tz).date()
vence = hoy + dt.timedelta(days=12)

email = gmail_client.Email(
    gmail_id="msg-1", thread_id="th-1",
    subject="Action required: DS-2019 fee",
    sender="CIEE <noreply@ciee.org>", to="yo@gmail.com",
    date=dt.datetime.now(dt.timezone.utc),
    body="You must pay the SEVIS fee before " + vence.strftime("%B %d, %Y"),
    auth_results="mx.google.com; spf=pass smtp.mailfrom=ciee.org; dkim=pass header.i=@ciee.org; dmarc=pass header.from=ciee.org",
)
gmail_client.list_message_ids = lambda query, limit=60: ["msg-1"]
gmail_client.get_email = lambda gid: email

analysis = llm.EmailAnalysis(
    relevante=True, tema="work-and-travel",
    resumen="CIEE te pide pagar la tasa SEVIS antes de la fecha indicada.",
    requiere_accion=True, accion="Pagar la tasa SEVIS",
    fechas_limite=[llm.Deadline(
        titulo="Pagar la tasa SEVIS", fecha=vence.isoformat(), hora="",
        confianza="alta", cita="You must pay the SEVIS fee before ...")],
    urgencia="media",
)
import app.pipeline as P
P.analyze_email = lambda text, topics: analysis

# ---- 4. Revisar el mail ----
stats = pipeline.poll_emails()
print("3. stats:", stats)
assert (stats["candidatos"], stats["nuevos"], stats["relevantes"], stats["propuestas"], stats["fallidos"]) == (1, 1, 1, 1, 0), stats

with session_scope() as s:
    props = list(s.query(Proposal).all())
    assert len(props) == 1
    p = props[0]
    print("4. propuesta:", p.id, p.title, p.status, p.start_at.date(), "deadline:", p.is_deadline)
    assert p.status == "pending" and p.is_deadline and p.start_at.date() == vence
    pid = p.id
print("5. mensaje a Telegram:\n" + "-"*60 + "\n" + enviados[0][0] + "\n" + "-"*60)
print("   botones:", enviados[0][1])
assert f"ok:{pid}" in str(enviados[0][1]) and f"no:{pid}" in str(enviados[0][1])

# ---- 5. No procesa el mismo mail dos veces ----
antes = len(enviados)
stats2 = pipeline.poll_emails()
print("6. segunda pasada:", stats2, "| mensajes nuevos:", len(enviados) - antes)
assert stats2["nuevos"] == 0 and len(enviados) == antes

# ---- 6. Aprobar crea el evento ----
creados = []
calendar_client.create_event = lambda **kw: (creados.append(kw), calendar_client.CalEvent(
    event_id="ev-1", calendar_id="cal-1", summary=kw["summary"],
    start="x", end="y", all_day=kw["all_day"], html_link="http://x"))[1]
P.calendar_client.create_event = calendar_client.create_event

print("7. approve ->", pipeline.approve(pid))
assert creados and creados[0]["summary"] == "Pagar la tasa SEVIS"
assert creados[0]["is_deadline"] is True and creados[0]["calendar_key"] == "work_travel"
print("   evento creado con:", {k: v for k, v in creados[0].items() if k != "description"})
with session_scope() as s:
    p = s.get(Proposal, pid)
    assert p.status == "approved" and p.calendar_event_id == "ev-1"
print("8. estado final de la propuesta: approved, event_id =", "ev-1")

# ---- 7. Recordatorios ----
with session_scope() as s:
    s.get(Proposal, pid).start_at = dt.datetime.combine(
        hoy + dt.timedelta(days=3), dt.time(9, 0), tzinfo=pipeline.settings.tz)
n = pipeline.send_reminders()
print("9. recordatorios mandados:", n)
assert n == 1
print("   texto:", enviados[-1][0].replace("\n", " | ")[:100])
assert pipeline.send_reminders() == 0, "no debe repetir el mismo recordatorio"
print("10. no repite el recordatorio: OK")

# ---- 8. Evento semanal desde el chat ----
ev = llm.ChatEvent(
    titulo="Clase de ingles", calendario="clases",
    fecha_inicio=(hoy + dt.timedelta(days=1)).isoformat(),
    hora_inicio="18:00", hora_fin="20:00", todo_el_dia=False,
    dias_semana=["MO", "WE"], repetir_hasta="2026-12-20",
)
new_id = pipeline.proposal_from_chat(ev)
with session_scope() as s:
    p = s.get(Proposal, new_id)
    print("11. evento de chat:", p.title, "| rrule:", p.recurrence, "| cal:", p.calendar_key)
    assert p.recurrence == "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261221T025900Z"
    assert p.calendar_key == "clases" and p.all_day is False
print("    preview:\n" + enviados[-1][0])

print("\nFLOW TEST OK")
