# -*- coding: utf-8 -*-
"""
Seam A — corregir el Apartamento (snapshot) de un Paquete `ANUNCIADO`, hermana
directa de `corregir_destinatario` (excepción acotada a ADR-0001). Ver
.scratch/asociacion-retroactiva-apartamento/spec.md e issues/01.

Comportamiento observable: corrige en ANUNCIADO y registra actor+timestamp;
en cualquier otro estado, TransicionInvalida sin efecto.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_lifecycle import (
    TransicionInvalida,
    cancel,
    corregir_apartamento,
    deliver,
    receive,
)
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _staff(session):
    return create_initial_admin(session, "admin@club.com", "Admin", _PW)


def _anunciar(session, tel="3001234567", nombre="Jesus Peres"):
    return announce(session, tel, nombre, Destinatario.yo_mismo())


def test_corregir_en_anunciado_actualiza_snapshot_y_registra_actor(db_session):
    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    p = _anunciar(db_session)
    assert p.snapshot_apartamento is None

    corregir_apartamento(db_session, p, staff, apto)

    # normalizar_terna sube a MAYUSCULAS -- literales independientes del
    # calculo de la propia implementacion, no `apto.*`.
    assert p.snapshot_conjunto == "EL CLUB"
    assert p.snapshot_torre == "TORRE 1"
    assert p.snapshot_apartamento == "101"
    assert p.corrected_by_usuario_id == staff.id
    assert p.corrected_at is not None
    assert p.estado == EstadoPaquete.ANUNCIADO


def test_corregir_no_toca_recipient_name_ni_phone(db_session):
    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    p = _anunciar(db_session)
    nombre_original = p.recipient_name
    telefono_original = p.recipient_phone

    corregir_apartamento(db_session, p, staff, apto)

    assert p.recipient_name == nombre_original
    assert p.recipient_phone == telefono_original


def test_corregir_sin_apartamento_falla_y_no_muta(db_session):
    staff = _staff(db_session)
    p = _anunciar(db_session)

    with pytest.raises(ValueError):
        corregir_apartamento(db_session, p, staff, None)

    assert p.snapshot_apartamento is None
    assert p.corrected_at is None


def test_corregir_en_recibido_falla_sin_efecto(db_session):
    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    p = _anunciar(db_session)
    receive(db_session, p, staff)

    with pytest.raises(TransicionInvalida):
        corregir_apartamento(db_session, p, staff, apto)

    assert p.snapshot_apartamento is None
    assert p.corrected_at is None


def test_corregir_en_entregado_falla_sin_efecto(db_session):
    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)

    with pytest.raises(TransicionInvalida):
        corregir_apartamento(db_session, p, staff, apto)


def test_corregir_en_cancelado_falla_sin_efecto(db_session):
    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    p = _anunciar(db_session)
    cancel(db_session, p, staff, "OTRO")

    with pytest.raises(TransicionInvalida):
        corregir_apartamento(db_session, p, staff, apto)
