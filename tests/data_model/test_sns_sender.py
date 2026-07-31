# -*- coding: utf-8 -*-
"""
Conector real de AWS SNS (ticket 03) — probado con `boto3.client`
reemplazado por un doble de prueba vía `monkeypatch`, mismo patrón que
`test_s3_foto_storage.py`/`test_liwa_sender.py`. No depende de red real ni
de credenciales verdaderas.
"""

import botocore.exceptions
import pytest

from app.domain import sns_sender as mod
from app.domain.sms_failover import ErrorConectividadSms
from app.domain.sns_sender import SnsNotificationSender, SnsOtpSender


class _ClienteSnsFalso:
    def __init__(self, excepcion=None, message_id="msg-123"):
        self.llamadas = []
        self._excepcion = excepcion
        self._message_id = message_id

    def publish(self, **kwargs):
        if self._excepcion:
            raise self._excepcion
        self.llamadas.append(kwargs)
        return {"MessageId": self._message_id} if self._message_id else {}


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_publica_con_message_attributes_transactional(monkeypatch):
    cliente = _ClienteSnsFalso()
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    mod._enviar_sms("+573001234567", "Hola")

    assert len(cliente.llamadas) == 1
    llamada = cliente.llamadas[0]
    assert llamada["PhoneNumber"] == "+573001234567"
    assert llamada["Message"] == "Hola"
    assert llamada["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"


def test_sin_bandera_habilitada_lanza_runtimeerror(monkeypatch):
    monkeypatch.delenv("AWS_SNS_SMS_ENABLED", raising=False)
    with pytest.raises(RuntimeError):
        mod._enviar_sms("+573001234567", "Hola")


def test_credenciales_aws_de_s3_no_activan_sns_por_si_solas(monkeypatch):
    """Tener AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (p.ej. por S3) no basta:
    hace falta la bandera explícita."""
    monkeypatch.delenv("AWS_SNS_SMS_ENABLED", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
    with pytest.raises(RuntimeError):
        mod._enviar_sms("+573001234567", "Hola")


def test_error_de_conexion_se_traduce_a_error_de_conectividad_reintentable(monkeypatch):
    excepcion = botocore.exceptions.EndpointConnectionError(
        endpoint_url="https://sns.us-east-1.amazonaws.com"
    )
    cliente = _ClienteSnsFalso(excepcion=excepcion)
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    with pytest.raises(ErrorConectividadSms):
        mod._enviar_sms("+573001234567", "Hola")


def test_throttling_se_traduce_a_error_de_conectividad_reintentable(monkeypatch):
    excepcion = botocore.exceptions.ClientError(
        {"Error": {"Code": "Throttling", "Message": "demasiadas solicitudes"}}, "Publish"
    )
    cliente = _ClienteSnsFalso(excepcion=excepcion)
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    with pytest.raises(ErrorConectividadSms):
        mod._enviar_sms("+573001234567", "Hola")


def test_rechazo_explicito_no_es_reintentable(monkeypatch):
    excepcion = botocore.exceptions.ClientError(
        {"Error": {"Code": "InvalidParameterValue", "Message": "número inválido"}}, "Publish"
    )
    cliente = _ClienteSnsFalso(excepcion=excepcion)
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    with pytest.raises(RuntimeError, match="InvalidParameterValue"):
        mod._enviar_sms("+573001234567", "Hola")


def test_respuesta_sin_message_id_no_es_reintentable(monkeypatch):
    cliente = _ClienteSnsFalso(message_id=None)
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    with pytest.raises(RuntimeError, match="MessageId"):
        mod._enviar_sms("+573001234567", "Hola")


def test_sns_notification_sender_delega_a_enviar_sms(monkeypatch):
    cliente = _ClienteSnsFalso()
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    SnsNotificationSender().enviar("+573001234567", "Tu paquete llegó")

    assert cliente.llamadas[0]["Message"] == "Tu paquete llegó"


def test_sns_otp_sender_arma_el_mensaje_con_el_codigo(monkeypatch):
    cliente = _ClienteSnsFalso()
    monkeypatch.setattr(mod.boto3, "client", lambda *a, **kw: cliente)

    SnsOtpSender().enviar("+573001234567", "42")

    assert "42" in cliente.llamadas[0]["Message"]
