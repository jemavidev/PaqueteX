# -*- coding: utf-8 -*-
"""
Seam A — resolución de Teléfonos de todos los Ocupantes activos del
Apartamento de una Persona (`.scratch/mis-paquetes-vista-apartamento`,
issue 01). Base para ampliar el alcance de `/mis-paquetes` a toda la unidad.

Comportamiento observable: sin Apartamento, solo el propio teléfono; con
Apartamento, los teléfonos de todos los Ocupantes ACTIVOS con Teléfono
propio (nunca los dados de baja, nunca aporta nada un Ocupante sin Teléfono).
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import (
    agregar_ocupante,
    dar_de_baja_ocupante,
    telefonos_activos_del_apartamento_de,
)
from app.domain.persona_service import get_or_create_persona

pytestmark = pytest.mark.integration


def _apto(session):
    return resolver_apartamento(session, "TORRE 1", "101")


def test_persona_sin_apartamento_devuelve_solo_su_propio_telefono(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    assert telefonos_activos_del_apartamento_de(db_session, persona) == [
        persona.telefono
    ]


def test_con_apartamento_trae_telefonos_de_todos_los_ocupantes_activos(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Ana", telefono="3001111111")
    agregar_ocupante(db_session, apto, "Beto", telefono="3002222222")
    db_session.flush()

    persona_principal = get_or_create_persona(db_session, "3001111111", "Ana")

    telefonos = telefonos_activos_del_apartamento_de(db_session, persona_principal)

    assert set(telefonos) == {"+573001111111", "+573002222222"}


def test_ocupante_sin_telefono_no_aporta_nada(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Ana", telefono="3001111111")
    agregar_ocupante(db_session, apto, "Hijo sin teléfono")  # sin teléfono
    db_session.flush()

    persona_principal = get_or_create_persona(db_session, "3001111111", "Ana")

    telefonos = telefonos_activos_del_apartamento_de(db_session, persona_principal)

    assert telefonos == ["+573001111111"]


def test_ocupante_dado_de_baja_no_aparece(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Ana", telefono="3001111111")
    secundario = agregar_ocupante(db_session, apto, "Beto", telefono="3002222222")
    dar_de_baja_ocupante(db_session, secundario)
    db_session.flush()

    persona_principal = get_or_create_persona(db_session, "3001111111", "Ana")

    telefonos = telefonos_activos_del_apartamento_de(db_session, persona_principal)

    assert telefonos == ["+573001111111"]
