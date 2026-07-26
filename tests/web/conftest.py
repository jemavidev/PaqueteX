# -*- coding: utf-8 -*-
"""
Fixtures de la capa web (rebuild PaqueteXv.2).

Reutiliza el arnés compartido (`tests/conftest.py` → Postgres efímero construido
con `alembic upgrade head`). Expone un `client` (`TestClient` sobre el app nuevo,
con la dependencia de sesión atada a la BD migrada) que además lleva una sesión
inspectora `client.db` para verificar el estado de la BD tras un request.

Aislamiento entre tests: las rutas **commitean de verdad** contra el Postgres
efímero, y el fixture **trunca** las tablas del rebuild al terminar cada test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.web.app import create_app
from app.web.db import get_db

_TABLAS = "paquetes, usuarios, personas, apartamentos"


@pytest.fixture()
def client(migrated_db_url):
    app = create_app()
    engine = create_engine(migrated_db_url)
    Session = sessionmaker(bind=engine, autoflush=False)

    def _override_get_db():
        db = Session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    inspector = Session()  # para consultar la BD tras un request
    c = TestClient(app)
    c.db = inspector
    try:
        with c:
            yield c
    finally:
        inspector.close()
        app.dependency_overrides.clear()
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {_TABLAS} RESTART IDENTITY CASCADE"))
        engine.dispose()
