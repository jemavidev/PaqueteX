"""Issues 428 y 430 (.scratch/pendientes-cliente): lo que la app exige y esconde al arrancar fuera de desarrollo."""

import pytest
from fastapi.testclient import TestClient

from app.web.app import create_app


def _produccion(monkeypatch, entorno="production"):
    monkeypatch.setenv("WEB_ENV", entorno)
    monkeypatch.setenv("SECRET_KEY", "llave-de-prueba")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ejemplo.test")


@pytest.mark.parametrize("entorno", ["production", "staging"])
def test_fuera_de_desarrollo_no_se_publica_la_documentacion_de_la_api(monkeypatch, entorno):
    # Issue 428: FastAPI publicaba /docs, /redoc y /openapi.json -- el mapa completo de rutas, sin sesión.
    _produccion(monkeypatch, entorno)
    cliente = TestClient(create_app())

    for ruta in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(ruta).status_code == 404, ruta


def test_en_desarrollo_la_documentacion_de_la_api_sigue_disponible(monkeypatch):
    monkeypatch.delenv("WEB_ENV", raising=False)
    cliente = TestClient(create_app())

    assert cliente.get("/docs").status_code == 200


@pytest.mark.parametrize("entorno", ["production", "staging"])
def test_fuera_de_desarrollo_no_arranca_sin_public_base_url(monkeypatch, entorno):
    # Issue 430: la base de los enlaces de SMS/WhatsApp/correo -- sin ella un enlace saldría relativo e inservible.
    # Mejor que la app no arranque a que lo descubra un residente.
    _produccion(monkeypatch, entorno)
    monkeypatch.delenv("PUBLIC_BASE_URL")

    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        create_app()
