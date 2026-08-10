# -*- coding: utf-8 -*-
"""
Override fail-closed de staging + wiring en las rutas de `/paquetes` (ticket 02).

El test más importante de esta rebanada: en `WEB_ENV=staging` SIN
`SMS_OVERRIDE_NUMBER`, una transición real (recibir) no debe disparar NINGUNA
llamada al sender envuelto — el fail-closed es lo que protege a un residente
real de un SMS de una prueba de staging.
"""

import botocore.exceptions
import httpx
import pytest

from app.domain import liwa_sender
from app.domain.liwa_sender import LiwaNotificationSender
from app.domain.notification_sender import ConsoleNotificationSender
from app.domain.persona_service import get_or_create_persona
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.preferencia_notificacion_service import guardar_preferencia
from app.domain.sms_failover import FailoverSmsSender
from app.domain.sns_sender import SnsNotificationSender
from app.domain.staff_service import create_initial_admin
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.twilio_sender import TwilioNotificationSender
from app.web.notifications import StagingOverrideSender, get_notification_sender

_PW = "Contrasena1"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _anunciar(client, tel="3001234567", nombre="Ana"):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    return p


def _activar_sms(client, tel, evento):
    # SMS ya no está activo por default salvo para ANUNCIADO (2026-08-10) --
    # los tests que ejercitan Recibido/Entregado/Cancelado lo activan a
    # propósito para poder probar el mensaje/envío de ESE evento. `nombre`
    # solo se usaría si la Persona no existiera ya (nunca pasa acá: siempre
    # se llama después de `_anunciar`, que ya la creó).
    persona = get_or_create_persona(client.db, tel, "PLACEHOLDER")
    guardar_preferencia(client.db, persona.id, CanalNotificacion.SMS, evento, True)
    client.db.commit()


# --------------------------------------------------------------------------- #
# StagingOverrideSender — unidad, sin DB ni HTTP.
# --------------------------------------------------------------------------- #
class _SenderEspia:
    def __init__(self):
        self.enviados = []

    def enviar(self, destino, mensaje):
        self.enviados.append((destino, mensaje))


def test_override_ausente_no_envia_nada_fail_closed():
    espia = _SenderEspia()
    StagingOverrideSender(espia, None).enviar("+573001234567", "hola")
    assert espia.enviados == []


def test_override_vacio_tambien_falla_cerrado():
    espia = _SenderEspia()
    StagingOverrideSender(espia, "   ").enviar("+573001234567", "hola")
    assert espia.enviados == []


def test_override_presente_redirige_al_numero_de_prueba():
    espia = _SenderEspia()
    StagingOverrideSender(espia, "+570000000000").enviar("+573001234567", "hola")
    assert espia.enviados == [("+570000000000", "hola")]  # nunca el real


# --------------------------------------------------------------------------- #
# get_notification_sender — selección por entorno.
# --------------------------------------------------------------------------- #
def test_sin_web_env_devuelve_console_sender_directo(monkeypatch):
    monkeypatch.delenv("WEB_ENV", raising=False)
    sender = get_notification_sender()
    assert isinstance(sender, ConsoleNotificationSender)


def test_staging_devuelve_staging_override_sender(monkeypatch):
    monkeypatch.setenv("WEB_ENV", "staging")
    sender = get_notification_sender()
    assert isinstance(sender, StagingOverrideSender)


def _sin_ningun_proveedor(monkeypatch):
    monkeypatch.delenv("WEB_ENV", raising=False)
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


