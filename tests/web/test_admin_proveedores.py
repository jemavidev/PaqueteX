# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/proveedores` (`.scratch/administracion-
proveedores/spec.md`, issues 03 y 05).

Comportamiento observable por HTTP: gate `require_admin` (mismo patrón que
`/administracion/notificaciones`); sin personalizar, cada proveedor del
catálogo aparece habilitado por defecto; guardar togglear/reordenar persiste
y se refleja de inmediato en la cadena real de envío (issue 02); guardar una
credencial (issue 05) nunca muestra su valor real completo -- issue 291
revela un enmascarado parcial para campos secretos configurados, o el valor
real completo para los no-secretos -- espera confirmación real del
mecanismo SSH (mockeado en estos tests) antes de responder, y deja
auditoría de SOLO el nombre del campo. WhatsApp (META, issue 289) aparece y
es editable pese a no tener `Sender` real; Llamadas (PXB, issues 289-291)
aparece pero bloqueada -- toggle/campos/botón "Guardar" `disabled`, badge
"Próximamente" -- y la ruta POST igual descarta cualquier cambio a un
proveedor `disponible=False` aunque llegue en la petición.
"""

import os
import re

import httpx
from sqlalchemy import text

import app.web.routes.admin_proveedores as admin_proveedores_mod
from app.domain.proveedor_config_historial import ProveedorConfigHistorial
from app.domain.proveedor_credencial_historial import ProveedorCredencialHistorial
from app.domain.sms_failover import FailoverSmsSender
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario
from app.infra.deploy_ssh import ErrorAplicandoCredenciales
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


def test_whatsapp_aparece_y_es_editable(client):
    # Issue 289 (pedido explícito del cliente): revierte la decisión
    # original -- WhatsApp (META) ahora SÍ aparece y es editable, aunque no
    # tenga `Sender` real todavía (deja el terreno de configuración listo).
    _login_admin(client)
    r = client.get("/administracion/proveedores")
    assert "/administracion/proveedores/WHATSAPP" in r.text
    assert "Meta (WhatsApp Business)" in r.text
    assert 'name="META_ACCESS_TOKEN"' in r.text


def test_campos_de_proveedor_editable_se_ocultan_hasta_encenderlo(client):
    # "I was thinking in hiding just the toggles that are disable[d] (hide
    # the forms when toggle is disabled)" -- pedido explícito del cliente,
    # aplica a cualquier proveedor `disponible=True` (AWS SNS, LIWA, Twilio,
    # SMTP, Meta): el marcado que el JS usa para ocultar/mostrar en vivo
    # debe estar presente para estos, referenciando el `id` real del
    # checkbox (que el macro `toggle()` autogenera como `name`).
    _login_admin(client)
    r = client.get("/administracion/proveedores")

    assert 'data-campos-de="AWS_SNS_habilitado"' in r.text
    assert 'data-campos-de="LIWA_habilitado"' in r.text
    assert 'data-campos-de="TWILIO_habilitado"' in r.text
    assert 'data-campos-de="SMTP_habilitado"' in r.text
    assert 'data-campos-de="META_habilitado"' in r.text


def test_llamadas_aparece_bloqueada(client):
    # Issues 289-291 (varias correcciones en vivo del cliente hasta esta
    # versión final): la tab de Llamadas SÍ está presente y se puede entrar
    # a ella -- toggle y campos se VEN, pero `disabled`, con badge
    # "Próximamente"; el botón Guardar también queda deshabilitado.
    _login_admin(client)
    r = client.get("/administracion/proveedores")

    assert 'data-tab="LLAMADA"' in r.text
    assert "Issabel (PBX)" in r.text
    assert "Próximamente" in r.text

    m = re.search(r'<input[^>]*name="PXB_habilitado"[^>]*>', r.text)
    assert m and "disabled" in m.group(0)
    m = re.search(r'<input[^>]*name="PXB_HOST"[^>]*>', r.text)
    assert m and "disabled" in m.group(0)

    # A diferencia de un proveedor editable, PXB NO lleva el marcado que
    # oculta/muestra sus campos según el toggle -- sus campos quedan
    # SIEMPRE visibles (aunque disabled), nunca ocultos.
    assert 'data-campos-de="PXB_habilitado"' not in r.text

    # El botón "Guardar" de ESTE panel (no el de SMS/Email/WhatsApp, que
    # también dicen "Guardar") -- Llamadas es el último canal del catálogo,
    # así que todo lo que sigue a su `data-panel=` hasta el `<script>` final
    # es su propio panel.
    panel_desde = r.text.index('data-panel="LLAMADA"')
    panel_html = r.text[panel_desde:r.text.index("<script>", panel_desde)]
    m = re.search(r'<button\b[^>]*>.*?Guardar.*?</button>', panel_html, re.DOTALL)
    assert m and "disabled" in m.group(0)


def test_llamadas_guardar_no_hace_nada_aunque_se_arme_el_post(client, monkeypatch):
    # Defensa en profundidad: un POST armado a mano (sin pasar por el HTML
    # `disabled`) tampoco debe poder tocar BD ni disparar el mecanismo SSH
    # para un proveedor `disponible=False`.
    def _no_debe_llamarse(cambios):
        raise AssertionError(f"No debía llamarse aplicar_credenciales_proveedor({cambios!r})")

    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", _no_debe_llamarse)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/LLAMADA",
        data={"PXB_habilitado": "on", "PXB_HOST": "10.0.0.5", "PXB_SECRETO": "shh"},
    )

    assert r.status_code == 200
    fila = client.db.execute(
        text(
            "SELECT habilitado FROM proveedores_notificacion_config "
            "WHERE canal = 'LLAMADA' AND proveedor = 'PXB'"
        )
    ).fetchone()
    assert fila is None  # nunca se creó la fila -- el guardado se saltó por completo
    assert client.db.query(ProveedorCredencialHistorial).filter_by(proveedor="PXB").count() == 0


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


# --------------------------------------------------------------------------- #
# Credenciales (issue 05, Fase 2) -- `aplicar_credenciales_proveedor` mockeado
# a nivel de módulo (nunca toca SSH real en estos tests).
# --------------------------------------------------------------------------- #


def test_enmascarar_secreto_revela_solo_inicio_y_final():
    assert admin_proveedores_mod._enmascarar_secreto("AKIAABCDEFGHIJKLMNOP") == "AKIA••••••••MNOP"


def test_enmascarar_secreto_valor_corto_se_enmascara_por_completo():
    # Issue 291: un valor de 8 caracteres o menos revelaría casi todo con
    # 4+4 -- se enmascara entero en vez de exponerlo.
    assert admin_proveedores_mod._enmascarar_secreto("abcd1234") == "••••••••"
    assert admin_proveedores_mod._enmascarar_secreto("a") == "••••••••"


def test_campo_secreto_configurado_muestra_valor_enmascarado_nunca_completo(client, monkeypatch):
    # Issue 291 (pedido explícito del cliente, "solo ver la información
    # necesaria pero no toda"): a diferencia del criterio anterior (nunca
    # mostrar nada del valor real), ahora SÍ se revela un enmascarado
    # parcial -- pero el valor COMPLETO nunca debe aparecer en la respuesta.
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "el-secreto-real-no-debe-aparecer-completo")
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    assert "el-secreto-real-no-debe-aparecer-completo" not in r.text
    assert "Actual: el-s" in r.text  # primeros 4 caracteres visibles
    assert "leto" in r.text  # últimos 4 caracteres ("...completo") visibles


def test_campo_no_secreto_configurado_muestra_el_valor_real_completo(client, monkeypatch):
    # Issue 291: `AWS_REGION` (secreto=False) no es sensible -- "solo ver
    # la información necesaria" para este tipo de campo es el valor
    # completo, sin enmascarar.
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    assert "Actual: us-east-1" in r.text


def test_campo_secreto_muestra_icono_de_candado(client):
    # Pedido explícito del cliente: señal visual genérica en los campos
    # sensibles -- reusa el ícono de candado ya existente en `icons.py`, sin
    # diseñar uno nuevo. `d="M8 11V8a4 4 0 118 0v3"` es el trazo del arco del
    # candado, suficientemente específico para no calzar con otro ícono.
    _login_admin(client)
    r = client.get("/administracion/proveedores")
    assert 'd="M8 11V8a4 4 0 118 0v3"' in r.text


# Issue 294 retiró el `<select>` de los booleanos (ver tests de toggle más
# abajo) -- el ícono en todo campo (issue 291bis) sigue aplicando a los
# campos de texto, cubierto por los dos tests de abajo.


def test_campo_no_secreto_muestra_icono_de_rayo(client):
    # Pedido explícito del cliente, elegido tras comparar variantes en vivo
    # (`prototype`): TODO campo lleva algún ícono, no solo los secretos --
    # `rayo` (genérico, config/ajuste) para los no-secretos, ya que no hay
    # un ícono 1-a-1 para conceptos como "Región" o "Host". `d="M13 10V3L4
    # 14h7v7l9-11h-7z"` es el trazo del rayo, específico de ese ícono.
    _login_admin(client)
    r = client.get("/administracion/proveedores")
    assert 'd="M13 10V3L4 14h7v7l9-11h-7z"' in r.text


