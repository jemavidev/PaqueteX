# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/proveedores` (`.scratch/administracion-
proveedores/spec.md`, issue 03).

Comportamiento observable por HTTP: gate `require_admin` (mismo patrón que
`/administracion/notificaciones`); sin personalizar, cada proveedor del
catálogo aparece habilitado por defecto; guardar togglear/reordenar persiste
y se refleja de inmediato en la cadena real de envío (issue 02); WhatsApp/
Llamadas no aparecen (sin proveedor real en el catálogo).
"""

import httpx
from sqlalchemy import text

from app.domain.proveedor_config_historial import ProveedorConfigHistorial
from app.domain.sms_failover import FailoverSmsSender
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario
from app.web.notifications import get_notification_sender

_PW = "Contrasena1"

_VARS_SMS_COMPLETAS = (
    "LIWA_API_KEY",
    "LIWA_ACCOUNT",
    "LIWA_PASSWORD",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_MESSAGING_SERVICE_SID",
    "AWS_SNS_SMS_ENABLED",
)


def _login_admin(client, email="admin@club.com"):
    admin = create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return admin


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/proveedores", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/proveedores")
    assert r.status_code == 403


def test_admin_ve_los_proveedores_del_catalogo_habilitados_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/proveedores")
    assert r.status_code == 200
    assert "AWS SNS" in r.text
    assert "LIWA" in r.text
    assert "Twilio" in r.text
    assert "SMTP" in r.text


def test_whatsapp_y_llamadas_no_aparecen(client):
    # "WhatsApp" a secas aparece en el footer/soporte de TODA la app (ajeno
    # a esta pantalla) -- lo específico de esta pantalla es que no exista
    # una tarjeta de canal para WHATSAPP/LLAMADA (sin proveedor real en el
    # catálogo todavía, sin sección "próximamente").
    _login_admin(client)
    r = client.get("/administracion/proveedores")
    assert "/administracion/proveedores/WHATSAPP" not in r.text
    assert "/administracion/proveedores/LLAMADA" not in r.text
    assert "próximamente" not in r.text.lower()


def test_guardar_deshabilitado_persiste_y_se_refleja_re_renderizado(client):
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={
            "AWS_SNS_habilitado": "on",
            "AWS_SNS_orden": "1",
            "LIWA_orden": "2",  # sin "_habilitado" -- checkbox sin marcar
            "TWILIO_habilitado": "on",
            "TWILIO_orden": "3",
        },
    )

    assert r.status_code == 200
    assert "Configuración guardada." in r.text

    filas = client.db.execute(
        text(
            "SELECT proveedor, habilitado FROM proveedores_notificacion_config "
            "WHERE canal = 'SMS' ORDER BY proveedor"
        )
    ).fetchall()
    habilitado_por_proveedor = {p: h for p, h in filas}
    assert habilitado_por_proveedor["LIWA"] is False
    assert habilitado_por_proveedor["AWS_SNS"] is True
    assert habilitado_por_proveedor["TWILIO"] is True


def test_guardar_reordena_y_afecta_la_cadena_real_de_inmediato(client, monkeypatch):
    # Criterio explícito del ticket 03: "demostrable enviando una
    # notificación de prueba real después del cambio" -- no basta con
    # inspeccionar el tipo del sender armado, hay que ejercitar `.enviar()`
    # de verdad y observar CUÁL proveedor recibe la llamada HTTP.
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={
            "AWS_SNS_habilitado": "on",
            "AWS_SNS_orden": "3",
            "LIWA_habilitado": "on",
            "LIWA_orden": "2",
            "TWILIO_habilitado": "on",
            "TWILIO_orden": "1",
        },
    )
    assert r.status_code == 200

    for var in _VARS_SMS_COMPLETAS:
        monkeypatch.setenv(var, "fake" if var != "AWS_SNS_SMS_ENABLED" else "true")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGfake")

    llamadas_twilio = []

    class _RespuestaTwilioOk:
        status_code = 201

        def raise_for_status(self):
            pass

    def _post(url, **kwargs):
        if "twilio.com" not in url:
            raise AssertionError(
                f"Se llamó a {url!r} -- Twilio (orden=1) debía ser el único invocado"
            )
        llamadas_twilio.append(kwargs.get("data"))
        return _RespuestaTwilioOk()

    monkeypatch.setattr(httpx, "post", _post)

    sender = get_notification_sender(client.db)
    assert isinstance(sender, FailoverSmsSender)

    sender.enviar("+573001234567", "Tu paquete llegó")

    assert llamadas_twilio == [
        {"To": "+573001234567", "MessagingServiceSid": "MGfake", "Body": "Tu paquete llegó"}
    ]


def test_guardar_deja_historial_con_el_actor(client):
    admin = _login_admin(client)

    client.post(
        "/administracion/proveedores/EMAIL",
        data={"SMTP_habilitado": "on"},
    )

    historial = (
        client.db.query(ProveedorConfigHistorial)
        .filter_by(canal="EMAIL", proveedor="SMTP")
        .one()
    )
    assert historial.usuario_id == admin.id
    assert historial.habilitado_nuevo is True


def test_operador_no_puede_guardar(client):
    _login_operador(client)
    r = client.post("/administracion/proveedores/SMS", data={"AWS_SNS_habilitado": "on"})
    assert r.status_code == 403
