# -*- coding: utf-8 -*-
"""
Seam A — candidatos de "Corregir" (Grupo 16, Ronda 2).

Comportamiento observable: los Ocupantes del Apartamento del snapshot más el
Anunciante, únicos por (nombre, teléfono); sin Apartamento resuelto, solo el
Anunciante; nunca crea un Apartamento por accidente al consultar.
"""

import pytest

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import get_or_create_apartamento
from app.domain.ocupante_service import agregar_ocupante
from app.domain.paquete_correccion_service import candidatos_correccion
from app.domain.paquete_service import Destinatario, announce

pytestmark = pytest.mark.integration


def _anunciar(session, tel="3001234567", nombre="Ana", apartamento=None):
    return announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
        apartamento=apartamento,
    )


def test_sin_apartamento_en_el_snapshot_solo_trae_al_anunciante(db_session):
    p = _anunciar(db_session, nombre="Ana")

    candidatos = candidatos_correccion(db_session, p)

    assert candidatos == [{"nombre": "Ana", "telefono": "+573001234567"}]


def test_con_apartamento_trae_ocupantes_mas_el_anunciante(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "A", "101")
    agregar_ocupante(db_session, apto, "Papá", "3011111111")
    agregar_ocupante(db_session, apto, "Mamá")  # sin teléfono
    db_session.commit()

    p = _anunciar(db_session, tel="3022222222", nombre="Visitante", apartamento=apto)

    candidatos = candidatos_correccion(db_session, p)

    assert {"nombre": "Papá", "telefono": "+573011111111"} in candidatos
    assert {"nombre": "Mamá", "telefono": None} in candidatos
    assert {"nombre": "Visitante", "telefono": "+573022222222"} in candidatos
    assert len(candidatos) == 3


def test_no_duplica_si_el_anunciante_es_tambien_ocupante(db_session):
    apto = get_or_create_apartamento(db_session, "Las Flores", "A", "101")
    agregar_ocupante(db_session, apto, "Ana", "3001234567")
    db_session.commit()

    p = _anunciar(db_session, tel="3001234567", nombre="Ana", apartamento=apto)

    candidatos = candidatos_correccion(db_session, p)

    assert candidatos == [{"nombre": "Ana", "telefono": "+573001234567"}]


def test_apartamento_del_snapshot_que_ya_no_existe_no_revienta(db_session):
    # Snapshot con una terna que nunca se materializó como Apartamento real
    # (p.ej. datos legados) -- no debe crear uno ni fallar, solo omitir esos
    # candidatos y caer al Anunciante.
    p = _anunciar(db_session, nombre="Ana")
    p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento = (
        "FANTASMA",
        "Z",
        "999",
    )
    db_session.flush()

    candidatos = candidatos_correccion(db_session, p)

    assert candidatos == [{"nombre": "Ana", "telefono": "+573001234567"}]
    assert db_session.query(Apartamento).count() == 0  # no se creó nada