def test_campo_configurado_conserva_su_nombre_como_placeholder(client, monkeypatch):
    # Corrección en vivo del cliente: antes, al configurarse, el
    # `placeholder` pasaba de mostrar el NOMBRE del campo a mostrar "Actual:
    # ..." -- perdiendo de vista a simple vista qué campo era cada uno. El
    # nombre ahora se queda siempre en el placeholder; "Actual: ..." vive
    # aparte, como `help_text`.
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    m = re.search(r'<input[^>]*name="AWS_REGION"[^>]*>', r.text)
    assert m and 'placeholder="Región"' in m.group(0)
    assert 'placeholder="Actual: us-east-1"' not in r.text


def test_access_key_id_ya_no_es_secreto(client, monkeypatch):
    # Issue 291, confirmado explícitamente por el cliente ("realiza lo que
    # te pido con las llaves de aws, asi lo necesito"): AWS_ACCESS_KEY_ID
    # es un identificador, no un secreto -- se muestra completo, sin
    # enmascarar, mismo criterio que la propia consola de AWS.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAABCDEFGHIJKLMNOP")
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    assert "Actual: AKIAABCDEFGHIJKLMNOP" in r.text
    m = re.search(r'<input[^>]*name="AWS_ACCESS_KEY_ID"[^>]*>', r.text)
    assert m and 'type="text"' in m.group(0)  # ya no type="password"


