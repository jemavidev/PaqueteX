# -*- coding: utf-8 -*-
"""
Seam de navegador real (Chromium + Playwright) — `.scratch/captura-guia-lector-camara`, ticket 02.

Lo que solo existe en un navegador (el Enter que envía un formulario, el foco, el ciclo de vida de la
cámara) no se puede probar con `TestClient`. Este seam levanta la app REAL sobre el mismo Postgres
efímero de la suite web (esquema por `alembic upgrade head`), abre Chromium de verdad y usa la BD
como fuente de verdad de qué se envió: teclado y clics reales (eventos de confianza, no despachados
a mano -- el envío implícito con Enter solo ocurre con esos).

CÓMO CORRERLO (desde `CODE/`):

    .venv/bin/python -m pytest -m browser

- Requisitos: `pip install playwright` y `playwright install chromium`, más Docker (Postgres
  efímero) o `TEST_DATABASE_URL` apuntando a un Postgres DESECHABLE (nunca el de desarrollo: cada
  prueba trunca las tablas al terminar).
- Un `pytest` sin marcador NO corre estas pruebas (`addopts` de `pytest.ini` deselecciona `browser`):
  la suite por defecto no cambia ni en tiempo ni en dependencias.
- Si Playwright o Chromium no están instalados, las pruebas se SALTAN con el mensaje de qué falta.
- No toca el servidor de desarrollo (`localhost:8010`) ni su BD: la app corre en un puerto libre.

Todas las pruebas de esta carpeta reciben el marcador `browser` automáticamente.
"""

import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import _harness as H

_AQUI = Path(__file__).resolve().parent
_WEB_CONFTEST = _AQUI.parent / "web" / "conftest.py"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Marca `browser` a todo lo de esta carpeta (antes de que el filtro `-m` decida)."""
    for item in items:
        if _AQUI in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.browser)


def _tablas_a_truncar():
    """Las mismas tablas que trunca el fixture `client` de la suite web (una sola fuente de verdad)."""
    spec = importlib.util.spec_from_file_location("_web_conftest_referencia", _WEB_CONFTEST)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo._TABLAS


@pytest.fixture(scope="session")
def chromium():
    """Un Chromium headless por sesión de pytest; se salta si falta Playwright o el navegador."""
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="Falta Playwright: pip install playwright"
    )
    gestor = sync_api.sync_playwright().start()
    try:
        navegador = gestor.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo de arranque = no hay Chromium usable
        gestor.stop()
        pytest.skip(
            f"No se pudo abrir Chromium ({type(exc).__name__}): corre `playwright install chromium`."
        )
    yield navegador
    navegador.close()
    gestor.stop()


@pytest.fixture()
def app_viva(migrated_db_url):
    """La app real, sirviendo en un puerto libre sobre el Postgres efímero migrado.

    Mismo cableado y mismo aislamiento que el fixture `client` de la suite web (sesión de BD atada
    al Postgres de la prueba, tablas truncadas al terminar), pero detrás de un servidor HTTP de
    verdad. Devuelve `.url` (base) y `.db` (sesión inspectora para sembrar y verificar).
    """
    uvicorn = pytest.importorskip("uvicorn")
    from app.domain.configuracion_conjunto_service import NOMBRE_CONJUNTO_POR_DEFECTO
    from app.web.app import create_app
    from app.web.db import get_db, get_session_factory

    tablas = _tablas_a_truncar()
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
    app.dependency_overrides[get_session_factory] = lambda: Session

    puerto = H.free_port()
    servidor = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning")
    )
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    limite = time.time() + 15
    while not servidor.started:
        if time.time() > limite or not hilo.is_alive():
            raise RuntimeError("El servidor de la prueba no arrancó en 15 s")
        time.sleep(0.05)

    inspector = Session()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{puerto}", db=inspector)
    finally:
        servidor.should_exit = True
        hilo.join(timeout=10)
        inspector.close()
        app.dependency_overrides.clear()
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tablas} RESTART IDENTITY CASCADE"))
            conn.execute(
                text("UPDATE apartamentos SET conjunto = :nombre"),
                {"nombre": NOMBRE_CONJUNTO_POR_DEFECTO},
            )
        engine.dispose()


@pytest.fixture()
def pagina(chromium):
    """Una página en un contexto de navegador limpio (cookies propias) por prueba."""
    contexto = chromium.new_context(
        viewport={"width": 1280, "height": 900},
        locale="es-CO",
        timezone_id="America/Bogota",
    )
    pag = contexto.new_page()
    pag.set_default_timeout(10_000)
    yield pag
    contexto.close()


@pytest.fixture()
def camara(pagina):
    """La cámara simulada (`_camara.py`) ya instalada en `pagina`: por defecto entrega video sin código."""
    from _camara import CamaraSimulada

    return CamaraSimulada(pagina).instalar()
