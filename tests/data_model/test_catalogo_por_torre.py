# -*- coding: utf-8 -*-
"""
Seam A — Catálogo agrupado por Torre, para los pickers de Torre/Apartamento
de `/mis-datos` (ticket 04) y `/announce-new` (ticket 05).
"""

import pytest

from app.domain.apartamento_service import listar_catalogo_por_torre

pytestmark = pytest.mark.integration


def test_agrupa_las_804_unidades_en_10_torres(db_session):
    catalogo = listar_catalogo_por_torre(db_session)

    assert list(catalogo.keys()) == [f"TORRE {n}" for n in range(1, 11)]
    assert sum(len(aptos) for aptos in catalogo.values()) == 804


def test_apartamentos_de_una_torre_en_orden_numerico(db_session):
    catalogo = listar_catalogo_por_torre(db_session)

    assert catalogo["TORRE 1"][:3] == ["101", "102", "103"]
    assert catalogo["TORRE 1"][-1] == "702"


def test_torre_3_no_tiene_el_duplicado_del_listado_original(db_session):
    catalogo = listar_catalogo_por_torre(db_session)

    assert catalogo["TORRE 3"].count("303") == 1
    assert len(catalogo["TORRE 3"]) == 100
