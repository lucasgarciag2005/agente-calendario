"""Tocar varios botones a la vez no debe duplicar calendarios ni eventos."""
import os, sys, pathlib, threading, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.update({"GEMINI_API_KEY": "x", "TELEGRAM_BOT_TOKEN": "x",
                   "DATABASE_URL": "sqlite:///./data/conc-test.db"})
(ROOT / "data" / "conc-test.db").unlink(missing_ok=True)

from app.db import init_db, session_scope
from app.config import settings
from app.models import Proposal
from app import pipeline, telegram, calendar_client
init_db()
telegram.send = lambda *a, **k: 1
telegram.edit = lambda *a, **k: None

# Simulamos la API de Google: lenta al crear calendario, que es donde se cuela la carrera
calendarios_creados, eventos_creados = [], []
lock = threading.Lock()

def fake_crear(key, name, color, kv_key):
    import time
    from app.store import kv_set
    time.sleep(0.05)                      # la llamada real tarda
    with lock: calendarios_creados.append(name)
    cid = f"cal-{len(calendarios_creados)}"
    with session_scope() as s: kv_set(s, kv_key, cid)
    return cid

calendar_client._crear_calendario = fake_crear

def fake_evento(**kw):
    # Igual que el real: primero resuelve el calendario, y ahi esta la carrera.
    cid = calendar_client.ensure_calendar(kw["calendar_key"])
    with lock: eventos_creados.append(kw["summary"])
    return calendar_client.CalEvent(f"ev-{len(eventos_creados)}", cid,
                                    kw["summary"], "x", "y", kw["all_day"], "")
pipeline.calendar_client.create_event = fake_evento

hoy = dt.datetime.now(settings.tz)
with session_scope() as s:
    for i in range(8):
        s.add(Proposal(title=f"Materia {i}", calendar_key="clases", status="pending",
                       start_at=hoy + dt.timedelta(days=i), all_day=False))

with session_scope() as s:
    ids = [p.id for p in s.query(Proposal).all()]

hilos = [threading.Thread(target=pipeline.approve, args=(i,)) for i in ids]
for h in hilos: h.start()
for h in hilos: h.join()

print(f"  calendarios creados: {len(calendarios_creados)} (debe ser 1)")
print(f"  eventos creados    : {len(eventos_creados)} (debe ser 8)")
assert len(calendarios_creados) == 1, calendarios_creados
assert len(eventos_creados) == 8, eventos_creados

# Y ahora el doble toque sobre la MISMA propuesta
eventos_creados.clear()
with session_scope() as s:
    p = Proposal(title="Viaje", calendar_key="viajes", status="pending",
                 start_at=hoy, all_day=True)
    s.add(p); s.flush(); pid = p.id
hilos = [threading.Thread(target=pipeline.approve, args=(pid,)) for _ in range(6)]
for h in hilos: h.start()
for h in hilos: h.join()
print(f"  doble toque -> eventos: {len(eventos_creados)} (debe ser 1)")
assert len(eventos_creados) == 1, eventos_creados

print("\nCONCURRENCIA OK")
