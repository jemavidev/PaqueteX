# -*- coding: utf-8 -*-
"""
Conector real de LIWA (Grupo 8) — probado con la API SIMULADA vía
`monkeypatch` sobre `httpx.post` (no depende de red real ni de credenciales
verdaderas en la suite automatizada; la verificación contra la API real de
LIWA se hizo una vez, manualmente, antes de dar esto por bueno).
"""

import httpx
import pytest

from app.domain import liwa_sender as mod
from app.domain.liwa_sender import LiwaNotificationSender, LiwaOtpSender


class _RespuestaFalsa:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


@pytest.fixture(autouse=True)
def _credenciales(monkeypatch):
    monkeypatch.setenv("LIWA_API_KEY", "fake-key")
    monkeypatch.setenv("LIWA_ACCOUNT", "fake-account")
    monkeypatch.setenv("LIWA_PASSWORD", "fake-password")
    mod._token_cache.clear()
    yield
    mod._token_cache.clear()


def _fake_post_ok(llamadas):
    def _post(url, json=None, headers=None, timeout=None):
        llamadas.append((url, json, headers))
        if url == mod._AUTH_URL_DEFAULT:
            return _RespuestaFalsa(200, {"token": "tok-123"})
        return _RespuestaFalsa(200, {"success": True, "menssageId": "m1"})
    return _post


def test_enviar_sms_autentica_y_envia_con_el_payload_correcto(monkeypatch):
    llamadas = []
    monkeypatch.setattr(mod.httpx, "post", _fake_post_ok(llamadas))

    mod._enviar_sms("+573001234567", "Hola")

    assert len(llamadas) == 2
    auth_url, auth_payload, _ = llamadas[0]
    assert auth_url == mod._AUTH_URL_DEFAULT
    assert auth_payload == {"account": "fake-account", "password": "fake-password"}

    sms_url, sms_payload, sms_headers = llamadas[1]
    assert sms_url == mod._SMS_URL
    assert sms_payload == {"number": "573001234567", "message": "Hola", "type": 1}
    assert sms_headers["Authorization"] == "Bearer tok-123"
    assert sms_headers["API-KEY"] == "fake-key"


def test_token_se_cachea_entre_envios(monkeypatch):
    llamadas = []
    monkeypatch.setattr(mod.httpx, "post", _fake_post_ok(llamadas))

    mod._enviar_sms("+573001234567", "Uno")
    mod._enviar_sms("+573001234567", "Dos")

    llamadas_auth = [c for c in llamadas if c[0] == mod._AUTH_URL_DEFAULT]
    assert len(llamadas_auth) == 1  # solo autenticó una vez


def test_reintenta_autenticacion_si_recibe_401(monkeypatch):
    llamadas = []

    def _post(url, json=None, headers=None, timeout=None):
        llamadas.append((url, json, headers))
        if url == mod._AUTH_URL_DEFAULT:
            return _RespuestaFalsa(200, {"token": f"tok-{len(llamadas)}"})
        # Primer intento de envío falla con 401; el segundo (tras reautenticar) ok.
        intentos_envio = sum(1 for c in llamadas if c[0] == mod._SMS_URL)
        if intentos_envio == 1:
            return _RespuestaFalsa(401, {})
        return _RespuestaFalsa(200, {"success": True})

    monkeypatch.setattr(mod.httpx, "post", _post)

    mod._enviar_sms("+573001234567", "Hola")

    llamadas_auth = [c for c in llamadas if c[0] == mod._AUTH_URL_DEFAULT]
    assert len(llamadas_auth) == 2  # reautenticó tras el 401


def test_falla_si_liwa_responde_success_false(monkeypatch):
    def _post(url, json=None, headers=None, timeout=None):
        if url == mod._AUTH_URL_DEFAULT:
            return _RespuestaFalsa(200, {"token": "tok"})
        return _RespuestaFalsa(200, {"success": False, "message": "saldo insuficiente"})

    monkeypatch.setattr(mod.httpx, "post", _post)

    with pytest.raises(RuntimeError, match="saldo insuficiente"):
        mod._enviar_sms("+573001234567", "Hola")


def test_falla_sin_credenciales(monkeypatch):
    monkeypatch.delenv("LIWA_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        mod._enviar_sms("+573001234567", "Hola")


def test_liwa_notification_sender_delega_a_enviar_sms(monkeypatch):
    llamadas = []
    monkeypatch.setattr(mod.httpx, "post", _fake_post_ok(llamadas))

    LiwaNotificationSender().enviar("+573001234567", "Tu paquete llegó")

    _, payload, _ = llamadas[1]
    assert payload["message"] == "Tu paquete llegó"


def test_liwa_otp_sender_arma_el_mensaje_con_el_codigo(monkeypatch):
    llamadas = []
    monkeypatch.setattr(mod.httpx, "post", _fake_post_ok(llamadas))

    LiwaOtpSender().enviar("+573001234567", "42")

    _, payload, _ = llamadas[1]
    assert "42" in payload["message"]
