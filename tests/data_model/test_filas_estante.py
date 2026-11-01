# -*- coding: utf-8 -*-
"""
Seam A — filas del estante activas/desactivadas (issue 416, `.scratch/pendientes-cliente`).

El ADMIN desactiva filas completas del estante (sus dos lados); una Posición de una fila desactivada no se puede
elegir al Recibir. Nunca se puede quedar sin ninguna fila activa: la Posición es obligatoria al recibir.
"""

import pytest

from app.domain.posicion_service import (
    SinFilasActivas,
    filas_desactivadas,
    guardar_filas_activas,
    posicion_habilitada,
)

pytestmark = pytest.mark.integration


def test_sin_configurar_todas_las_filas_estan_activas(db_session):
    assert filas_desactivadas(db_session) == frozenset()


def test_guardar_las_filas_activas_desactiva_las_demas(db_session):
    guardar_filas_activas(db_session, {3, 4, 5, 6, 7})

    assert filas_desactivadas(db_session) == frozenset({1, 2})


def test_volver_a_activar_una_fila_la_saca_de_las_desactivadas(db_session):
    guardar_filas_activas(db_session, {1, 2, 3, 5, 6})
    guardar_filas_activas(db_session, {1, 2, 3, 4, 5, 6})

    assert filas_desactivadas(db_session) == frozenset({7})


def test_no_se_puede_dejar_el_estante_sin_ninguna_fila_activa(db_session):
    guardar_filas_activas(db_session, {1, 2, 3, 4, 5, 6})

    with pytest.raises(SinFilasActivas):
        guardar_filas_activas(db_session, set())

    assert filas_desactivadas(db_session) == frozenset({7})  # sin efecto


def test_una_posicion_de_una_fila_desactivada_no_esta_habilitada(db_session):
    guardar_filas_activas(db_session, {1, 2, 3, 5, 6, 7})

    assert not posicion_habilitada(db_session, "41")
    assert not posicion_habilitada(db_session, "42")
    assert posicion_habilitada(db_session, "51")


# --------------------------------------------------------------------------- #
# El piso "00" (pedido del cliente 2026-10-31): una Posición válida que NO es una fila.
# --------------------------------------------------------------------------- #
def test_el_piso_es_una_posicion_valida_fuera_de_la_grilla_del_estante():
    from app.domain.posicion import GRILLA_ESTANTE, POSICION_PISO, POSICIONES_VALIDAS, normalizar_posicion

    assert POSICION_PISO == "00"
    assert "00" in POSICIONES_VALIDAS
    assert normalizar_posicion(" 00 ") == "00"
    assert all("00" not in fila for fila in GRILLA_ESTANTE)


def test_el_piso_sigue_habilitado_con_cualquier_fila_desactivada(db_session):
    guardar_filas_activas(db_session, {7})

    assert posicion_habilitada(db_session, "00")
    assert 0 not in filas_desactivadas(db_session)


def test_guardar_filas_ignora_una_fila_0(db_session):
    guardar_filas_activas(db_session, {0, 1, 2, 3, 4, 5, 6, 7})

    assert filas_desactivadas(db_session) == frozenset()


def _recibido(db_session, posicion="41"):
    from app.domain.paquete_lifecycle import receive
    from app.domain.paquete_service import Destinatario, announce
    from app.domain.staff_service import create_initial_admin

    staff = create_initial_admin(db_session, "staff@club.com", "Staff", "Contrasena1")
    p = announce(db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana", destinatario=Destinatario.yo_mismo())
    receive(db_session, p, staff, posicion=posicion)
    db_session.flush()
    return p, staff


def test_cambiar_posicion_de_un_recibido_la_guarda_incluido_el_piso(db_session):
    from app.domain.posicion_service import cambiar_posicion

    p, _ = _recibido(db_session)

    assert cambiar_posicion(db_session, p, "00") == "00"
    assert p.posicion == "00"
    assert cambiar_posicion(db_session, p, "72") == "72"
    assert p.posicion == "72"


def test_cambiar_posicion_rechaza_invalida_desactivada_vacia_y_no_recibido_sin_efecto(db_session):
    from app.domain.paquete_lifecycle import deliver
    from app.domain.posicion import PosicionInvalida
    from app.domain.posicion_service import PosicionNoCambiable, cambiar_posicion

    p, staff = _recibido(db_session)
    guardar_filas_activas(db_session, {1, 2, 3, 5, 6, 7})

    with pytest.raises(PosicionInvalida):
        cambiar_posicion(db_session, p, "13")
    with pytest.raises(PosicionNoCambiable):
        cambiar_posicion(db_session, p, "42")
    with pytest.raises(PosicionNoCambiable):
        cambiar_posicion(db_session, p, "")
    assert p.posicion == "41"

    deliver(db_session, p, staff)
    with pytest.raises(PosicionNoCambiable):
        cambiar_posicion(db_session, p, "00")
    assert p.posicion == "41"
