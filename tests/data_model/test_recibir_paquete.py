# -*- coding: utf-8 -*-
"""
Seam A — Recibir un Paquete (ANUNCIADO → RECIBIDO) + infra de transiciones.

Comportamiento observable: el estado resultante, el actor/timestamp registrados,
la Guía opcional, y que recibir un no-`ANUNCIADO` se rechace sin efecto.
"""

import pytest

from app.domain.paquete import CondicionPaquete, EstadoPaquete, TipoPaquete
from app.domain.paquete_lifecycle import TransicionInvalida, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session, nombre="Operador", rol=RolUsuario.OPERADOR) -> Usuario:
    u = Usuario(nombre=nombre, rol=rol)
    session.add(u)
    session.flush()  # asigna el id (actor de la sesión real)
    return u


def _anunciar(session, tel="3001234567", nombre="Ana"):
    return announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )


def test_recibir_un_anunciado_lo_pasa_a_recibido(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    receive(db_session, p, op)

    assert p.estado == EstadoPaquete.RECIBIDO
    assert p.received_at is not None
    assert p.received_by_usuario_id == op.id


def test_recibir_captura_la_guia_opcional(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    receive(db_session, p, op, guide_number="1Z-ABC-123")

    assert p.guide_number == "1Z-ABC-123"
    assert p.estado == EstadoPaquete.RECIBIDO


def test_recibir_sin_guia_es_valido(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    receive(db_session, p, op)

    assert p.guide_number is None
    assert p.estado == EstadoPaquete.RECIBIDO


def test_recibir_un_no_anunciado_se_rechaza_sin_efecto(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, op)  # ya RECIBIDO

    received_at_antes = p.received_at
    with pytest.raises(TransicionInvalida):
        receive(db_session, p, op)  # segundo intento: origen no es ANUNCIADO

    # Sin efecto: el estado y el timestamp del primer recibo no cambian.
    assert p.estado == EstadoPaquete.RECIBIDO
    assert p.received_at == received_at_antes


def test_recibir_con_tipo_y_condicion_explicitos_los_persiste(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    receive(
        db_session,
        p,
        op,
        package_type=TipoPaquete.EXTRA_DIMENSIONADO,
        package_condition=CondicionPaquete.ABIERTO,
    )

    assert p.package_type == TipoPaquete.EXTRA_DIMENSIONADO
    assert p.package_condition == CondicionPaquete.ABIERTO


def test_recibir_sin_tipo_ni_condicion_usa_los_defaults(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    receive(db_session, p, op)

    assert p.package_type == TipoPaquete.NORMAL
    assert p.package_condition == CondicionPaquete.BUENO
