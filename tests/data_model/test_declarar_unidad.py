# -*- coding: utf-8 -*-
"""
Seam A — Declarar unidad + herencia de apartamento (ticket 05).

El staff declara una unidad a propósito (varios teléfonos de un mismo
Apartamento) y todos HEREDAN ese Apartamento de una vez. Un "a nombre de" casual
al anunciar NO agrupa a nadie. Cualquier herencia errónea es CORREGIBLE mudando
al teléfono afectado, sin tocar a los demás.
"""

import pytest

from app.domain.apartamento_service import (
    declare_unit,
    get_or_create_apartamento,
    move_resident,
    set_apartamento_actual,
)
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona

pytestmark = pytest.mark.integration


def _apto_actual(session, telefono_canonico):
    return (
        session.query(Persona)
        .filter(Persona.telefono == telefono_canonico)
        .one()
        .apartamento_actual_id
    )


def test_declarar_unidad_agrupa_a_todos_los_telefonos(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    personas = declare_unit(
        db_session,
        apto,
        [("3001234567", "Ana"), ("3019999999", "Beto"), ("3025555555", "Cira")],
    )

    assert all(p.apartamento_actual_id == apto.id for p in personas)
    assert _apto_actual(db_session, "+573001234567") == apto.id
    assert _apto_actual(db_session, "+573019999999") == apto.id
    assert _apto_actual(db_session, "+573025555555") == apto.id


def test_declarar_unidad_registra_a_los_miembros_nuevos(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    declare_unit(db_session, apto, [("3001234567", "Ana")])

    # 'Ana' quedó registrada como Persona por su teléfono.
    assert (
        db_session.query(Persona)
        .filter(Persona.telefono == "+573001234567")
        .one_or_none()
        is not None
    )


def test_a_nombre_de_casual_en_announce_no_agrupa_apartamentos(db_session):
    # Ana (con apartamento) anuncia a nombre de Beto (registrado, sin apartamento).
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto_ana = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    set_apartamento_actual(db_session, "3001234567", apto_ana)
    get_or_create_persona(db_session, "3019999999", "Beto")

    announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.persona_registrada("3019999999"),
    )

    # El favor puntual NO hace que Beto herede el apartamento de Ana.
    assert _apto_actual(db_session, "+573019999999") is None
    # Ana sigue en el suyo.
    assert _apto_actual(db_session, "+573001234567") == apto_ana.id


def test_herencia_corregible_sin_afectar_a_los_demas(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")
    declare_unit(db_session, apto, [("3001234567", "Ana"), ("3019999999", "Beto")])

    # Herencia errónea de Beto: se corrige mudándolo a otra torre.
    otro = get_or_create_apartamento(db_session, "Las Flores", "Torre Z", "999")
    move_resident(db_session, "3019999999", otro)

    assert _apto_actual(db_session, "+573019999999") == otro.id
    # Ana no se ve afectada por la corrección de Beto.
    assert _apto_actual(db_session, "+573001234567") == apto.id