def test_campo_vacio_no_llama_al_mecanismo_ssh(client, monkeypatch):
    def _no_debe_llamarse(cambios):
        raise AssertionError(f"No debía llamarse aplicar_credenciales_proveedor({cambios!r})")

    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", _no_debe_llamarse)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={"AWS_SNS_habilitado": "on", "AWS_ACCESS_KEY_ID": "   "},  # solo espacios
    )

    assert r.status_code == 200
    assert "Configuración guardada." in r.text


def test_guardar_credencial_exitosa_deja_auditoria_solo_del_campo(client, monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append
    )
    admin = _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={"AWS_SNS_habilitado": "on", "AWS_ACCESS_KEY_ID": "AKIANUEVA"},
    )

    assert r.status_code == 200
    assert "Configuración guardada." in r.text
    assert llamadas == [{"AWS_ACCESS_KEY_ID": "AKIANUEVA"}]

    historial = (
        client.db.query(ProveedorCredencialHistorial)
        .filter_by(canal="SMS", proveedor="AWS_SNS", campo="AWS_ACCESS_KEY_ID")
        .one()
    )
    assert historial.usuario_id == admin.id
    # Ninguna columna de esta tabla puede guardar el valor -- lo confirma el
    # propio modelo (solo canal/proveedor/campo/usuario_id/created_at), pero
    # además nunca aparece en la respuesta HTML.
    assert "AKIANUEVA" not in r.text


def test_guardar_credencial_que_falla_muestra_error_sin_auditoria(client, monkeypatch):
    def _falla(cambios):
        raise ErrorAplicandoCredenciales("el servidor rechazó la conexión")

    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", _falla)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={"AWS_SNS_habilitado": "on", "AWS_ACCESS_KEY_ID": "AKIANUEVA"},
    )

    assert r.status_code == 400
    assert "el servidor rechazó la conexión" in r.text
    assert "AKIANUEVA" not in r.text  # el valor sometido nunca debe filtrarse
    assert client.db.query(ProveedorCredencialHistorial).count() == 0

    # "sin cambios ... en el estado configurado percibido" (ticket 05): el
    # intento fallido no debió tocar el entorno -- sigue sin estar seteado.
    assert not os.environ.get("AWS_ACCESS_KEY_ID")


