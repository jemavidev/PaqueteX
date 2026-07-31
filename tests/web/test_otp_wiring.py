# -*- coding: utf-8 -*-
"""
`get_otp_sender` — selección de proveedor por entorno, cadena de failover
LIWA → Twilio → SNS. Unidad, sin DB ni HTTP — mismo patrón que la sección
"selección por entorno" de `tests/web/test_notifications.py`.
"""

from app.domain.liwa_sender import LiwaOtpSender
from app.domain.otp_sender import DevOtpSender
from app.domain.sms_failover import FailoverSmsSender
from app.domain.sns_sender import SnsOtpSender
from app.domain.twilio_sender import TwilioOtpSender
from app.web.otp import get_otp_sender


def _sin_ningun_proveedor(monkeypatch):
    monkeypatch.delenv("LIWA_API_KEY", raising=False)
    monkeypatch.delenv("LIWA_ACCOUNT", raising=False)
    monkeypatch.delenv("LIWA_PASSWORD", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_MESSAGING_SERVICE_SID", raising=False)
    monkeypatch.delenv("AWS_SNS_SMS_ENABLED", raising=False)


def _liwa_completo(monkeypatch):
    monkeypatch.setenv("LIWA_API_KEY", "fake")
    monkeypatch.setenv("LIWA_ACCOUNT", "fake")
    monkeypatch.setenv("LIWA_PASSWORD", "fake")


def _twilio_completo(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")


def test_sin_credenciales_devuelve_dev_otp_sender(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    assert isinstance(get_otp_sender(), DevOtpSender)


def test_solo_liwa_configurado_devuelve_liwa_directo(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    assert isinstance(get_otp_sender(), LiwaOtpSender)


def test_solo_twilio_configurado_devuelve_twilio_directo(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _twilio_completo(monkeypatch)
    assert isinstance(get_otp_sender(), TwilioOtpSender)


def test_twilio_con_solo_account_sid_no_se_incluye_en_la_cadena(monkeypatch):
    """Regresión: un Twilio a medio configurar (falta AUTH_TOKEN/FROM_NUMBER)
    no debe entrar a la cadena — si entrara, su `RuntimeError` de config
    rompería el failover hacia SNS (lo trataría como rechazo no
    reintentable). Con LIWA completo + Twilio a medias + SNS habilitado, debe
    devolver LIWA directo, SIN envolver — Twilio ni siquiera cuenta."""
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")  # solo esta, a propósito
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    sender = get_otp_sender()

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [LiwaOtpSender, SnsOtpSender]


def test_solo_sns_configurado_devuelve_sns_directo(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    assert isinstance(get_otp_sender(), SnsOtpSender)


def test_credenciales_aws_de_s3_sin_la_bandera_no_activan_sns(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
    assert isinstance(get_otp_sender(), DevOtpSender)


def test_liwa_y_twilio_configurados_devuelve_cadena_de_failover_en_orden(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)

    sender = get_otp_sender()

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [LiwaOtpSender, TwilioOtpSender]


def test_los_tres_configurados_devuelve_cadena_completa_en_orden(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    sender = get_otp_sender()

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [LiwaOtpSender, TwilioOtpSender, SnsOtpSender]
