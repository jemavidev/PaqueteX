# -*- coding: utf-8 -*-
"""
Seam A — Servicios de Apartamento y membresía actual, contra el Postgres
efímero construido con `alembic upgrade head`.

Se prueba comportamiento externo observable (resolución contra el catálogo
cerrado, asignar el Apartamento actual, Persona sin Apartamento), no nombres
de columna ni internals de SQLAlchemy.

Catálogo cerrado (`.scratch/apartamento-catalogo-confirmacion`, ticket 03):
`resolver_apartamento` YA NO crea -- resuelve contra las 804 unidades que la
migración de seed (ticket 02) siembra antes de cualquier test. Ya no recibe
`conjunto` (es un único valor global, `configuracion_conjunto_service`) --
solo `torre` + `apartamento`.
"""

import pytest

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import (
    resolver_apartamento,
    set_apartamento_actual,
)
from app.domain.persona_service import get_or_create_persona

pytestmark = pytest.mark.integration


def _total_apartamentos(session) -> int:
    return session.query(Apartamento).count()


def test_terna_valida_del_catalogo_resuelve_sin_crear(db_session):
    total_antes = _total_apartamentos(db_session)

    apto = resolver_apartamento(db_session, "Torre 1", "101")

    assert apto.id is not None
    assert (apto.torre, apto.apartamento) == ("TORRE 1", "101")
    assert _total_apartamentos(db_session) == total_antes  # nada nuevo creado


def test_terna_fuera_del_catalogo_lanza_sin_crear(db_session):
    total_antes = _total_apartamentos(db_session)

    with pytest.raises(ValueError):
        resolver_apartamento(db_session, "Torre 99", "101")

    assert _total_apartamentos(db_session) == total_antes  # no creó nada


def test_apartamento_valido_pero_piso_inexistente_lanza(db_session):
    # "TORRE 1" existe, pero el piso 9 no (Torre 1 solo llega al 7) -- debe
    # fallar igual que una torre inexistente, no crear la unidad.
    with pytest.raises(ValueError):
        resolver_apartamento(db_session, "Torre 1", "901")


def test_casing_distinto_de_terna_valida_resuelve_al_mismo_apartamento(db_session):
    primero = resolver_apartamento(db_session, "TORRE 1", "101")
    otra_vez = resolver_apartamento(db_session, "  torre 1 ", "101")

    assert otra_vez.id == primero.id


def test_set_apartamento_actual_asigna_a_la_persona(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    apto = resolver_apartamento(db_session, "Torre 1", "101")

    actualizada = set_apartamento_actual(db_session, "3001234567", apto)

    assert actualizada.id == persona.id
    assert actualizada.apartamento_actual_id == apto.id


def test_set_apartamento_actual_resuelve_por_telefono_canonico(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto = resolver_apartamento(db_session, "Torre 1", "101")

    # Teléfono en otro formato → misma Persona (resolución canónica).
    actualizada = set_apartamento_actual(db_session, "+57 300 123 4567", apto)

    assert actualizada.apartamento_actual_id == apto.id


def test_persona_sin_apartamento_es_valida(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    # Registrar una Persona no exige Apartamento: la FK queda nula.
    assert persona.apartamento_actual_id is None


def test_desvincular_apartamento_pone_nulo(db_session):
    apto = resolver_apartamento(db_session, "Torre 1", "101")
    get_or_create_persona(db_session, "3001234567", "Ana")
    set_apartamento_actual(db_session, "3001234567", apto)

    desvinculada = set_apartamento_actual(db_session, "3001234567", None)

    assert desvinculada.apartamento_actual_id is None


def test_set_apartamento_actual_persona_inexistente_lanza(db_session):
    apto = resolver_apartamento(db_session, "Torre 1", "101")

    # No hay Persona con ese teléfono y este servicio no la crea (no recibe nombre).
    with pytest.raises(LookupError):
        set_apartamento_actual(db_session, "3009999999", apto)