def test_guardar_credencial_que_falla_no_bloquea_el_habilitado_orden(client, monkeypatch):
    # Decisión de diseño explícita (docstring del módulo): habilitado/orden
    # (BD, de bajo riesgo) se aplica igual aunque la parte de credenciales
    # (SSH, de alto riesgo) falle en el mismo submit.
    def _falla(cambios):
        raise ErrorAplicandoCredenciales("timeout")

    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", _falla)
    _login_admin(client)

    client.post(
        "/administracion/proveedores/SMS",
        data={"LIWA_orden": "2", "AWS_ACCESS_KEY_ID": "AKIANUEVA"},  # LIWA sin "_habilitado"
    )

    fila = client.db.execute(
        text(
            "SELECT habilitado FROM proveedores_notificacion_config "
            "WHERE canal = 'SMS' AND proveedor = 'LIWA'"
        )
    ).scalar()
    assert fila is False


def test_campo_booleano_es_un_toggle_no_un_select(client):
    # Issue 294, pedido explícito del cliente ("para Usar TLS y Usar SSL
    # crea un toggle para cada uno"): reemplaza el dropdown de 3 estados
    # ("No cambiar"/true/false) por un switch real, mismo componente que ya
    # usa el toggle `habilitado` de arriba.
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    assert "<select" not in r.text  # ya no queda ningún booleano como select
    m = re.search(r'<input[^>]*name="SMTP_USE_TLS"[^>]*>', r.text)
    assert m and 'type="checkbox"' in m.group(0)


def test_campo_booleano_toggle_refleja_el_valor_real_configurado(client, monkeypatch):
    # El switch arranca en la posición que de verdad tiene `.env` -- no hay
    # un tercer estado "sin configurar" posible en un toggle real, así que
    # sin configurar se muestra apagado (mismo criterio que la comparación
    # de `_campo_cambio`: sin configurar equivale a "false").
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    m = re.search(r'<input[^>]*name="SMTP_USE_TLS"[^>]*>', r.text)
    assert m and "checked" in m.group(0)
    m = re.search(r'<input[^>]*name="SMTP_USE_SSL"[^>]*>', r.text)  # nunca configurado en este test
    assert m and "checked" not in m.group(0)


def test_prender_toggle_booleano_dispara_ssh_solo_con_ese_campo(client, monkeypatch):
    # SMTP_USE_TLS pasa de "sin configurar" (~ false) a encendido -- cambio
    # real. SMTP_USE_SSL se queda apagado (ausente del form, como manda un
    # checkbox real sin marcar) -- sin configurar tampoco, sigue
    # equivaliendo a "false": no debe aparecer en el cambio.
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/EMAIL",
        data={"SMTP_habilitado": "on", "SMTP_USE_TLS": "on"},
    )

    assert r.status_code == 200
    assert llamadas == [{"SMTP_USE_TLS": "true"}]


def test_guardar_toggle_booleano_sin_cambiar_no_dispara_ssh(client, monkeypatch):
    # El punto entero de comparar contra el valor real (issue 294): un
    # guardado que no tocó el switch no debe reiniciar el servidor.
    monkeypatch.setenv("SMTP_USE_TLS", "true")

    def _no_debe_llamarse(cambios):
        raise AssertionError(f"No debía llamarse aplicar_credenciales_proveedor({cambios!r})")

    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", _no_debe_llamarse)
    _login_admin(client)

    # El switch ya está prendido (SMTP_USE_TLS=true) -- se manda tal cual
    # estaba, SMTP_USE_SSL se deja apagado tal cual estaba (ausente).
    r = client.post(
        "/administracion/proveedores/EMAIL",
        data={"SMTP_habilitado": "on", "SMTP_USE_TLS": "on"},
    )

    assert r.status_code == 200


