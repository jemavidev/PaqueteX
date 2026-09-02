# -*- coding: utf-8 -*-
"""
Fixtures de la capa web (rebuild PaqueteXv.2).

Reutiliza el arnés compartido (`tests/conftest.py` → Postgres efímero construido
con `alembic upgrade head`). Expone un `client` (`TestClient` sobre el app nuevo,
con la dependencia de sesión atada a la BD migrada) que además lleva una sesión
inspectora `client.db` para verificar el estado de la BD tras un request.

Aislamiento entre tests: las rutas **commitean de verdad** contra el Postgres
efímero, y el fixture **trunca** las tablas del rebuild al terminar cada test.

`apartamentos` NO está en esa lista a propósito (`.scratch/apartamento-
catalogo-confirmacion`, ticket 03): con el catálogo cerrado, sus 804 filas
las siembra la migración UNA sola vez por sesión de test
(`migrated_db_url`, session-scoped) -- truncarla dejaría el catálogo vacío
para siempre después del primer test web que corra. `ocupantes` (que
referencia `apartamentos`) igual queda aislado entre tests vía el CASCADE de
truncar `personas`, sin necesitar que `apartamentos` esté en la lista.

`configuracion_conjunto` SÍ se trunca (vuelve sola al default vía el patrón
de override, ver `configuracion_conjunto_service`) -- pero como
`Apartamento.conjunto` queda sincronizado con lo que sea que un test haya
renombrado (`renombrar_conjunto` propaga en bloque), hace falta resetearlo
de vuelta al default en el mismo teardown, o quedaría desincronizado con la
lectura por defecto que ve el siguiente test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domain.configuracion_conjunto_service import NOMBRE_CONJUNTO_POR_DEFECTO
from app.web.app import create_app
from app.web.db import get_db, get_session_factory

_TABLAS = (
    "paquetes, usuarios, personas, plantillas_notificacion, "
    "otps_cliente, configuracion_conjunto, "
    "proveedores_notificacion_config, proveedores_notificacion_config_historial"
)


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
    # BackgroundTasks que abren su propia sesión (ej. subir_fotos_diferido)
    # deben usar la MISMA BD efímera, no `database_url()` (sin configurar
    # en tests) -- ver `db.get_session_factory`.
    app.dependency_overrides[get_session_factory] = lambda: Session
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
            # `configuracion_conjunto` ya quedó vacía (arriba) -- sincroniza
            # `Apartamento.conjunto` de vuelta al mismo default que el
            # override devolverá para el próximo test, sin tocar las 804
            # filas en sí (no se truncan, ver docstring del módulo).
            conn.execute(
                text("UPDATE apartamentos SET conjunto = :nombre"),
                {"nombre": NOMBRE_CONJUNTO_POR_DEFECTO},
            )
        engine.dispose()
