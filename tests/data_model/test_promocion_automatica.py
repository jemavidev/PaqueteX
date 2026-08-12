# -*- coding: utf-8 -*-
"""
Promoción automática a principal al recibir un paquete
(`.scratch/ocupante-principal-escenarios`, ticket 04).

Comportamiento observable: recibir el primer paquete de un residente de una
unidad sin principal lo promueve ahí mismo -- sin paso manual -- siempre que
tenga Teléfono o WhatsApp propio; si no, la unidad simplemente se queda sin
principal hasta que alguien con contacto reciba algo.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import (
    agregar_ocupante,
    confirmar_ocupante,
    promover_al_recibir,
    resolver_ocupante_de_paquete,
)
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

_PW = "Contrasena1"


def _apto(db_session, torre="TORRE 1", numero="101"):
    return resolver_apartamento(db_session, torre, numero)


def _staff(session):
    admin = session.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).first()
    if admin is not None:
        return admin
    return create_initial_admin(session, "admin@test.local", "Admin", _PW)


def _confirmar(session, apto, nombre, telefono=None):
    """Crea un Ocupante y lo confirma de inmediato (por staff) -- deja
    principal establecido a esa unidad."""
    ocupante = agregar_ocupante(session, apto, nombre, telefono)
    return confirmar_ocupante(session, ocupante, _staff(session))


def test_recibir_promueve_al_destinatario_resuelto_por_telefono(db_session):
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(ocupante)

    assert ocupante.es_principal is True
    assert ocupante.confirmado_en is not None


def test_recibir_no_promueve_si_el_resuelto_no_tiene_contacto_propio(db_session):
    """Unidad sin NADIE confirmado como principal todavía -- Ana (con
    teléfono, pending) y Hijo (sin contacto, pending) conviven ahí. Recibir
    un paquete a nombre de Hijo no lo promueve (no tiene con qué), y tampoco
    promueve a Ana (no fue el destinatario de este paquete puntual)."""
    apto = _apto(db_session)
    ana = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.ocupante(hijo.id),
        apartamento=apto,
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(ana)
    db_session.refresh(hijo)

    assert hijo.es_principal is False
    assert ana.es_principal is False


def test_recibir_no_cambia_nada_si_la_unidad_ya_tiene_principal(db_session):
    apto = _apto(db_session)
    principal = _confirmar(db_session, apto, "Papá", "3001234567")
    otro = agregar_ocupante(db_session, apto, "Otro", telefono="3007654321")

    paquete = announce(
        db_session,
        anunciante_telefono="3007654321",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(otro)
    db_session.refresh(principal)

    assert otro.es_principal is False
    assert principal.es_principal is True


def test_recibir_aplica_sin_importar_el_camino_de_anuncio(db_session):
    """El disparador mira el destinatario RESUELTO, no cómo se anunció --
    aplica igual si el anuncio fue Destinatario.yo_mismo() (Teléfono/
    WhatsApp directo o `/anunciar`) como si fue Destinatario.ocupante(id)
    (Torre+Apto de `/announce`)."""
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.ocupante(ocupante.id),
        apartamento=apto,
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(ocupante)

    assert ocupante.es_principal is True


def test_resolver_ocupante_de_paquete_por_telefono(db_session):
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apto,
    )
    resuelto = resolver_ocupante_de_paquete(db_session, paquete)

    assert resuelto is not None
    assert resuelto.id == ocupante.id


def test_resolver_ocupante_de_paquete_por_nombre_prioriza_sobre_telefono(db_session):
    """El destinatario (Hijo) no tiene contacto propio, así que
    `recipient_phone` cae al Anunciante (Ana -- ticket 10), NO queda `None`
    y NO es el teléfono de Hijo. Aun así, la resolución debe encontrar a
    Hijo (por nombre dentro del roster de la unidad), no a Ana -- resolver
    por teléfono ahí identificaría erróneamente a quien anunció."""
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.ocupante(hijo.id),
        apartamento=apto,
    )
    assert paquete.recipient_phone == "+573001234567"  # el de Ana (Anunciante), no None

    resuelto = resolver_ocupante_de_paquete(db_session, paquete)

    assert resuelto is not None
    assert resuelto.id == hijo.id


def test_resolver_ocupante_de_paquete_sin_apartamento_en_snapshot(db_session):
    paquete = announce(
        db_session,
        anunciante_telefono="3009999999",
        anunciante_nombre="Sin Unidad",
        destinatario=Destinatario.yo_mismo(),
    )
    assert resolver_ocupante_de_paquete(db_session, paquete) is None


def test_promover_al_recibir_no_falla_sin_resolucion_posible(db_session):
    paquete = announce(
        db_session,
        anunciante_telefono="3009999999",
        anunciante_nombre="Sin Unidad",
        destinatario=Destinatario.yo_mismo(),
    )
    assert promover_al_recibir(db_session, paquete) is None