def test_apagar_toggle_booleano_ya_configurado_dispara_ssh_con_false(client, monkeypatch):
    monkeypatch.setenv("SMTP_USE_SSL", "true")
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    # SMTP_USE_SSL ausente del form -- checkbox real sin marcar.
    r = client.post(
        "/administracion/proveedores/EMAIL",
        data={"SMTP_habilitado": "on"},
    )

    assert r.status_code == 200
    assert llamadas == [{"SMTP_USE_SSL": "false"}]


# --------------------------------------------------------------------------- #
# Issue 293 -- "el toggle debe hacer las 2 cosas": AWS_SNS_SMS_ENABLED deja de
# ser un campo editable aparte, el toggle `habilitado` lo sincroniza solo.
# --------------------------------------------------------------------------- #


def test_aws_sns_sms_enabled_ya_no_aparece_como_campo(client):
    _login_admin(client)
    r = client.get("/administracion/proveedores")
    assert 'name="AWS_SNS_SMS_ENABLED"' not in r.text
    assert "Bandera AWS_SNS_SMS_ENABLED" not in r.text


def test_apagar_toggle_sincroniza_bandera_a_false(client, monkeypatch):
    # AWS_SNS arranca `habilitado=True` por el fallback de
    # `habilitado_orden_efectivos` (sin fila en BD todavía) -- apagarlo es
    # un cambio real de valor, debe disparar la sincronización.
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    r = client.post("/administracion/proveedores/SMS", data={})  # AWS_SNS sin "_habilitado" -- checkbox apagado

    assert r.status_code == 200
    assert llamadas == [{"AWS_SNS_SMS_ENABLED": "false"}]


def test_prender_toggle_sincroniza_bandera_a_true(client, monkeypatch):
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    # Primero lo apaga (cambio real) para dejarlo en `False` conocido...
    client.post("/administracion/proveedores/SMS", data={})
    llamadas.clear()
    # ...ahora prenderlo de nuevo SÍ es un cambio real.
    r = client.post("/administracion/proveedores/SMS", data={"AWS_SNS_habilitado": "on"})

    assert r.status_code == 200
    assert llamadas == [{"AWS_SNS_SMS_ENABLED": "true"}]


def test_guardar_sin_cambiar_el_toggle_no_dispara_sincronizacion(client, monkeypatch):
    # El punto entero de sincronizar solo ante un cambio real: evitar un
    # reinicio del servidor en cada guardado que no tocó el toggle.
    def _no_debe_llamarse(cambios):
        raise AssertionError(f"No debía llamarse aplicar_credenciales_proveedor({cambios!r})")

    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", _no_debe_llamarse)
    _login_admin(client)

    # AWS_SNS ya está `habilitado=True` por el fallback -- mandarlo "on" de
    # nuevo no es un cambio.
    r = client.post("/administracion/proveedores/SMS", data={"AWS_SNS_habilitado": "on"})

    assert r.status_code == 200


def test_post_manual_de_la_bandera_oculta_se_ignora(client, monkeypatch):
    # Defensa en profundidad: un POST armado a mano con el nombre del campo
    # oculto no debe poder colarse por la vía manual -- solo la
    # sincronización automática puede tocar esta variable.
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={"AWS_SNS_habilitado": "on", "AWS_SNS_SMS_ENABLED": "valor-arbitrario"},
    )

    assert r.status_code == 200
    # Sin cambio real de `habilitado` (ya era True por fallback) -- no debía
    # llamarse el mecanismo SSH en absoluto, ni con el valor manual.
    assert llamadas == []


def test_post_manual_no_pisa_el_valor_real_de_la_sincronizacion(client, monkeypatch):
    # Caso más fuerte que el anterior: esta vez SÍ hay un cambio real de
    # `habilitado` (se apaga) -- el POST manual intenta colar un valor
    # ARBITRARIO para la misma variable que la sincronización automática ya
    # va a fijar en "false". El valor real de la sincronización debe ganar,
    # nunca el manual (que ni siquiera es "true"/"false").
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={"AWS_SNS_SMS_ENABLED": "valor-arbitrario"},  # sin "_habilitado" -- toggle se apaga
    )

    assert r.status_code == 200
    assert llamadas == [{"AWS_SNS_SMS_ENABLED": "false"}]
