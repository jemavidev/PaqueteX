# -*- coding: utf-8 -*-
"""
Seam A — `TarifaCobro` vigente, contra el Postgres efímero construido con
`alembic upgrade head`. Comportamiento externo: valores por defecto sin fila,
y que la fila se materialice y persista al usarla.

Los agregados de cobros (por rango, Tipo, Cobrado/Anulado, clientes,
operadores...) viven en `test_estadisticas_tablero_service.py` -- el servicio
de estadísticas orientado a listas que se probaba acá se retiró con el ticket
17 de `.scratch/estadisticas-cobro-dashboard`.
"""

import pytest

from app.domain.cobro_service import obtener_tarifas_vigentes

pytestmark = pytest.mark.integration


def test_sin_fila_devuelve_los_valores_por_defecto(db_session):
    tarifas = obtener_tarifas_vigentes(db_session)

    assert tarifas.base_normal == 1500
    assert tarifas.base_extra_dimensionado == 2000
    assert tarifas.bodegaje_normal_24h == 1000
    assert tarifas.bodegaje_extra_dimensionado_24h == 1500


def test_la_fila_se_materializa_y_persiste(db_session):
    tarifas = obtener_tarifas_vigentes(db_session)
    tarifas.base_normal = 1800
    db_session.flush()

    tarifas_de_nuevo = obtener_tarifas_vigentes(db_session)
    assert tarifas_de_nuevo.base_normal == 1800
    assert tarifas_de_nuevo.id == tarifas.id
