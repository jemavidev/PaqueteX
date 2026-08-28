# -*- coding: utf-8 -*-
"""
Seam A — Historial de auditoría de plantillas (`PlantillaNotificacionHistorial`),
`.scratch/plantillas-notificacion-multicanal`, ticket 04.

Comportamiento observable: cada `guardar_plantilla` exitoso deja una fila de
historial nueva (nunca se edita/borra una existente); la primera
personalización de una fila tiene `texto_anterior=None`; guardados
siguientes encadenan el texto anterior real.
"""

import pytest

from app.domain.notificacion_service import guardar_plantilla
from app.domain.paquete import EstadoPaquete
from app.domain.plantilla_notificacion_historial import PlantillaNotificacionHistorial
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _historial(session, evento, canal):
    return (
        session.query(PlantillaNotificacionHistorial)
        .filter(
            PlantillaNotificacionHistorial.evento == evento.value,
            PlantillaNotificacionHistorial.canal == canal.value,
        )
        .order_by(PlantillaNotificacionHistorial.created_at)
        .all()
    )


def test_primera_personalizacion_deja_texto_anterior_none(db_session):
    guardar_plantilla(db_session, EstadoPaquete.RECIBIDO, None, "Primer texto.")

    filas = _historial(db_session, EstadoPaquete.RECIBIDO, CanalNotificacion.SMS)
    assert len(filas) == 1
    assert filas[0].texto_anterior is None
    assert filas[0].texto_nuevo == "Primer texto."


def test_guardar_dos_veces_deja_dos_registros_encadenados(db_session):
    guardar_plantilla(db_session, EstadoPaquete.RECIBIDO, None, "Primer texto.")
    guardar_plantilla(db_session, EstadoPaquete.RECIBIDO, None, "Segundo texto.")

    filas = _historial(db_session, EstadoPaquete.RECIBIDO, CanalNotificacion.SMS)
    assert len(filas) == 2
    assert filas[0].texto_anterior is None
    assert filas[0].texto_nuevo == "Primer texto."
    assert filas[1].texto_anterior == "Primer texto."
    assert filas[1].texto_nuevo == "Segundo texto."


def test_historial_denormaliza_evento_motivo_canal(db_session):
    guardar_plantilla(
        db_session, EstadoPaquete.CANCELADO, "NO_RECLAMADO", "Texto cancelado.",
        canal=CanalNotificacion.WHATSAPP,
    )

    fila = _historial(db_session, EstadoPaquete.CANCELADO, CanalNotificacion.WHATSAPP)[0]
    assert fila.motivo == "NO_RECLAMADO"
    assert fila.canal == CanalNotificacion.WHATSAPP


def test_historial_de_email_guarda_asunto_anterior_y_nuevo(db_session):
    guardar_plantilla(
        db_session, EstadoPaquete.RECIBIDO, None, "Cuerpo 1", canal=CanalNotificacion.EMAIL,
        asunto="Asunto 1",
    )
    guardar_plantilla(
        db_session, EstadoPaquete.RECIBIDO, None, "Cuerpo 2", canal=CanalNotificacion.EMAIL,
        asunto="Asunto 2",
    )

    filas = _historial(db_session, EstadoPaquete.RECIBIDO, CanalNotificacion.EMAIL)
    assert filas[0].asunto_anterior is None
    assert filas[0].asunto_nuevo == "Asunto 1"
    assert filas[1].asunto_anterior == "Asunto 1"
    assert filas[1].asunto_nuevo == "Asunto 2"


def test_guardar_sin_usuario_deja_usuario_id_none(db_session):
    guardar_plantilla(db_session, EstadoPaquete.ENTREGADO, None, "Texto sin actor.")

    fila = _historial(db_session, EstadoPaquete.ENTREGADO, CanalNotificacion.SMS)[0]
    assert fila.usuario_id is None


def test_guardar_con_usuario_lo_persiste(db_session):
    admin = Usuario(nombre="Admin", rol=RolUsuario.ADMIN)
    db_session.add(admin)
    db_session.flush()

    guardar_plantilla(
        db_session, EstadoPaquete.ENTREGADO, None, "Texto con actor.", usuario_id=admin.id
    )

    fila = _historial(db_session, EstadoPaquete.ENTREGADO, CanalNotificacion.SMS)[0]
    assert fila.usuario_id == admin.id
