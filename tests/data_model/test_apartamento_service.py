# -*- coding: utf-8 -*-
"""
Seam A — Servicios de Apartamento y membresía actual, contra el Postgres
efímero construido con `alembic upgrade head`.

Se prueba comportamiento externo observable (dedup por terna, asignar el
Apartamento actual, Persona sin Apartamento), no nombres de columna ni internals
de SQLAlchemy. El dedup del Apartamento se verifica por su efecto: misma terna
(en cualquier casing/espaciado) → UN solo Apartamento.
"""

import pytest

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import (
    get_or_create_apartamento,
    set_apartamento_actual,
)
from app.domain.persona_service import get_or_create_persona

pytestmark = pytest.mark.integration


def _total_apartamentos(session) -> int:
    return session.query(Apartamento).count()


def test_terna_nueva_crea_apartamento(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")

    assert apto.id is not None
    # Persistido en forma canónica (MAYÚSCULAS, espacios colapsados).
    assert (apto.conjunto, apto.torre, apto.apartamento) == (
        "LAS FLORES",
        "TORRE A",
        "101",
    )
    assert _total_apartamentos(db_session) == 1


def test_misma_terna_reutiliza_el_mismo_apartamento_sin_duplicar(db_session):
    primero = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    # Otro casing/espaciado de la MISMA terna → mismo Apartamento.
    otra_vez = get_or_create_apartamento(db_session, "  las flores ", "torre a", "101")

    assert otra_vez.id == primero.id
    assert _total_apartamentos(db_session) == 1  # dedup por terna, sin duplicados


def test_ternas_distintas_crean_apartamentos_distintos(db_session):
    a = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    b = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "102")

    assert a.id != b.id
    assert _total_apartamentos(db_session) == 2


def test_set_apartamento_actual_asigna_a_la_persona(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")

    actualizada = set_apartamento_actual(db_session, "3001234567", apto)

    assert actualizada.id == persona.id
    assert actualizada.apartamento_actual_id == apto.id


def test_set_apartamento_actual_resuelve_por_telefono_canonico(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")

    # Teléfono en otro formato → misma Persona (resolución canónica).
    actualizada = set_apartamento_actual(db_session, "+57 300 123 4567", apto)

    assert actualizada.apartamento_actual_id == apto.id


def test_persona_sin_apartamento_es_valida(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    # Registrar una Persona no exige Apartamento: la FK queda nula.
    assert persona.apartamento_actual_id is None


def test_desvincular_apartamento_pone_nulo(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    get_or_create_persona(db_session, "3001234567", "Ana")
    set_apartamento_actual(db_session, "3001234567", apto)

    desvinculada = set_apartamento_actual(db_session, "3001234567", None)

    assert desvinculada.apartamento_actual_id is None


def test_set_apartamento_actual_persona_inexistente_lanza(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")

    # No hay Persona con ese teléfono y este servicio no la crea (no recibe nombre).
    with pytest.raises(LookupError):
        set_apartamento_actual(db_session, "3009999999", apto)
