# -*- coding: utf-8 -*-
"""
Seam A — corregir destinatario de un Paquete en `ESTADOS_CORREGIBLES`
(`ANUNCIADO`/`RECIBIDO`/`ENTREGADO`, excepción acotada a ADR-0001, ver
paquete_lifecycle.py y .scratch/announce-staff-completo/spec.md). Ampliado
2026-08-16 (pedido explícito del cliente) de "solo ANUNCIADO" al conjunto
actual -- el typo del nombre anunciado no siempre se nota mientras el
paquete sigue anunciado.

Comportamiento observable: corrige en ANUNCIADO/RECIBIDO/ENTREGADO y
registra actor+timestamp; en CANCELADO (el único estado fuera del
conjunto), TransicionInvalida sin efecto.
"""

import pytest

from app.domain.paquete_lifecycle import (
    TransicionInvalida,
    cancel,
    corregir_destinatario,
    deliver,
    receive,
)
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _anunciar(session, tel="3001234567", nombre="Jesu Peres"):
    p = announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.declarado_por_cliente(nombre),
    )
    session.flush()
    return p


def _staff(session):
    return create_initial_admin(session, "admin@club.com", "Admin", _PW)


def test_corregir_en_anunciado_actualiza_nombre_y_registra_actor(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)

    corregir_destinatario(db_session, p, staff, "Jesús Pérez")

    assert p.recipient_name == "JESÚS PÉREZ"
    assert p.corrected_by_usuario_id == staff.id
    assert p.corrected_at is not None
    # El estado no cambia por corregir.
    from app.domain.paquete import EstadoPaquete

    assert p.estado == EstadoPaquete.ANUNCIADO


def test_corregir_tambien_actualiza_telefono_si_se_pasa(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)

    corregir_destinatario(db_session, p, staff, "Jesús Pérez", "3021112233")

    assert p.recipient_phone == "+573021112233"


def test_corregir_sin_telefono_no_toca_el_existente(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)
    telefono_original = p.recipient_phone

    corregir_destinatario(db_session, p, staff, "Jesús Pérez")

    assert p.recipient_phone == telefono_original


def test_corregir_nombre_vacio_falla_y_no_muta(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)
    nombre_original = p.recipient_name

    with pytest.raises(ValueError):
        corregir_destinatario(db_session, p, staff, "   ")

    assert p.recipient_name == nombre_original


def test_corregir_en_recibido_se_permite(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)

    corregir_destinatario(db_session, p, staff, "Otro Nombre")

    assert p.recipient_name == "OTRO NOMBRE"
    assert p.corrected_by_usuario_id == staff.id
    from app.domain.paquete import EstadoPaquete

    assert p.estado == EstadoPaquete.RECIBIDO  # corregir no cambia el estado


def test_corregir_en_entregado_se_permite(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)

    corregir_destinatario(db_session, p, staff, "Otro Nombre")

    assert p.recipient_name == "OTRO NOMBRE"
    from app.domain.paquete import EstadoPaquete

    assert p.estado == EstadoPaquete.ENTREGADO  # corregir no cambia el estado


def test_corregir_en_cancelado_falla_sin_efecto(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)
    cancel(db_session, p, staff, "OTRO")

    with pytest.raises(TransicionInvalida):
        corregir_destinatario(db_session, p, staff, "Otro Nombre")
