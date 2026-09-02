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


def test_campo_booleano_se_muestra_como_select_no_como_texto_libre(client):
    # Issue 01 ya prometía (docstring de `CampoProveedor.tipo`) que "tipo"
    # gobernaría el input de esta Fase 2 -- AWS_SNS_SMS_ENABLED es
    # `tipo="booleano"`, code review issue 05: no debía quedar como texto
    # libre sin validar.
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    assert '<select' in r.text
    assert 'name="AWS_SNS_SMS_ENABLED"' in r.text
    assert 'value="true"' in r.text
    assert 'value="false"' in r.text
    # El input de texto libre viejo para este campo específico ya no existe.
    assert 'name="AWS_SNS_SMS_ENABLED" type="text"' not in r.text


def test_campo_booleano_configurado_muestra_actual_como_ayuda_no_en_el_placeholder(client, monkeypatch):
    # Corrección en vivo del cliente (issue 291): "No cambiar (actual:
    # true)" apretado en el placeholder del dropdown se veía irregular --
    # separado en un `help_text` corto debajo, placeholder simplificado a
    # "No cambiar" a secas.
    monkeypatch.setenv("AWS_SNS_SMS_ENABLED", "true")
    _login_admin(client)

    r = client.get("/administracion/proveedores")

    assert "Actual: true" in r.text
    assert "No cambiar (actual:" not in r.text
    m = re.search(r'<option value=""[^>]*>([^<]*)</option>', r.text)
    assert m and m.group(1).strip() == "No cambiar"


def test_campo_booleano_con_valor_nuevo_se_manda_al_mecanismo_ssh(client, monkeypatch):
    llamadas = []
    monkeypatch.setattr(admin_proveedores_mod, "aplicar_credenciales_proveedor", llamadas.append)
    _login_admin(client)

    r = client.post(
        "/administracion/proveedores/SMS",
        data={"AWS_SNS_habilitado": "on", "AWS_SNS_SMS_ENABLED": "true"},
    )

    assert r.status_code == 200
    assert llamadas == [{"AWS_SNS_SMS_ENABLED": "true"}]