def test_solo_twilio_configurado_devuelve_twilio_directo(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _twilio_completo(monkeypatch)
    assert isinstance(get_notification_sender(), TwilioNotificationSender)


def test_twilio_con_solo_account_sid_no_se_incluye_en_la_cadena(monkeypatch):
    """Regresión: un Twilio a medio configurar (falta AUTH_TOKEN/FROM_NUMBER)
    no debe entrar a la cadena — si entrara, su `RuntimeError` de config
    rompería el failover hacia SNS (lo trataría como rechazo no
    reintentable, no como falla de conectividad)."""
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")  # solo esta, a propósito
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    sender = get_notification_sender()

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [SnsNotificationSender, LiwaNotificationSender]


def test_liwa_y_twilio_configurados_devuelve_cadena_de_failover_en_orden(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)

    sender = get_notification_sender()

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [LiwaNotificationSender, TwilioNotificationSender]


def test_solo_sns_configurado_devuelve_sns_directo(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    assert isinstance(get_notification_sender(), SnsNotificationSender)


def test_credenciales_aws_de_s3_sin_la_bandera_no_activan_sns(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
    assert isinstance(get_notification_sender(), ConsoleNotificationSender)


def test_los_tres_configurados_devuelve_cadena_completa_en_orden(monkeypatch):
    _sin_ningun_proveedor(monkeypatch)
    _liwa_completo(monkeypatch)
    _twilio_completo(monkeypatch)
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")

    sender = get_notification_sender()

    assert isinstance(sender, FailoverSmsSender)
    assert [type(s) for s in sender.senders] == [
        SnsNotificationSender,
        LiwaNotificationSender,
        TwilioNotificationSender,
    ]


# --------------------------------------------------------------------------- #
# Comportamiento de la cadena de failover con proveedores reales — LIWA
# inalcanzable retrocede a Twilio automáticamente (ticket 02).
# --------------------------------------------------------------------------- #
def test_liwa_inalcanzable_reintenta_automaticamente_con_twilio(monkeypatch):
    # `liwa_sender.httpx` y `twilio_sender.httpx` son el MISMO módulo `httpx`
    # importado dos veces — un solo fake despachado por dominio de URL, no dos
    # `monkeypatch.setattr` independientes (el segundo pisaría al primero).
    llamadas_twilio = []

    class _RespuestaTwilioOk:
        status_code = 201

        def raise_for_status(self):
            pass

    def _post(url, **kwargs):
        if "liwa.co" in url:
            raise httpx.ConnectTimeout("timed out")
        llamadas_twilio.append(kwargs.get("data"))
        return _RespuestaTwilioOk()

    monkeypatch.setenv("LIWA_API_KEY", "fake")
    monkeypatch.setenv("LIWA_ACCOUNT", "fake")
    monkeypatch.setenv("LIWA_PASSWORD", "fake")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")
    monkeypatch.setattr(httpx, "post", _post)
    liwa_sender._token_cache.clear()

    sender = get_notification_sender()
    sender.enviar("+573001234567", "Tu paquete llegó")

    assert llamadas_twilio == [
        {"To": "+573001234567", "MessagingServiceSid": "MGfake", "Body": "Tu paquete llegó"}
    ]


def test_liwa_rechaza_explicitamente_no_reintenta_con_twilio(monkeypatch):
    llamadas_twilio = []

    class _RespuestaLiwaAuth:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"token": "tok"}

    class _RespuestaLiwaRechazo:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"success": False, "message": "saldo insuficiente"}

    def _post(url, **kwargs):
        if "liwa.co" in url and "auth" in url:
            return _RespuestaLiwaAuth()
        if "liwa.co" in url:
            return _RespuestaLiwaRechazo()
        llamadas_twilio.append(kwargs.get("data"))
        raise AssertionError("Twilio no debería llamarse tras un rechazo explícito")

    monkeypatch.setenv("LIWA_API_KEY", "fake")
    monkeypatch.setenv("LIWA_ACCOUNT", "fake")
    monkeypatch.setenv("LIWA_PASSWORD", "fake")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")
    monkeypatch.setattr(httpx, "post", _post)
    liwa_sender._token_cache.clear()

    sender = get_notification_sender()

    with pytest.raises(RuntimeError, match="saldo insuficiente"):
        sender.enviar("+573001234567", "Tu paquete llegó")

    assert llamadas_twilio == []  # nunca se prueba: el rechazo no es reintentable


def test_sns_y_liwa_inalcanzables_reintenta_hasta_twilio(monkeypatch):
    """Los tres proveedores configurados a la vez (ticket 03, orden vigente
    desde `.scratch/pendientes-cliente` 2026-08-06: SNS → LIWA → Twilio):
    SNS y LIWA caídos por conectividad, Twilio es el que finalmente
    entrega."""
    import boto3

    llamadas_twilio = []

    class _ClienteSnsInalcanzable:
        def publish(self, **kwargs):
            raise botocore.exceptions.EndpointConnectionError(
                endpoint_url="https://sns.us-east-1.amazonaws.com"
            )

    class _RespuestaTwilioOk:
        status_code = 201

        def raise_for_status(self):
            pass

    def _post(url, **kwargs):
        if "twilio.com" in url:
            llamadas_twilio.append(kwargs.get("data"))
            return _RespuestaTwilioOk()
        raise httpx.ConnectTimeout("timed out")  # LIWA, inalcanzable

    monkeypatch.setenv("LIWA_API_KEY", "fake")
    monkeypatch.setenv("LIWA_ACCOUNT", "fake")
    monkeypatch.setenv("LIWA_PASSWORD", "fake")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: _ClienteSnsInalcanzable())
    liwa_sender._token_cache.clear()

    sender = get_notification_sender()
    sender.enviar("+573001234567", "Tu paquete llegó")

    assert llamadas_twilio == [
        {"To": "+573001234567", "MessagingServiceSid": "MGfake", "Body": "Tu paquete llegó"}
    ]


# --------------------------------------------------------------------------- #
# Wiring en las rutas (desarrollo/test: sin WEB_ENV, sender inyectado).
# --------------------------------------------------------------------------- #
def test_receive_notifica_al_destinatario(client):
    _login_staff(client)
    p = _anunciar(client, nombre="Ana")
    _activar_sms(client, "3001234567", EstadoPaquete.RECIBIDO)

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia

    client.post(f"/paquetes/{p.id}/recibir", data={})

    assert len(espia.enviados) == 1
    destino, mensaje = espia.enviados[0]
    assert destino == "+573001234567"
    assert "ANA" in mensaje and "portería" in mensaje


