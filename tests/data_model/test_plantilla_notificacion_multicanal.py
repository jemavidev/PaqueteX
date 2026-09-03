# -*- coding: utf-8 -*-
"""
Seam A — Plantillas de notificación multicanal (SMS / Email / WhatsApp),
`.scratch/plantillas-notificacion-multicanal`, ticket 01.

Comportamiento observable: cada canal guarda y devuelve su propio texto (y
asunto, solo Email) de forma independiente para el mismo evento/motivo; sin
personalizar, cada canal cae al mismo texto informativo por defecto que ya
usa SMS; el envío real de SMS (`construir_mensaje`) ignora los overrides de
Email/WhatsApp para ese mismo evento -- no cambia de comportamiento por
tenerlos.
"""

import pytest

from app.domain.notificacion_service import (
    construir_mensaje,
    guardar_plantilla,
    mensaje_de_prueba,
    obtener_asunto_actual,
    obtener_texto_actual,
)
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.preferencia_notificacion import CanalNotificacion

pytestmark = pytest.mark.integration


def _anunciar(session):
    return announce(
        session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )


def test_guardar_un_canal_no_afecta_a_los_demas(db_session):
    guardar_plantilla(
        db_session, EstadoPaquete.RECIBIDO, None, "Texto SMS custom", canal=CanalNotificacion.SMS
    )
    guardar_plantilla(
        db_session,
        EstadoPaquete.RECIBIDO,
        None,
        "Texto Email custom",
        canal=CanalNotificacion.EMAIL,
        asunto="Asunto custom",
    )

    assert (
        obtener_texto_actual(db_session, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.SMS)
        == "Texto SMS custom"
    )
    assert (
        obtener_texto_actual(db_session, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.EMAIL)
        == "Texto Email custom"
    )
    # WhatsApp no se tocó -- sigue en el default (mismo texto que el de SMS
    # sin personalizar).
    assert "esta {estado}" in obtener_texto_actual(
        db_session, EstadoPaquete.RECIBIDO, canal=CanalNotificacion.WHATSAPP
    )


def test_sin_personalizar_cada_canal_devuelve_el_mismo_default_informativo(db_session):
    sms = obtener_texto_actual(db_session, EstadoPaquete.ENTREGADO, canal=CanalNotificacion.SMS)
    email = obtener_texto_actual(db_session, EstadoPaquete.ENTREGADO, canal=CanalNotificacion.EMAIL)
    whatsapp = obtener_texto_actual(
        db_session, EstadoPaquete.ENTREGADO, canal=CanalNotificacion.WHATSAPP
    )

    assert sms == email == whatsapp
    assert "esta {estado}" in sms


def test_obtener_texto_actual_default_es_sms_sin_pasar_canal(db_session):
    # Retro-compatibilidad: cualquier caller que no pase `canal` (como la
    # ruta admin de hoy) sigue viendo el texto de SMS, sin cambios.
    guardar_plantilla(db_session, EstadoPaquete.ENTREGADO, None, "Custom SMS")
    assert obtener_texto_actual(db_session, EstadoPaquete.ENTREGADO) == "Custom SMS"


def test_asunto_por_defecto_para_email_no_esta_vacio(db_session):
    assert obtener_asunto_actual(db_session, EstadoPaquete.RECIBIDO)


def test_guardar_asunto_email_lo_persiste(db_session):
    guardar_plantilla(
        db_session,
        EstadoPaquete.RECIBIDO,
        None,
        "cuerpo",
        canal=CanalNotificacion.EMAIL,
        asunto="Tu paquete llegó",
    )
    assert obtener_asunto_actual(db_session, EstadoPaquete.RECIBIDO) == "Tu paquete llegó"


def test_construir_mensaje_ignora_overrides_de_otros_canales(db_session):
    p = _anunciar(db_session)
    guardar_plantilla(
        db_session,
        EstadoPaquete.RECIBIDO,
        None,
        "Texto EMAIL, no debe usarse para el envío real de SMS",
        canal=CanalNotificacion.EMAIL,
    )

    msg = construir_mensaje(db_session, EstadoPaquete.RECIBIDO, p)

    assert "Recibido" in msg  # sigue siendo el default de SMS, no el override de EMAIL


def test_construir_mensaje_sigue_usando_el_override_de_sms(db_session):
    p = _anunciar(db_session)
    guardar_plantilla(
        db_session, EstadoPaquete.RECIBIDO, None, "Hola {recipient_name}, override SMS real."
    )

    msg = construir_mensaje(db_session, EstadoPaquete.RECIBIDO, p)

    assert msg == "Hola ANA, override SMS real."


# --------------------------------------------------------------------------- #
# .scratch/notificaciones-enviar-prueba, ticket 02 -- `mensaje_de_prueba`
# (envío de prueba real desde /administracion/notificaciones).
# --------------------------------------------------------------------------- #
def test_mensaje_de_prueba_resuelve_variables_de_ejemplo(db_session):
    texto, asunto = mensaje_de_prueba(
        db_session, EstadoPaquete.RECIBIDO, None, CanalNotificacion.SMS
    )

    assert "Juan Pérez" in texto
    assert "{recipient_name}" not in texto
    assert asunto is None  # SMS no tiene asunto


def test_mensaje_de_prueba_usa_la_plantilla_ya_guardada_no_un_borrador(db_session):
    guardar_plantilla(
        db_session,
        EstadoPaquete.RECIBIDO,
        None,
        "Hola {recipient_name}, plantilla YA guardada.",
        canal=CanalNotificacion.SMS,
    )

    texto, _ = mensaje_de_prueba(db_session, EstadoPaquete.RECIBIDO, None, CanalNotificacion.SMS)

    assert texto == "Hola Juan Pérez, plantilla YA guardada."


def test_mensaje_de_prueba_email_incluye_asunto_resuelto(db_session):
    guardar_plantilla(
        db_session,
        EstadoPaquete.RECIBIDO,
        None,
        "Cuerpo de prueba.",
        canal=CanalNotificacion.EMAIL,
        asunto="Asunto de prueba para {recipient_name}",
    )

    texto, asunto = mensaje_de_prueba(
        db_session, EstadoPaquete.RECIBIDO, None, CanalNotificacion.EMAIL
    )

    assert texto == "Cuerpo de prueba."
    assert asunto == "Asunto de prueba para Juan Pérez"


def test_mensaje_de_prueba_whatsapp_tampoco_tiene_asunto(db_session):
    _, asunto = mensaje_de_prueba(
        db_session, EstadoPaquete.RECIBIDO, None, CanalNotificacion.WHATSAPP
    )

    assert asunto is None


def test_mensaje_de_prueba_de_un_evento_cancelado_resuelve_su_propio_motivo(db_session):
    # `.scratch/motivos-cancelacion-catalogo`, ticket 03: `motivo` ya llega
    # como la etiqueta legible del catálogo -- sin transformación encima.
    texto, _ = mensaje_de_prueba(
        db_session, EstadoPaquete.CANCELADO, "No reclamado", CanalNotificacion.SMS
    )

    assert "No reclamado" in texto
    assert "{motivo}" not in texto
