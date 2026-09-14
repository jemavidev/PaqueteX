# -*- coding: utf-8 -*-
"""
Seam 1 — núcleo del saldo contra entrega (.scratch/dinero-contra-entrega,
ticket 01), contra el Postgres efímero construido con `alembic upgrade head`.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega_service import (
    listar_movimientos_saldo,
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


def test_listar_movimientos_saldo_mezcla_varias_personas_mas_reciente_primero(db_session):
    # .scratch/dinero-contra-entrega-control, ticket 02: ledger global, a
    # diferencia de `movimientos_de_persona` (acotada a una sola).
    staff = _usuario(db_session)
    ana = get_or_create_persona(db_session, "3001111111", "Ana")
    beto = get_or_create_persona(db_session, "3002222222", "Beto")
    registrar_movimiento_saldo(db_session, ana.id, 1000, staff)
    registrar_movimiento_saldo(db_session, beto.id, 2000, staff)
    registrar_movimiento_saldo(db_session, ana.id, -500, staff)
    db_session.commit()

    movimientos, total_paginas = listar_movimientos_saldo(db_session)

    assert total_paginas == 1
    assert [m.monto for m in movimientos] == [-500, 2000, 1000]
    assert {m.persona_id for m in movimientos} == {ana.id, beto.id}
    # Resueltos en batch (ver docstring): nombre de la Persona y de quién
    # de staff lo registró, sin necesitar una consulta aparte.
    assert {m.persona_nombre for m in movimientos} == {"ANA", "BETO"}
    assert all(m.registrado_por_nombre == staff.nombre for m in movimientos)


def test_listar_movimientos_saldo_filtra_por_termino_de_busqueda(db_session):
    staff = _usuario(db_session)
    ana = get_or_create_persona(db_session, "3001111111", "Ana")
    beto = get_or_create_persona(db_session, "3002222222", "Beto")
    registrar_movimiento_saldo(db_session, ana.id, 1000, staff)
    registrar_movimiento_saldo(db_session, beto.id, 2000, staff)
    db_session.commit()

    movimientos, _ = listar_movimientos_saldo(db_session, q="Ana")

    assert {m.persona_id for m in movimientos} == {ana.id}


def test_listar_movimientos_saldo_filtra_ingreso_vs_egreso(db_session):
    staff = _usuario(db_session)
    ana = get_or_create_persona(db_session, "3001111111", "Ana")
    registrar_movimiento_saldo(db_session, ana.id, 1000, staff)
    registrar_movimiento_saldo(db_session, ana.id, -300, staff)
    db_session.commit()

    ingresos, _ = listar_movimientos_saldo(db_session, tipo="ingreso")
    egresos, _ = listar_movimientos_saldo(db_session, tipo="egreso")

    assert [m.monto for m in ingresos] == [1000]
    assert [m.monto for m in egresos] == [-300]


def test_listar_movimientos_saldo_incluye_paquete_asociado(db_session):
    staff = _usuario(db_session)
    ana = get_or_create_persona(db_session, "3001111111", "Ana")
    paquete = announce(
        db_session,
        anunciante_telefono="3001111111",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    registrar_movimiento_saldo(db_session, ana.id, 1000, staff, paquete_id=paquete.id)
    registrar_movimiento_saldo(db_session, ana.id, 500, staff)
    db_session.commit()

    movimientos, _ = listar_movimientos_saldo(db_session)

    con_paquete = next(m for m in movimientos if m.monto == 1000)
    sin_paquete = next(m for m in movimientos if m.monto == 500)
    assert con_paquete.paquete_access_code == paquete.access_code
    assert sin_paquete.paquete_access_code is None


def test_listar_movimientos_saldo_pagina(db_session):
    staff = _usuario(db_session)
    ana = get_or_create_persona(db_session, "3001111111", "Ana")
    for i in range(25):
        registrar_movimiento_saldo(db_session, ana.id, 100 + i, staff)
    db_session.commit()

    pagina_1, total_paginas = listar_movimientos_saldo(db_session, pagina=1)
    pagina_2, _ = listar_movimientos_saldo(db_session, pagina=2)

    assert total_paginas == 2
    assert len(pagina_1) == 20
    assert len(pagina_2) == 5


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
