"""Issue 433 (.scratch/pendientes-cliente): la pestaña WhatsApp de cada estado es el mensaje del botón WhatsApp de
/paquetes, con formato enriquecido (negrillas, emojis, tildes, saltos de línea) -- el SMS no cambia."""

from urllib.parse import quote

from app.domain.notificacion_service import (
    EVENTO_SOLICITUD_AUTORIZACION,
    construir_mensaje,
    guardar_plantilla,
    mensaje_whatsapp_paquete,
    normalizar_texto_plantilla,
    obtener_texto_actual,
    texto_solicitud_autorizacion,
    validar_texto_whatsapp,
)
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_lifecycle import cancel
from app.domain.paquete_service import Destinatario, announce
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.staff_service import create_initial_admin

_BASE = "https://ejemplo.test"


def _paquete(db_session, nombre="Ana"):
    p = announce(db_session, anunciante_telefono="3001234567", anunciante_nombre=nombre,
                 destinatario=Destinatario.yo_mismo())
    db_session.flush()
    return p


def test_sin_editar_el_mensaje_de_whatsapp_es_el_de_siempre_del_boton(db_session):
    p = _paquete(db_session)

    mensaje = mensaje_whatsapp_paquete(db_session, p, _BASE)

    assert mensaje == (
        f"Hola *ANA*, tu paquete con código *{p.access_code}* está *Anunciado*. "
        f"Consulta más detalles aquí: {_BASE}/consultar?q={p.access_code}"
    )


def test_cancelado_por_defecto_incluye_el_motivo(db_session):
    staff = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")
    p = _paquete(db_session)
    cancel(db_session, p, staff, "Dirección errada")
    db_session.flush()

    assert "Dirección errada" in mensaje_whatsapp_paquete(db_session, p, _BASE)


def test_la_pestana_whatsapp_editada_es_el_mensaje_con_formato_emojis_y_saltos(db_session):
    p = _paquete(db_session)
    guardar_plantilla(db_session, EstadoPaquete.ANUNCIADO, None,
                      "📦 *Hola {recipient_name}*\n_Tu código:_ ~viejo~ {access_code}\nMás: {link}",
                      canal=CanalNotificacion.WHATSAPP)

    mensaje = mensaje_whatsapp_paquete(db_session, p, _BASE)

    assert mensaje == f"📦 *Hola ANA*\n_Tu código:_ ~viejo~ {p.access_code}\nMás: {_BASE}/consultar?q={p.access_code}"
    assert "%0A" in quote(mensaje) and "%F0%9F%93%A6" in quote(mensaje)  # viaja bien en el enlace


def test_el_sms_no_cambia_sin_tildes_y_con_su_propia_pestana(db_session):
    p = _paquete(db_session)
    guardar_plantilla(db_session, EstadoPaquete.ANUNCIADO, None, "*WhatsApp* {recipient_name}",
                      canal=CanalNotificacion.WHATSAPP)

    sms = construir_mensaje(db_session, EstadoPaquete.ANUNCIADO, p, _BASE)

    assert "*WhatsApp*" not in sms and "codigo" in sms and "código" not in sms


def test_el_default_de_whatsapp_trae_formato_y_el_de_sms_no(db_session):
    whatsapp = obtener_texto_actual(db_session, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.WHATSAPP)
    sms = obtener_texto_actual(db_session, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.SMS)

    assert "*{recipient_name}*" in whatsapp and "código" in whatsapp
    assert "*" not in sms and "codigo" in sms


def test_la_solicitud_de_autorizacion_se_guarda_con_formato(db_session):
    guardar_plantilla(db_session, EVENTO_SOLICITUD_AUTORIZACION, None, "¡Hola veci! 👋\n*¿Nos autoriza?*",
                      canal=CanalNotificacion.WHATSAPP)

    assert texto_solicitud_autorizacion(db_session) == "¡Hola veci! 👋\n*¿Nos autoriza?*"


def test_normalizar_convierte_los_saltos_de_windows_y_recorta_los_bordes():
    assert normalizar_texto_plantilla("  Hola\r\n*veci*\r\n  ") == "Hola\n*veci*"


def test_validar_whatsapp_rechaza_variable_desconocida_y_texto_largo():
    assert validar_texto_whatsapp("Hola {recipient_name} {link}") is None
    assert "{nombre}" in validar_texto_whatsapp("Hola {nombre}")
    assert "500" in validar_texto_whatsapp("x" * 501)
    # La solicitud no tiene variables: las llaves se muestran tal cual, no se validan.
    assert validar_texto_whatsapp("Hola {nombre}", con_variables=False) is None
