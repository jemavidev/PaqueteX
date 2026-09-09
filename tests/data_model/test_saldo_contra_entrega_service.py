# -*- coding: utf-8 -*-
"""
Seam 1 — núcleo del saldo contra entrega (.scratch/dinero-contra-entrega,
ticket 01), contra el Postgres efímero construido con `alembic upgrade head`.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega_service import (
    personas_con_historial_en_apartamento,
    registrar_movimiento_saldo,
    saldo_de_persona,
)
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session) -> Usuario:
    u = Usuario(nombre="Operador", rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def test_registrar_movimiento_positivo_suma_al_saldo(db_session):
    staff = _usuario(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    registrar_movimiento_saldo(db_session, persona.id, 5000, staff)

    assert saldo_de_persona(db_session, persona.id) == 5000


def test_registrar_movimiento_negativo_resta_y_puede_quedar_en_negativo(db_session):
    staff = _usuario(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    registrar_movimiento_saldo(db_session, persona.id, 5000, staff)

    registrar_movimiento_saldo(db_session, persona.id, -8000, staff)

    assert saldo_de_persona(db_session, persona.id) == -3000


def test_saldo_suma_varios_movimientos(db_session):
    staff = _usuario(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    registrar_movimiento_saldo(db_session, persona.id, 3000, staff)
    registrar_movimiento_saldo(db_session, persona.id, 2000, staff)
    registrar_movimiento_saldo(db_session, persona.id, -1000, staff)

    assert saldo_de_persona(db_session, persona.id) == 4000


def test_persona_sin_movimientos_tiene_saldo_cero(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    assert saldo_de_persona(db_session, persona.id) == 0


def test_movimiento_guarda_el_paquete_asociado_si_se_pasa(db_session):
    staff = _usuario(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    mov = registrar_movimiento_saldo(db_session, persona.id, 1000, staff, paquete_id=None)
    assert mov.paquete_id is None
    assert mov.registrado_por_usuario_id == staff.id


def test_personas_con_historial_en_apartamento_actual(db_session):
    staff = _usuario(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    con_historial = get_or_create_persona(db_session, "3001111111", "Con historial")
    sin_historial = get_or_create_persona(db_session, "3002222222", "Sin historial")
    set_apartamento_actual(db_session, con_historial.telefono, apto)
    set_apartamento_actual(db_session, sin_historial.telefono, apto)
    registrar_movimiento_saldo(db_session, con_historial.id, 1000, staff)
    db_session.commit()

    resultado = personas_con_historial_en_apartamento(db_session, apto.id)

    ids = {p.id for p in resultado}
    assert con_historial.id in ids
    assert sin_historial.id not in ids


def test_personas_con_historial_no_incluye_otro_apartamento(db_session):
    staff = _usuario(db_session)
    apto_a = resolver_apartamento(db_session, "TORRE 1", "101")
    apto_b = resolver_apartamento(db_session, "TORRE 1", "102")
    persona = get_or_create_persona(db_session, "3001111111", "Ana")
    set_apartamento_actual(db_session, persona.telefono, apto_a)
    registrar_movimiento_saldo(db_session, persona.id, 1000, staff)
    db_session.commit()

    resultado = personas_con_historial_en_apartamento(db_session, apto_b.id)
    assert resultado == []


def test_personas_con_historial_usa_apartamento_actual_no_uno_viejo(db_session):
    staff = _usuario(db_session)
    apto_viejo = resolver_apartamento(db_session, "TORRE 1", "101")
    apto_nuevo = resolver_apartamento(db_session, "TORRE 1", "102")
    persona = get_or_create_persona(db_session, "3001111111", "Ana")
    set_apartamento_actual(db_session, persona.telefono, apto_viejo)
    registrar_movimiento_saldo(db_session, persona.id, 1000, staff)
    db_session.commit()

    set_apartamento_actual(db_session, persona.telefono, apto_nuevo)
    db_session.commit()

    assert personas_con_historial_en_apartamento(db_session, apto_viejo.id) == []
    ids = {p.id for p in personas_con_historial_en_apartamento(db_session, apto_nuevo.id)}
    assert persona.id in ids
