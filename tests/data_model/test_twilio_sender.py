# -*- coding: utf-8 -*-
"""
Conector real de Twilio (ticket 02) — probado con `httpx.post` reemplazado
por un doble de prueba vía `monkeypatch` (mismo patrón que
`test_liwa_sender.py`; no depende de red real ni de credenciales verdaderas).
"""

import httpx
import pytest

from app.domain import twilio_sender as mod
from app.domain.sms_failover import ErrorConectividadSms
from app.domain.twilio_sender import TwilioNotificationSender, TwilioOtpSender


class _RespuestaFalsa:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.twilio.com/fake")
            raise httpx.HTTPStatusError("error", request=request, response=self)


@pytest.fixture(autouse=True)
def _credenciales(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")


def test_enviar_sms_llama_con_basic_auth_y_payload_correcto(monkeypatch):
    llamadas = []

    def _post(url, data=None, auth=None, timeout=None):
        llamadas.append((url, data, auth))
        return _RespuestaFalsa(201)

    monkeypatch.setattr(mod.httpx, "post", _post)

    mod._enviar_sms("+573001234567", "Hola")

    assert len(llamadas) == 1
    url, payload, auth = llamadas[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/ACfake/Messages.json"
    assert payload == {"To": "+573001234567", "MessagingServiceSid": "MGfake", "Body": "Hola"}
    assert auth == ("ACfake", "fake-token")


def test_falla_sin_credenciales(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    with pytest.raises(RuntimeError):
        mod._enviar_sms("+573001234567", "Hola")


def test_timeout_de_conexion_se_traduce_a_error_de_conectividad_reintentable(monkeypatch):
    def _post_que_no_conecta(url, data=None, auth=None, timeout=None):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(mod.httpx, "post", _post_que_no_conecta)

    with pytest.raises(ErrorConectividadSms):
        mod._enviar_sms("+573001234567", "Hola")


def test_status_5xx_se_traduce_a_error_de_conectividad_reintentable(monkeypatch):
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **kw: _RespuestaFalsa(503))

    with pytest.raises(ErrorConectividadSms):
        mod._enviar_sms("+573001234567", "Hola")


def test_status_400_es_rechazo_no_reintentable(monkeypatch):
    monkeypatch.setattr(
        mod.httpx, "post", lambda *a, **kw: _RespuestaFalsa(400, text="número inválido")
    )

    with pytest.raises(RuntimeError, match="número inválido"):
        mod._enviar_sms("+573001234567", "Hola")


def test_twilio_notification_sender_delega_a_enviar_sms(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        mod.httpx,
        "post",
        lambda url, data=None, auth=None, timeout=None: (llamadas.append(data), _RespuestaFalsa(201))[1],
    )

    TwilioNotificationSender().enviar("+573001234567", "Tu paquete llegó")

    assert llamadas[0]["Body"] == "Tu paquete llegó"


def test_twilio_otp_sender_arma_el_mensaje_con_el_codigo(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        mod.httpx,
        "post",
        lambda url, data=None, auth=None, timeout=None: (llamadas.append(data), _RespuestaFalsa(201))[1],
    )

    TwilioOtpSender().enviar("+573001234567", "42")

    assert "42" in llamadas[0]["Body"]