def test_deliver_notifica(client):
    _login_staff(client)
    p = _anunciar(client)
    client.post(f"/paquetes/{p.id}/recibir", data={})
    _activar_sms(client, "3001234567", EstadoPaquete.ENTREGADO)

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    client.post(f"/paquetes/{p.id}/entregar")

    assert len(espia.enviados) == 1
    assert "entregado" in espia.enviados[0][1].lower()


def test_cancel_notifica_con_motivo(client):
    _login_staff(client)
    p = _anunciar(client)
    _activar_sms(client, "3001234567", EstadoPaquete.CANCELADO)

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    client.post(f"/paquetes/{p.id}/cancelar", data={"motivo": "NO_RECLAMADO"})

    assert len(espia.enviados) == 1
    assert "no reclamado" in espia.enviados[0][1].lower()


def test_transicion_rechazada_no_notifica(client):
    _login_staff(client)
    p = _anunciar(client)
    client.post(f"/paquetes/{p.id}/recibir", data={})  # ya RECIBIDO

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    r = client.post(f"/paquetes/{p.id}/recibir", data={})  # inválido: ya recibido

    assert r.status_code == 400
    assert espia.enviados == []


# --------------------------------------------------------------------------- #
# El test más importante: fail-closed de punta a punta (HTTP real, sin overrides
# de dependencia — ejercita la wiring de producción tal cual).
# --------------------------------------------------------------------------- #
def test_staging_sin_override_number_cero_llamadas_tras_transicion_real(
    client, monkeypatch
):
    monkeypatch.setenv("WEB_ENV", "staging")
    monkeypatch.delenv("SMS_OVERRIDE_NUMBER", raising=False)

    llamadas = []
    monkeypatch.setattr(
        ConsoleNotificationSender,
        "enviar",
        lambda self, destino, mensaje: llamadas.append((destino, mensaje)),
    )

    _login_staff(client)
    p = _anunciar(client)
    # RECIBIDO ya no está activo por default (2026-08-10) -- se activa a
    # propósito para que el "cero llamadas" de abajo se deba de verdad al
    # fail-closed de staging, no a que el evento ya venía apagado.
    _activar_sms(client, "3001234567", EstadoPaquete.RECIBIDO)

    r = client.post(f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False)
    assert r.status_code == 303

    assert llamadas == []  # fail-closed: CERO llamadas al sender envuelto


def test_staging_sin_override_number_con_los_tres_proveedores_configurados(
    client, monkeypatch
):
    """La garantía fail-closed sobrevive a la cadena de failover completa
    (ticket 03): con LIWA + Twilio + SNS configurados a la vez, sin
    `SMS_OVERRIDE_NUMBER` no debe haber NINGUNA llamada real a ninguno de
    los tres."""
    import boto3

    def _post_que_no_deberia_llamarse(url, **kwargs):
        raise AssertionError("no debería intentarse ningún envío real")

    def _client_que_no_deberia_llamarse(*a, **kw):
        raise AssertionError("no debería intentarse ningún envío real")

    monkeypatch.setenv("WEB_ENV", "staging")
    monkeypatch.delenv("SMS_OVERRIDE_NUMBER", raising=False)
    monkeypatch.setenv("LIWA_API_KEY", "fake")
    monkeypatch.setenv("LIWA_ACCOUNT", "fake")
    monkeypatch.setenv("LIWA_PASSWORD", "fake")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    monkeypatch.setattr(httpx, "post", _post_que_no_deberia_llamarse)
    monkeypatch.setattr(boto3, "client", _client_que_no_deberia_llamarse)
    liwa_sender._token_cache.clear()

    _login_staff(client)
    p = _anunciar(client)
    _activar_sms(client, "3001234567", EstadoPaquete.RECIBIDO)

    r = client.post(f"/paquetes/{p.id}/recibir", data={}, follow_redirects=False)
    assert r.status_code == 303  # la transición sí ocurrió — solo el envío se frenó


def test_staging_con_override_number_redirige_al_numero_de_prueba(client, monkeypatch):
    monkeypatch.setenv("WEB_ENV", "staging")
    monkeypatch.setenv("SMS_OVERRIDE_NUMBER", "+570000000000")

    llamadas = []
    monkeypatch.setattr(
        ConsoleNotificationSender,
        "enviar",
        lambda self, destino, mensaje: llamadas.append((destino, mensaje)),
    )

    _login_staff(client)
    p = _anunciar(client, tel="3001234567")
    _activar_sms(client, "3001234567", EstadoPaquete.RECIBIDO)

    client.post(f"/paquetes/{p.id}/recibir", data={})

    assert len(llamadas) == 1
    destino, _mensaje = llamadas[0]
    assert destino == "+570000000000"
    assert destino != "+573001234567"  # nunca el teléfono real del residente
