# -*- coding: utf-8 -*-
"""
Rate limiting en `/ingresar` y `/otp/solicitar` (ticket único).

Comportamiento observable por HTTP: por debajo del límite, ambas rutas funcionan
igual que antes; al excederlo, 429 con mensaje claro; y si el `RateLimiter`
configurado falla, la solicitud pasa igual (fail-open).
"""

from app.domain.staff_service import create_initial_admin
from app.web.rate_limit import get_rate_limiter

_PW = "Contrasena1"


def test_login_por_debajo_del_limite_funciona_normal(client):
    create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()

    r = client.post(
        "/ingresar", data={"email": "admin@club.com", "password": _PW}
    )
    assert r.status_code == 200  # siguió el redirect a /mi-sesion


def test_login_excede_el_limite_da_429(client):
    create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()

    # Límite es 10/60s; la 11ª solicitud debe rechazarse.
    for _ in range(10):
        r = client.post(
            "/ingresar", data={"email": "admin@club.com", "password": "mala1234"}
        )
        assert r.status_code == 400  # credenciales inválidas, pero permitido

    r = client.post(
        "/ingresar", data={"email": "admin@club.com", "password": "mala1234"}
    )
    assert r.status_code == 429
    assert "demasiados intentos" in r.text.lower()


def test_request_otp_excede_su_limite_mas_estricto_da_429(client):
    # Límite es 5/60s (más estricto que login).
    for _ in range(5):
        r = client.post("/otp/solicitar", data={"telefono": "3001234567"})
        assert r.status_code == 200

    r = client.post("/otp/solicitar", data={"telefono": "3001234567"})
    assert r.status_code == 429
    assert "demasiados intentos" in r.text.lower()


def test_rate_limiter_que_falla_no_bloquea_el_login_fail_open(client):
    class _LimiterQueFalla:
        def permitir(self, clave, limite, ventana_segundos):
            raise RuntimeError("backend de conteo caído")

    create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()

    client.app.dependency_overrides[get_rate_limiter] = lambda: _LimiterQueFalla()

    r = client.post(
        "/ingresar", data={"email": "admin@club.com", "password": _PW}
    )
    assert r.status_code == 200  # fail-open: la solicitud pasó igual
