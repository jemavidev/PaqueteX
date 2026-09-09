# -*- coding: utf-8 -*-
"""
Seam A — bloqueo reversible de una Persona (.scratch/bloquear-clientes,
ticket 01), contra el Postgres efímero construido con `alembic upgrade head`.
"""

import pytest

from app.domain.persona_service import (
    autorizar_desbloqueo,
    bloquear_persona,
    get_or_create_persona,
)

pytestmark = pytest.mark.integration


def _persona(session, tel="3001234567"):
    return get_or_create_persona(session, tel, "Ana")


def test_bloquear_exige_motivo(db_session):
    persona = _persona(db_session)
    with pytest.raises(ValueError):
        bloquear_persona(db_session, persona, "")

    db_session.refresh(persona)
    assert persona.bloqueado_en is None


def test_bloquear_con_motivo_marca_el_estado(db_session):
    persona = _persona(db_session)

    bloquear_persona(db_session, persona, "Incumplimiento de términos")

    assert persona.bloqueado_en is not None
    assert persona.motivo_bloqueo == "Incumplimiento de términos"
    assert persona.desbloqueo_autorizado_en is None


def test_bloquear_dos_veces_es_idempotente(db_session):
    persona = _persona(db_session)
    bloquear_persona(db_session, persona, "Motivo A")
    primera_fecha = persona.bloqueado_en

    bloquear_persona(db_session, persona, "Motivo B")

    assert persona.bloqueado_en == primera_fecha
    assert persona.motivo_bloqueo == "Motivo A"


def test_autorizar_desbloqueo_sin_estar_bloqueada_se_rechaza(db_session):
    persona = _persona(db_session)
    with pytest.raises(ValueError):
        autorizar_desbloqueo(db_session, persona)


def test_autorizar_desbloqueo_marca_el_estado(db_session):
    persona = _persona(db_session)
    bloquear_persona(db_session, persona, "Motivo A")

    autorizar_desbloqueo(db_session, persona)

    assert persona.desbloqueo_autorizado_en is not None
    assert persona.bloqueado_en is not None  # sigue bloqueado para paquetes
