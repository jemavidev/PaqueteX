# -*- coding: utf-8 -*-
"""
Seam A — candidatos de "Corregir" (Grupo 16, Ronda 2).

Comportamiento observable: los Ocupantes del Apartamento del snapshot más el
Anunciante, únicos por (nombre, teléfono); sin Apartamento resuelto, solo el
Anunciante; nunca crea un Apartamento por accidente al consultar.
"""

import pytest

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import agregar_ocupante
from app.domain.paquete_correccion_service import (
    candidatos_correccion,
    candidatos_correccion_por_paquetes,
)
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

    # `estado_ocupante=None`: Ana es solo Anunciante acá, no Ocupante de
    # ninguna unidad -- no hay badge (Principal/Confirmado/Pendiente) que
    # mostrar, sería un dato inventado. `persona_id` (.scratch/paquetes-
    # residentes-conexion): siempre el Anunciante cuando no hay Ocupante.
    assert candidatos == [
        {
            "nombre": "ANA",
            "telefono": "+573001234567",
            "estado_ocupante": None,
            "persona_id": p.announced_by_persona_id,
        }
    ]


def test_con_apartamento_trae_ocupantes_mas_el_anunciante(db_session):
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    papa = agregar_ocupante(db_session, apto, "Papá", "3011111111")
    mama = agregar_ocupante(db_session, apto, "Mamá")  # sin teléfono
    db_session.commit()

    p = _anunciar(db_session, tel="3022222222", nombre="Visitante", apartamento=apto)

    candidatos = candidatos_correccion(db_session, p)

    # Ningún Ocupante nuevo nace principal/confirmado (issue 97/98) -- los
    # dos quedan "pendiente" hasta que alguien los confirme. `persona_id`
    # (.scratch/paquetes-residentes-conexion): el de su propio Ocupante --
    # `None` para Mamá, que no tiene contacto propio todavía.
    assert {
        "nombre": "PAPÁ", "telefono": "+573011111111", "estado_ocupante": "pendiente",
        "persona_id": papa.persona_id,
    } in candidatos
    assert {
        "nombre": "MAMÁ", "telefono": None, "estado_ocupante": "pendiente",
        "persona_id": mama.persona_id,
    } in candidatos
    assert {
        "nombre": "VISITANTE", "telefono": "+573022222222", "estado_ocupante": None,
        "persona_id": p.announced_by_persona_id,
    } in candidatos
    assert len(candidatos) == 3
    assert papa.persona_id is not None
    assert mama.persona_id is None


def test_no_duplica_si_el_anunciante_es_tambien_ocupante(db_session):
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto, "Ana", "3001234567")
    db_session.commit()

    p = _anunciar(db_session, tel="3001234567", nombre="Ana", apartamento=apto)

    candidatos = candidatos_correccion(db_session, p)

    # El dedup mantiene la entrada del Ocupante (se procesa antes que el
    # Anunciante en `_construir_candidatos`) -- por eso SÍ trae "pendiente",
    # no `None`. Mismo teléfono -> misma Persona real detrás de las dos
    # entradas, así que el `persona_id` que sobrevive coincide con la del
    # Anunciante de todos modos.
    assert candidatos == [
        {
            "nombre": "ANA",
            "telefono": "+573001234567",
            "estado_ocupante": "pendiente",
            "persona_id": p.announced_by_persona_id,
        }
    ]


def test_apartamento_del_snapshot_que_ya_no_existe_no_revienta(db_session):
    # Snapshot con una terna que nunca se materializó como Apartamento real
    # (p.ej. datos legados) -- no debe crear uno ni fallar, solo omitir esos
    # candidatos y caer al Anunciante.
    total_antes = db_session.query(Apartamento).count()
    p = _anunciar(db_session, nombre="Ana")
    p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento = (
        "FANTASMA",
        "Z",
        "999",
    )
    db_session.flush()

    candidatos = candidatos_correccion(db_session, p)

    assert candidatos == [
        {
            "nombre": "ANA",
            "telefono": "+573001234567",
            "estado_ocupante": None,
            "persona_id": p.announced_by_persona_id,
        }
    ]
    assert db_session.query(Apartamento).count() == total_antes  # no se creó nada


# --------------------------------------------------------------------------- #
# `candidatos_correccion_por_paquetes` -- versión batch (auditoría de
# rendimiento 2026-08-10, .scratch/pendientes-cliente): mismo resultado que
# llamar a `candidatos_correccion` una vez por Paquete, en un puñado FIJO de
# queries en vez de una tanda por Paquete.
# --------------------------------------------------------------------------- #
def test_batch_da_el_mismo_resultado_que_llamar_uno_por_uno(db_session):
    apto1 = resolver_apartamento(db_session, "TORRE 1", "101")
    agregar_ocupante(db_session, apto1, "Papá", "3001234567")
    agregar_ocupante(db_session, apto1, "Hijo")
    db_session.commit()

    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    agregar_ocupante(db_session, apto2, "Mamá", whatsapp_usuario="mama.whats")
    db_session.commit()

    p_con_apto1 = _anunciar(db_session, tel="3009998877", nombre="Visita", apartamento=apto1)
    p_con_apto2 = _anunciar(db_session, tel="3009998866", nombre="Otra Visita", apartamento=apto2)
    p_sin_apto = _anunciar(db_session, tel="3009998855", nombre="Sin Unidad")
    p_fantasma = _anunciar(db_session, tel="3009998844", nombre="Fantasma")
    p_fantasma.snapshot_conjunto, p_fantasma.snapshot_torre, p_fantasma.snapshot_apartamento = (
        "FANTASMA", "Z", "999",
    )
    db_session.flush()

    paquetes = [p_con_apto1, p_con_apto2, p_sin_apto, p_fantasma]
    esperado = {p.id: candidatos_correccion(db_session, p) for p in paquetes}

    resultado = candidatos_correccion_por_paquetes(db_session, paquetes)

    assert resultado == esperado
    assert len(resultado[p_con_apto1.id]) == 3  # Papá, Hijo, Visita
    assert len(resultado[p_con_apto2.id]) == 2  # Mamá, Otra Visita


def test_batch_lista_vacia_no_falla(db_session):
    assert candidatos_correccion_por_paquetes(db_session, []) == {}
