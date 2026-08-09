# -*- coding: utf-8 -*-
"""
Seam A/B — `personas.telefono` pasa a nullable; nueva constraint "Teléfono o
WhatsApp, nunca los dos vacíos" + índice único parcial sobre `whatsapp_usuario`
(`.scratch/announce-rapido`, ticket 01).

Se prueba el efecto observable de la migración (qué inserciones acepta o
rechaza la base), no la forma interna del constraint/índice -- mismo espíritu
que `test_apartamento_seed.py`.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from app.domain.persona import Persona

pytestmark = pytest.mark.integration


def _insertar(session, *, telefono=None, whatsapp_usuario=None):
    persona = Persona(
        id=uuid.uuid4(),
        telefono=telefono,
        whatsapp_usuario=whatsapp_usuario,
        nombre="ANA",
    )
    session.add(persona)
    session.flush()
    return persona


def test_solo_telefono_sigue_funcionando(db_session):
    persona = _insertar(db_session, telefono="+573001234567")
    assert persona.telefono == "+573001234567"
    assert persona.whatsapp_usuario is None


def test_solo_whatsapp_usuario_sin_telefono_funciona(db_session):
    persona = _insertar(db_session, whatsapp_usuario="ana.whats")
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "ana.whats"


def test_ambos_vacios_viola_la_constraint(db_session):
    with pytest.raises(IntegrityError):
        _insertar(db_session)
    db_session.rollback()


def test_whatsapp_usuario_duplicado_viola_el_indice_unico(db_session):
    _insertar(db_session, whatsapp_usuario="ana.whats")
    with pytest.raises(IntegrityError):
        _insertar(db_session, whatsapp_usuario="ana.whats")
    db_session.rollback()


def test_dos_personas_sin_whatsapp_usuario_no_chocan(db_session):
    # El índice único es PARCIAL (`WHERE whatsapp_usuario IS NOT NULL`) --
    # varias filas con `whatsapp_usuario IS NULL` conviven sin problema,
    # mismo criterio que ya usa `uq_ocupantes_principal_por_apartamento`.
    _insertar(db_session, telefono="+573001234567")
    _insertar(db_session, telefono="+573019999999")

    total = db_session.execute(text("SELECT COUNT(*) FROM personas")).scalar()
    assert total == 2
