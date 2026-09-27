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
