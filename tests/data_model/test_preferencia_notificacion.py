# -*- coding: utf-8 -*-
"""
Seam A — preferencia de notificación por Canal × Evento (Grupo 13, Ronda 2).

Comportamiento observable: sin fila guardada, el default (2026-08-10) es SMS
activo SOLO para ANUNCIADO -- el resto de eventos, y el resto de canales,
inactivos -- para cualquier Persona (sin backfill). Guardar una preferencia
la sobreescribe sin tocar las demás combinaciones. La matriz completa
siempre trae las 16 combinaciones (4 canales x 4 eventos).
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import agregar_ocupante
from app.domain.paquete import EstadoPaquete
from app.domain.persona_service import get_or_create_persona
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.preferencia_notificacion_service import (
    EVENTOS,
    activar_canal_en_todos_los_eventos,
    guardar_matriz_preferencias,
    guardar_preferencia,
    matriz_preferencias,
    preferencia_activa,
    preferencia_efectiva_ocupante,
)

pytestmark = pytest.mark.integration


def test_sin_fila_guardada_sms_resuelve_activo_por_default(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    assert preferencia_activa(
        db_session, ana.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True


def test_sin_fila_guardada_otro_canal_resuelve_inactivo_por_default(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    for canal in (CanalNotificacion.EMAIL, CanalNotificacion.LLAMADA, CanalNotificacion.WHATSAPP):
        assert preferencia_activa(db_session, ana.id, canal, EstadoPaquete.ANUNCIADO) is False


def test_sin_fila_guardada_sms_otros_eventos_resuelve_inactivo_por_default(db_session):
    # 2026-08-10 (pedido del cliente): una Persona nueva recibe SOLO 1 SMS,
    # al anunciar -- confirma que el teléfono es alcanzable, sin generar
    # envíos en el resto de eventos hasta que se activen a propósito.
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    for evento in (EstadoPaquete.RECIBIDO, EstadoPaquete.ENTREGADO, EstadoPaquete.CANCELADO):
        assert preferencia_activa(db_session, ana.id, CanalNotificacion.SMS, evento) is False


def test_guardar_preferencia_sobreescribe_solo_esa_combinacion(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    guardar_preferencia(
        db_session, ana.id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO, True
    )

    assert preferencia_activa(
        db_session, ana.id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is True
    # El resto de la matriz de Ana no se tocó.
    assert preferencia_activa(
        db_session, ana.id, CanalNotificacion.WHATSAPP, EstadoPaquete.ENTREGADO
    ) is False
    assert preferencia_activa(
        db_session, ana.id, CanalNotificacion.SMS, EstadoPaquete.RECIBIDO
    ) is False  # default: SMS solo activo para ANUNCIADO


def test_guardar_preferencia_dos_veces_actualiza_no_duplica(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    guardar_preferencia(db_session, ana.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO, False)
    guardar_preferencia(db_session, ana.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO, True)

    assert preferencia_activa(
        db_session, ana.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True


def test_matriz_preferencias_trae_las_16_combinaciones_con_default(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    matriz = matriz_preferencias(db_session, ana.id)

    assert len(matriz) == 16
    for canal in CanalNotificacion:
        for evento in EVENTOS:
            esperado = canal is CanalNotificacion.SMS and evento is EstadoPaquete.ANUNCIADO
            assert matriz[(canal.value, evento.value)] is esperado


def test_matriz_preferencias_refleja_lo_guardado(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    guardar_preferencia(db_session, ana.id, CanalNotificacion.EMAIL, EstadoPaquete.CANCELADO, True)
    guardar_preferencia(db_session, ana.id, CanalNotificacion.SMS, EstadoPaquete.CANCELADO, False)

    matriz = matriz_preferencias(db_session, ana.id)
    assert matriz[("EMAIL", "CANCELADO")] is True
    assert matriz[("SMS", "CANCELADO")] is False


def test_guardar_matriz_preferencias_de_una_sola_vez(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    activos = {("WHATSAPP", "ANUNCIADO"), ("EMAIL", "RECIBIDO")}
    guardar_matriz_preferencias(db_session, ana.id, activos)

    matriz = matriz_preferencias(db_session, ana.id)
    for clave, valor in matriz.items():
        assert valor == (clave in activos)


def test_activar_canal_en_todos_los_eventos(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    activar_canal_en_todos_los_eventos(db_session, ana.id, CanalNotificacion.SMS, False)

    for evento in EVENTOS:
        assert preferencia_activa(db_session, ana.id, CanalNotificacion.SMS, evento) is False


# --------------------------------------------------------------------------- #
# Ticket 06 (.scratch/mis-datos) — preferencia efectiva de un Ocupante:
# heredada del principal (sin teléfono) vs propia (con teléfono).
# --------------------------------------------------------------------------- #
def test_ocupante_con_telefono_usa_sus_propias_preferencias(db_session):
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    guardar_preferencia(
        db_session, hija.persona_id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO, True
    )

    assert preferencia_efectiva_ocupante(
        db_session, hija, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is True


def test_ocupante_sin_telefono_usa_las_del_principal(db_session):
    from app.domain.ocupante_service import confirmar_ocupante
    from app.domain.staff_service import create_initial_admin

    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    admin = create_initial_admin(db_session, "admin@club.com", "Admin", "Contrasena1")
    confirmar_ocupante(db_session, papa, admin)  # papá confirmado como principal
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin teléfono

    guardar_preferencia(
        db_session, papa.persona_id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO, True
    )

    assert preferencia_efectiva_ocupante(
        db_session, hijo, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is True
    # Cambiar la del principal también cambia lo que aplica al hijo sin teléfono.
    guardar_preferencia(
        db_session, papa.persona_id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO, False
    )
    assert preferencia_efectiva_ocupante(
        db_session, hijo, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is False


def test_ocupante_sin_telefono_default_historico_sin_preferencia_del_principal(db_session):
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")

    assert preferencia_efectiva_ocupante(
        db_session, hijo, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True
    assert preferencia_efectiva_ocupante(
        db_session, hijo, CanalNotificacion.EMAIL, EstadoPaquete.ANUNCIADO
    ) is False
