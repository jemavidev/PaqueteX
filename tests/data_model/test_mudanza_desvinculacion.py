# -*- coding: utf-8 -*-
"""
Seam A — Mudanza / desvinculación y la INMUTABILIDAD del snapshot (ticket 04,
ADR-0001).

Una Persona puede mudarse a otro Apartamento o desvincularse en cualquier
momento (`move_resident`), y hacerlo NUNCA reescribe los paquetes que ya anunció:
los paquetes viejos siguen mostrando el apartamento de entonces. Es el invariante
central del rebuild.
"""

import pytest

from app.domain.apartamento_service import (
    resolver_apartamento,
    move_resident,
    set_apartamento_actual,
)
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona

pytestmark = pytest.mark.integration

CANON = "+573001234567"


def _ana(session):
    return session.query(Persona).filter(Persona.telefono == CANON).one()


def test_move_resident_cambia_el_apartamento_actual(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    a = resolver_apartamento(db_session, "TORRE 1", "101")
    move_resident(db_session, "3001234567", a)
    assert _ana(db_session).apartamento_actual_id == a.id

    b = resolver_apartamento(db_session, "TORRE 2", "202")
    move_resident(db_session, "3001234567", b)
    assert _ana(db_session).apartamento_actual_id == b.id


def test_desvincular_pone_el_apartamento_en_nulo(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    a = resolver_apartamento(db_session, "TORRE 1", "101")
    move_resident(db_session, "3001234567", a)

    move_resident(db_session, "3001234567", None)
    assert _ana(db_session).apartamento_actual_id is None


def test_mudar_no_reescribe_el_snapshot_de_un_paquete_ya_anunciado(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    viejo = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", viejo)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    nuevo = resolver_apartamento(db_session, "TORRE 4", "404")
    move_resident(db_session, "3001234567", nuevo)
    db_session.refresh(paquete)

    # El paquete conserva el apartamento de ENTONCES, no el nuevo.
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 1", "101")


def test_desvincular_tampoco_reescribe_el_snapshot(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    viejo = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", viejo)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )

    move_resident(db_session, "3001234567", None)  # desvincular
    db_session.refresh(paquete)

    assert _ana(db_session).apartamento_actual_id is None
    # El snapshot del paquete viejo sigue intacto pese a la desvinculación.
    assert (
        paquete.snapshot_conjunto,
        paquete.snapshot_torre,
        paquete.snapshot_apartamento,
    ) == ("EL CLUB", "TORRE 1", "101")
