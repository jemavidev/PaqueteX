# -*- coding: utf-8 -*-
"""
Seam A — Cancelar un Paquete ({ANUNCIADO | RECIBIDO} → CANCELADO).

Comportamiento observable: el estado terminal resultante, el actor/timestamp/motivo
registrados, que el motivo sea obligatorio, y que cancelar un estado terminal se
rechace sin efecto.
"""

import pytest

from app.domain.paquete import EstadoPaquete
from app.domain.paquete_lifecycle import (
    TransicionInvalida,
    cancel,
    deliver,
    receive,
)
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


def test_cancelar_desde_anunciado(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    cancel(db_session, p, op, "Anuncio erróneo")

    assert p.estado == EstadoPaquete.CANCELADO
    assert p.cancelled_at is not None
    assert p.cancelled_by_usuario_id == op.id
    assert p.cancel_reason == "Anuncio erróneo"


def test_cancelar_desde_recibido(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, op)

    cancel(db_session, p, op, "Devuelto al transportador")

    assert p.estado == EstadoPaquete.CANCELADO
    assert p.cancel_reason == "Devuelto al transportador"


def test_cancelar_sin_motivo_lanza_valueerror_sin_efecto(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    with pytest.raises(ValueError):
        cancel(db_session, p, op, None)

    # Sin efecto: sigue ANUNCIADO, sin motivo ni timestamp.
    assert p.estado == EstadoPaquete.ANUNCIADO
    assert p.cancel_reason is None
    assert p.cancelled_at is None


def test_cancelar_con_motivo_vacio_lanza_valueerror(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)

    with pytest.raises(ValueError):
        cancel(db_session, p, op, "   ")

    assert p.estado == EstadoPaquete.ANUNCIADO


def test_cancelar_un_entregado_se_rechaza_sin_efecto(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, op)
    deliver(db_session, p, op)  # ENTREGADO (terminal)

    with pytest.raises(TransicionInvalida):
        cancel(db_session, p, op, "Otro")

    # Sin efecto: sigue ENTREGADO, sin motivo de cancelación.
    assert p.estado == EstadoPaquete.ENTREGADO
    assert p.cancel_reason is None


def test_cancelar_dos_veces_se_rechaza_sin_efecto(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    cancel(db_session, p, op, "No reclamado")

    reason_antes = p.cancel_reason
    with pytest.raises(TransicionInvalida):
        cancel(db_session, p, op, "Otro")

    # Sin efecto: conserva el motivo original.
    assert p.estado == EstadoPaquete.CANCELADO
    assert p.cancel_reason == reason_antes == "No reclamado"
