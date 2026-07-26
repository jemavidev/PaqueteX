# -*- coding: utf-8 -*-
"""
Capa web — smoke del bootstrap clean-room (ticket 01).

Verifica que el app nuevo monta y responde, que arranca SIN credenciales AWS, y
que el arnés HTTP con BD efímera funciona de punta a punta.
"""

from fastapi.testclient import TestClient

from app.web.app import create_app


def test_health_responde_ok():
    # El app arranca y responde sin necesidad de BD ni AWS.
    with TestClient(create_app()) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_el_app_arranca_sin_variables_aws(monkeypatch):
    # Aunque no haya credenciales AWS en el entorno, el app se crea (ADR-0004).
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    app = create_app()
    assert app is not None


def test_health_a_traves_del_arnes_con_db_efimera(client):
    # Ejercita el arnés HTTP DB-backed (TestClient + Postgres efímero + override).
    r = client.get("/health")
    assert r.status_code == 200
