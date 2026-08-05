# -*- coding: utf-8 -*-
"""
Seam B — Migración de seed del catálogo cerrado de Apartamento (804 unidades,
`.scratch/apartamento-catalogo-confirmacion`, ticket 02).

Aserción delgada, mismo espíritu que `test_migration_graph.py`: se prueba el
EFECTO de la migración sobre un Postgres vacío (cuántas filas, con qué
`conjunto`), no el código Python de la migración en sí.
"""

import pytest
from sqlalchemy import create_engine, text

import _harness as H
from app.domain.apartamento_service import resolver_apartamento

pytestmark = pytest.mark.integration

# Total por torre, ya verificado con el cliente (spec, sección "Catálogo
# completo") -- Torre 3 con el typo de Piso 3 (`303` duplicado) corregido.
_TOTAL_POR_TORRE = {
    "TORRE 1": 38,
    "TORRE 2": 56,
    "TORRE 3": 100,
    "TORRE 4": 104,
    "TORRE 5": 104,
    "TORRE 6": 104,
    "TORRE 7": 104,
    "TORRE 8": 100,
    "TORRE 9": 56,
    "TORRE 10": 38,
}


def _filas_apartamentos(db_url: str) -> list[tuple[str, str, str]]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT conjunto, torre, apartamento FROM apartamentos")
            ).all()
    finally:
        engine.dispose()
    return [tuple(r) for r in rows]


def test_upgrade_head_siembra_exactamente_804_unidades(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", "head")

    filas = _filas_apartamentos(empty_db_url)

    assert len(filas) == 804


def test_conteo_por_torre_coincide_con_el_catalogo_verificado(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", "head")

    filas = _filas_apartamentos(empty_db_url)
    conteo: dict[str, int] = {}
    for _conjunto, torre, _apartamento in filas:
        conteo[torre] = conteo.get(torre, 0) + 1

    assert conteo == _TOTAL_POR_TORRE


def test_todas_las_filas_usan_el_conjunto_vigente(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", "head")

    filas = _filas_apartamentos(empty_db_url)

    assert all(conjunto == "EL CLUB" for conjunto, _torre, _apartamento in filas)


def test_sin_duplicados_de_terna(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", "head")

    filas = _filas_apartamentos(empty_db_url)

    assert len(set(filas)) == len(filas)


def test_downgrade_a_la_revision_anterior_vacia_la_tabla(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", "head")
    assert len(_filas_apartamentos(empty_db_url)) == 804

    H.run_alembic(empty_db_url, "downgrade", "0020_configuracion_conjunto")

    assert _filas_apartamentos(empty_db_url) == []

    # Re-upgrade: reproducible sobre BD limpia (mismo espíritu que Seam B).
    H.run_alembic(empty_db_url, "upgrade", "head")
    assert len(_filas_apartamentos(empty_db_url)) == 804


def test_resolver_apartamento_reutiliza_una_unidad_ya_sembrada(db_session):
    total_antes = db_session.execute(text("SELECT COUNT(*) FROM apartamentos")).scalar()

    apto = resolver_apartamento(db_session, "Torre 1", "101")

    total_despues = db_session.execute(text("SELECT COUNT(*) FROM apartamentos")).scalar()
    # Ya estaba sembrada por la migración -- el total NO creció.
    assert total_despues == total_antes
    assert apto.conjunto == "EL CLUB"
