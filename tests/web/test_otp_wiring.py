# -*- coding: utf-8 -*-
"""
`get_otp_sender` — selección de proveedor por entorno, cadena de failover
AWS SNS → LIWA → Twilio (orden por defecto, sembrado por la migración 0037 --
issue 02, `.scratch/administracion-proveedores/spec.md`; antes de esa feature
era una constante fija en código, y antes de 2026-08-06 el orden era LIWA →
Twilio → SNS). Sin HTTP -- usa `db_session` directo (el orden/habilitado de
cada proveedor ya vive en `ProveedorConfig`), mismo patrón que la sección
"selección por entorno" de `tests/web/test_notifications.py`.
"""

from app.domain.liwa_sender import LiwaOtpSender
from app.domain.otp_sender import DevOtpSender
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedor_config_service import guardar_habilitado_orden
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


def test_sin_credenciales_devuelve_dev_otp_sender(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    assert isinstance(get_otp_sender(db_session), DevOtpSender)


def test_solo_liwa_configurado_devuelve_liwa_directo(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    assert isinstance(get_otp_sender(db_session), LiwaOtpSender)


def test_solo_twilio_configurado_devuelve_twilio_directo(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    _twilio_completo(monkeypatch)
    assert isinstance(get_otp_sender(db_session), TwilioOtpSender)


def test_twilio_con_solo_account_sid_no_se_incluye_en_la_cadena(monkeypatch, db_session):
    """Regresión: un Twilio a medio configurar (falta AUTH_TOKEN/FROM_NUMBER)
    no debe entrar a la cadena — si entrara, su `RuntimeError` de config
    rompería el failover (lo trataría como rechazo no reintentable). Con
    LIWA completo + Twilio a medias + SNS habilitado, Twilio ni siquiera
    cuenta -- la cadena queda SNS → LIWA."""
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")  # solo esta, a propósito
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    sender = get_otp_sender(db_session)

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [SnsOtpSender, LiwaOtpSender]


def test_solo_sns_configurado_devuelve_sns_directo(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    assert isinstance(get_otp_sender(db_session), SnsOtpSender)


def test_credenciales_aws_de_s3_sin_la_bandera_no_activan_sns(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
    assert isinstance(get_otp_sender(db_session), DevOtpSender)


def test_liwa_y_twilio_configurados_devuelve_cadena_de_failover_en_orden(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)

    sender = get_otp_sender(db_session)

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [LiwaOtpSender, TwilioOtpSender]


def test_los_tres_configurados_devuelve_cadena_completa_en_orden(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    sender = get_otp_sender(db_session)

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [SnsOtpSender, LiwaOtpSender, TwilioOtpSender]


def test_orden_distinto_en_bd_cambia_el_orden_real(monkeypatch, db_session):
    # Issue 02, criterio explícito del ticket: sembrar un orden DISTINTO al
    # histórico (Twilio primero) y verificar que `get_otp_sender` lo respeta
    # -- prueba que de verdad lee la BD, no un fallback oculto a la
    # constante vieja.
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=True, orden=1)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "AWS_SNS", habilitado=True, orden=2)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "LIWA", habilitado=True, orden=3)

    sender = get_otp_sender(db_session)

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [TwilioOtpSender, SnsOtpSender, LiwaOtpSender]


def test_deshabilitado_en_bd_excluye_aunque_este_configurado(monkeypatch, db_session):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)

    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "LIWA", habilitado=False, orden=1)

    sender = get_otp_sender(db_session)

    assert isinstance(sender, TwilioOtpSender)  # LIWA deshabilitado en BD, aunque esté configurado
