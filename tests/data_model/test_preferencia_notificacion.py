# -*- coding: utf-8 -*-
"""
Seam A — preferencia de notificación por Canal × Evento (Grupo 13, Ronda 2).

Comportamiento observable: sin fila guardada, el default histórico es SMS
activo / resto inactivo, para cualquier Persona (sin backfill). Guardar una
preferencia la sobreescribe sin tocar las demás combinaciones. La matriz
completa siempre trae las 16 combinaciones (4 canales x 4 eventos).
"""

import pytest

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
    ) is True


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
            esperado = canal is CanalNotificacion.SMS
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
