"""Verifica que una fecha sobreviva el viaje a la base sin corrimiento de dia."""
import os, sys, pathlib, datetime as dt
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.update({"ANTHROPIC_API_KEY":"x","TELEGRAM_BOT_TOKEN":"x",
                   "DATABASE_URL":"sqlite:///./data/tz-test.db"})
(ROOT/"data"/"tz-test.db").unlink(missing_ok=True)

from app.db import init_db, session_scope
from app.config import settings
from app.models import Proposal
from app import pipeline
init_db()

# 00:30 del 15 de diciembre, hora de Buenos Aires. En UTC son las 03:30 del
# mismo dia; si el huso se perdiera, al releerlo en un host UTC daria el 14.
original = dt.datetime(2026, 12, 15, 0, 30, tzinfo=settings.tz)
print("guardo      :", original, "->", original.astimezone(settings.tz).date())

with session_scope() as s:
    p = Proposal(title="prueba de huso", start_at=original, all_day=False,
                 is_deadline=True, status="approved")
    s.add(p); s.flush(); pid = p.id

with session_scope() as s:
    leido = s.get(Proposal, pid).start_at

print("leo de la db:", leido, "| tzinfo:", leido.tzinfo)
print("en ART      :", leido.astimezone(settings.tz), "->", leido.astimezone(settings.tz).date())
assert leido.tzinfo is not None, "volvio sin zona horaria"
assert leido == original, f"{leido} != {original}"
assert leido.astimezone(settings.tz).date() == dt.date(2026, 12, 15)
print("human_date  :", pipeline.human_date(leido, with_time=True))

# Simular un host en UTC: el resultado tiene que ser el mismo
os.environ["TZ"] = "UTC"
import time
if hasattr(time, "tzset"): time.tzset()
with session_scope() as s:
    leido2 = s.get(Proposal, pid).start_at
assert leido2.astimezone(settings.tz).date() == dt.date(2026, 12, 15)
print("con el sistema en UTC tambien da:", leido2.astimezone(settings.tz).date())

print("\nTZ TEST OK")
