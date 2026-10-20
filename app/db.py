"""Motor de base de datos y sesiones."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

url = settings.database_url

# SQLite: asegurar que exista la carpeta del archivo.
if url.startswith("sqlite"):
    path = url.split("///", 1)[-1]
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)

engine = create_engine(
    url,
    connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    pool_pre_ping=True,
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
