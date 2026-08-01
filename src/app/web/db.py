# -*- coding: utf-8 -*-
"""
Motor y dependencia de sesión de la capa web (clean-room, ADR-0004).

El engine es PEREZOSO: no se conecta al importar, de modo que el app arranca sin
BD (y sin AWS). La dependencia `get_db` entrega una `Session` por request con
**commit al éxito / rollback al error**. No reutiliza el `get_db` viejo (atado al
`config` con AWS).
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import database_url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autoflush=False)


def get_db() -> Iterator[Session]:
    """Dependencia FastAPI: una `Session` por request (commit/rollback/close)."""
    db = _session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_session_factory() -> sessionmaker:
    """Dependencia FastAPI: la fábrica de sesiones cacheada, para pasar a un
    `BackgroundTask` que necesite abrir su PROPIA sesión de BD.

    Un `BackgroundTask` nunca debe reusar la `Session` del request (`get_db`)
    — FastAPI no garantiza que su código de cierre (`db.commit()`/`db.close()`)
    corra antes o después de los `BackgroundTasks`, así que para cuando el
    task se ejecute esa sesión puede estar cerrada, o el commit del request
    puede no haber ocurrido todavía. Pasar la fábrica (no una sesión) deja que
    el propio task abra, comitee y cierre la suya de punta a punta."""
    return _session_factory()
