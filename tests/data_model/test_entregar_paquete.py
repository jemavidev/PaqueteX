# -*- coding: utf-8 -*-
"""
Seam A — Entregar un Paquete (RECIBIDO → ENTREGADO).

Comportamiento observable: el estado resultante, el actor/timestamp de entrega, y
que entregar algo que no está RECIBIDO se rechace sin efecto. El destinatario
snapshot sigue legible para confirmar quién retira.
"""

import pytest

from app.domain.paquete import EstadoPaquete
from app.domain.paquete_lifecycle import TransicionInvalida, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session, nombre="Operador", rol=RolUsuario.OPERADOR) -> Usuario:
    u = Usuario(nombre=nombre, rol=rol)
    session.add(u)
    session.flush()
    return u


def _anunciar(session, tel="3001234567", nombre="Ana"):
    return announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )


def test_entregar_un_recibido_lo_pasa_a_entregado(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, op)

    deliver(db_session, p, op)

    assert p.estado == EstadoPaquete.ENTREGADO
    assert p.delivered_at is not None
    assert p.delivered_by_usuario_id == op.id


def test_el_destinatario_snapshot_sigue_legible_al_entregar(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session, nombre="Ana")
    receive(db_session, p, op)

    deliver(db_session, p, op)

    # El snapshot congelado al anunciar permite confirmar a quién se entrega.
    assert p.recipient_name == "ANA"
    assert p.announced_by_phone == "+573001234567"


def test_entregar_un_anunciado_todavia_no_recibido_se_rechaza(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)  # sigue ANUNCIADO

    with pytest.raises(TransicionInvalida):
        deliver(db_session, p, op)

    assert p.estado == EstadoPaquete.ANUNCIADO
    assert p.delivered_at is None


def test_entregar_dos_veces_se_rechaza_sin_efecto(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, op)
    deliver(db_session, p, op)  # ENTREGADO (terminal)

    delivered_at_antes = p.delivered_at
    with pytest.raises(TransicionInvalida):
        deliver(db_session, p, op)

    # Sin efecto: sigue ENTREGADO con su timestamp original.
    assert p.estado == EstadoPaquete.ENTREGADO
    assert p.delivered_at == delivered_at_antes
