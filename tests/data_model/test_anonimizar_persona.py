# -*- coding: utf-8 -*-
"""
Seam A — Anonimizar Persona (ADR-0005), contra el Postgres efímero.

Comportamiento observable: limpia los campos correctos, el teléfono queda
sintético y único, se desvincula del Apartamento, es idempotente, el snapshot
de un Paquete ya anunciado no cambia, y el teléfono real original vuelve a
resolver a una Persona NUEVA (el olvido es real, no cosmético).
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
from app.domain.paquete import Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import anonimizar_persona, get_or_create_persona

pytestmark = pytest.mark.integration


def test_anonimizar_limpia_los_campos_personales(db_session):
    p = get_or_create_persona(db_session, "3001234567", "Ana")
    p.email = "ana@x.com"
    p.documento = "123"
    p.tipo_documento = "CC"
    db_session.flush()

    anonimizar_persona(db_session, p)

    assert p.nombre == "Cliente eliminado"
    assert p.email is None
    assert p.documento is None
    assert p.tipo_documento is None
    assert p.eliminado_en is not None


def test_telefono_queda_sintetico_y_unico(db_session):
    p1 = get_or_create_persona(db_session, "3001234567", "Ana")
    p2 = get_or_create_persona(db_session, "3019999999", "Beto")

    anonimizar_persona(db_session, p1)
    anonimizar_persona(db_session, p2)

    assert p1.telefono.startswith("DEL-") and p2.telefono.startswith("DEL-")
    assert p1.telefono != p2.telefono
    assert not p1.telefono.startswith("+57")


def test_desvincula_del_apartamento(db_session):
    p = get_or_create_persona(db_session, "3001234567", "Ana")
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto)

    anonimizar_persona(db_session, p)

    assert p.apartamento_actual_id is None


def test_es_idempotente(db_session):
    p = get_or_create_persona(db_session, "3001234567", "Ana")
    anonimizar_persona(db_session, p)
    telefono_tras_primera = p.telefono
    eliminado_en_tras_primera = p.eliminado_en

    anonimizar_persona(db_session, p)  # segunda vez: no-op

    assert p.telefono == telefono_tras_primera
    assert p.eliminado_en == eliminado_en_tras_primera


def test_snapshot_de_paquete_ya_anunciado_no_cambia(db_session):
    get_or_create_persona(db_session, "3001234567", "Ana")
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    set_apartamento_actual(db_session, "3001234567", apto)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    paquete_id = paquete.id

    persona = db_session.query(Persona).filter(Persona.telefono == "+573001234567").one()
    anonimizar_persona(db_session, persona)

    db_session.refresh(paquete)
    assert paquete.recipient_name == "ANA"
    assert paquete.announced_by_phone == "+573001234567"
    assert (paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento) == (
        "EL CLUB",
        "TORRE 1",
        "101",
    )
    # El Paquete anonimizado sigue siendo, literalmente, ese mismo Paquete.
    assert db_session.get(Paquete, paquete_id) is paquete


def test_reanunciar_con_el_telefono_real_crea_persona_nueva(db_session):
    p1 = get_or_create_persona(db_session, "3001234567", "Ana")
    anonimizar_persona(db_session, p1)

    p2 = get_or_create_persona(db_session, "3001234567", "Ana Nueva")

    assert p2.id != p1.id
    assert p2.telefono == "+573001234567"
    assert p1.telefono != p2.telefono
