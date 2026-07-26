# -*- coding: utf-8 -*-
"""
Seam A — Ocupante (ADR-0006), contra el Postgres efímero.

Comportamiento observable: un Apartamento con Ocupantes siempre tiene
exactamente 1 principal (con Teléfono real); promover exige Teléfono y degrada
al anterior en la misma transacción; listar ordena principal primero.
"""

import pytest

from app.domain.apartamento_service import get_or_create_apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    agregar_ocupante,
    listar_ocupantes,
    promover_a_principal,
)
from app.domain.persona import Persona

pytestmark = pytest.mark.integration


def _apto(db_session):
    return get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")


def test_primer_ocupante_sin_telefono_falla(db_session):
    apto = _apto(db_session)
    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "Mamá")


def test_primer_ocupante_con_telefono_queda_principal_automatico(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    assert papa.es_principal is True
    assert papa.persona_id is not None
    persona = db_session.get(Persona, papa.persona_id)
    assert persona.telefono == "+573001234567"


def test_segundo_ocupante_no_se_auto_promueve(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")  # sin teléfono

    assert mama.es_principal is False
    assert mama.persona_id is None


def test_ocupante_con_telefono_reutiliza_persona_existente(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3001234567")

    # Mismo teléfono => misma Persona (aunque sean Ocupantes distintos aquí no
    # aplicaría en la práctica, pero confirma que no duplica Personas).
    assert papa.persona_id == hija.persona_id


def test_promover_sin_telefono_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    with pytest.raises(ValueError):
        promover_a_principal(db_session, mama)


def test_promover_con_telefono_degrada_al_anterior(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    promover_a_principal(db_session, hija)
    db_session.refresh(papa)
    db_session.refresh(hija)

    assert hija.es_principal is True
    assert papa.es_principal is False
    # Nunca 0 ni 2 principales: exactamente 1.
    principales = [o for o in listar_ocupantes(db_session, apto) if o.es_principal]
    assert len(principales) == 1


def test_listar_ordena_principal_primero(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    agregar_ocupante(db_session, apto, "Mamá")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    ocupantes = listar_ocupantes(db_session, apto)
    assert len(ocupantes) == 3
    assert ocupantes[0].es_principal is True


def test_indice_unico_impide_dos_principales_a_nivel_de_bd(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    # Forzar la violación directamente (sin pasar por promover_a_principal)
    # confirma que el índice único parcial protege a nivel de base de datos,
    # no solo por disciplina de la función de servicio.
    hija.es_principal = True
    with pytest.raises(Exception):
        db_session.flush()
