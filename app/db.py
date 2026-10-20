"""Motor de base de datos y sesiones."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

def _normalizar(url: str) -> str:
    """Deja la URL en la forma que espera SQLAlchemy con el driver psycopg.

    Neon y la mayoria de los hostings entregan la cadena como `postgresql://`
    (o `postgres://`, al estilo viejo de Heroku), que SQLAlchemy intentaria
    abrir con psycopg2. Nosotros instalamos psycopg 3, asi que lo decimos.
    """
    for viejo in ("postgresql://", "postgres://"):
        if url.startswith(viejo):
            return "postgresql+psycopg://" + url[len(viejo):]
    return url


url = _normalizar(settings.database_url)
es_sqlite = url.startswith("sqlite")

# SQLite: asegurar que exista la carpeta del archivo.
if es_sqlite:
    path = url.split("///", 1)[-1]
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)

if es_sqlite:
    connect_args: dict = {"check_same_thread": False}
else:
    # Neon entrega un endpoint "-pooler" (PgBouncer en modo transaccion).
    # psycopg empieza a preparar sentencias solas despues de unas pocas
    # ejecuciones y ahi el pooler devuelve "prepared statement already
    # exists" de forma intermitente. Apagarlo cuesta nada a esta escala.
    connect_args = {"prepare_threshold": None}

engine = create_engine(
    url,
    connect_args=connect_args,
    # Neon cierra las conexiones ociosas, asi que las revisamos antes de usar
    # y las reciclamos seguido en vez de arrastrar una muerta.
    pool_pre_ping=True,
    pool_recycle=300 if not es_sqlite else -1,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Abre una sesion, hace commit al salir bien y rollback si hay error."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    from app import models  # noqa: F401  (registra las tablas)

    models.Base.metadata.create_all(engine)
