# -*- coding: utf-8 -*-
"""
Seam A — Servicio de dominio `get_or_create_persona`, contra el Postgres
efímero construido con `alembic upgrade head`.

Se prueba comportamiento externo observable (crear / reutilizar / normalizar),
no nombres de columna ni internals de SQLAlchemy. La unicidad del Teléfono se
verifica por su efecto: dos anuncios → UNA sola Persona.
"""

import pytest

from app.domain.persona import Persona
from app.domain.persona_service import (
    cambiar_telefono_propio,
    get_or_create_persona,
    set_autoriza_recepcion_automatica,
    update_datos_personales,
    url_llamada,
    url_whatsapp,
)

pytestmark = pytest.mark.integration


def _total_personas(session) -> int:
    return session.query(Persona).count()


def test_telefono_nuevo_crea_persona(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    assert persona.id is not None
    assert persona.nombre == "ANA"
    assert persona.telefono == "+573001234567"  # persistida en forma canónica
    assert _total_personas(db_session) == 1


def test_mismo_telefono_reutiliza_la_misma_persona_sin_duplicar(db_session):
    primera = get_or_create_persona(db_session, "3001234567", "Ana")
    otra_vez = get_or_create_persona(db_session, "3001234567", "Ana María")

    assert otra_vez.id == primera.id
    assert _total_personas(db_session) == 1  # registro implícito, sin duplicados


def test_dos_formatos_del_mismo_numero_resuelven_a_una_persona(db_session):
    con_indicativo = get_or_create_persona(db_session, "+57 300 123 4567", "Ana")
    sin_indicativo = get_or_create_persona(db_session, "3001234567", "Ana")

    assert sin_indicativo.id == con_indicativo.id
    assert _total_personas(db_session) == 1


def test_telefonos_distintos_crean_personas_distintas(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    beto = get_or_create_persona(db_session, "3019999999", "Beto")

    assert ana.id != beto.id
    assert _total_personas(db_session) == 2


def test_unicidad_del_telefono_es_observable(db_session):
    # Formatos distintos del mismo número → una sola fila con el teléfono canónico.
    get_or_create_persona(db_session, "(301) 999-9999", "Beto")
    get_or_create_persona(db_session, "+573019999999", "Beto B")

    filas = (
        db_session.query(Persona)
        .filter(Persona.telefono == "+573019999999")
        .all()
    )
    assert len(filas) == 1


# --------------------------------------------------------------------------- #
# Ticket 12 (.scratch/mis-datos) — autorización automática de recepción.
# --------------------------------------------------------------------------- #
def test_autoriza_recepcion_automatica_desactivado_por_default(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    assert ana.autoriza_recepcion_automatica is False


def test_set_autoriza_recepcion_automatica(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    set_autoriza_recepcion_automatica(db_session, ana, True)
    assert ana.autoriza_recepcion_automatica is True

    set_autoriza_recepcion_automatica(db_session, ana, False)
    assert ana.autoriza_recepcion_automatica is False


# --------------------------------------------------------------------------- #
# `.scratch/pendientes-cliente/issues/35` — editar el propio teléfono.
# --------------------------------------------------------------------------- #
def test_cambiar_telefono_propio_renombra_la_misma_persona(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    ana_id = ana.id

    cambiar_telefono_propio(db_session, ana, "3009998877")

    assert ana.id == ana_id  # misma fila, no una Persona nueva
    assert ana.telefono == "+573009998877"


def test_cambiar_telefono_propio_al_mismo_numero_no_hace_nada(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    cambiar_telefono_propio(db_session, ana, "3001234567")
    assert ana.telefono == "+573001234567"


def test_cambiar_telefono_propio_a_uno_en_uso_falla(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    get_or_create_persona(db_session, "3019999999", "Beto")

    with pytest.raises(ValueError):
        cambiar_telefono_propio(db_session, ana, "3019999999")

    assert ana.telefono == "+573001234567"  # intacto


# --------------------------------------------------------------------------- #
# Issue 68 (.scratch/pendientes-cliente) — el "@" del usuario de WhatsApp es
# puramente de presentación: se guarda SIEMPRE sin él, sin importar cuántos
# vengan al inicio (pegar un valor que ya traía "@" no puede duplicarlo).
# --------------------------------------------------------------------------- #
def test_whatsapp_usuario_guarda_sin_arroba(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    update_datos_personales(db_session, ana, whatsapp_usuario="@ana.whats")
    assert ana.whatsapp_usuario == "ana.whats"


def test_whatsapp_usuario_con_varias_arrobas_no_duplica(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    update_datos_personales(db_session, ana, whatsapp_usuario="@@ana.whats")
    assert ana.whatsapp_usuario == "ana.whats"


def test_whatsapp_usuario_sin_arroba_se_guarda_igual(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    update_datos_personales(db_session, ana, whatsapp_usuario="ana.whats")
    assert ana.whatsapp_usuario == "ana.whats"


def test_whatsapp_usuario_invalido_rechaza(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    with pytest.raises(ValueError):
        update_datos_personales(db_session, ana, whatsapp_usuario="con espacios")
    assert ana.whatsapp_usuario is None


# --------------------------------------------------------------------------- #
# Issue 67/68 — links de contacto (WhatsApp/llamada) usados en `/residentes`.
# --------------------------------------------------------------------------- #
def test_url_whatsapp_prioriza_el_usuario_sobre_el_telefono(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    update_datos_personales(db_session, ana, whatsapp_usuario="ana.whats")
    assert url_whatsapp(ana) == "https://wa.me/ana.whats"


def test_url_whatsapp_cae_al_telefono_sin_usuario(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    assert url_whatsapp(ana) == "https://wa.me/573001234567"


def test_url_llamada_usa_el_telefono_canonico_con_mas(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    assert url_llamada(ana) == "tel:+573001234567"
