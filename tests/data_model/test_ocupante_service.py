# -*- coding: utf-8 -*-
"""
Seam A — Ocupante (ADR-0006), contra el Postgres efímero.

Comportamiento observable: un Apartamento con Ocupantes siempre tiene
exactamente 1 principal (con Teléfono real); promover exige Teléfono y degrada
al anterior en la misma transacción; listar ordena principal primero.
"""

import pytest

from app.domain.apartamento_service import get_or_create_apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    agregar_ocupante,
    asociar_telefono_a_ocupante,
    dar_de_baja_ocupante,
    desvincular_telefono_ocupante,
    editar_telefono_ocupante,
    listar_ocupantes,
    ocupante_activo_de_persona,
    promover_a_principal,
)
from app.domain.persona import Persona

pytestmark = pytest.mark.integration


def _apto(db_session):
    return get_or_create_apartamento(db_session, "Las Flores", "Torre A", "101")


def test_primer_ocupante_sin_telefono_falla(db_session):
    apto = _apto(db_session)
    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "Mamá")


def test_primer_ocupante_con_telefono_queda_principal_automatico(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    assert papa.es_principal is True
    assert papa.persona_id is not None
    persona = db_session.get(Persona, papa.persona_id)
    assert persona.telefono == "+573001234567"


def test_segundo_ocupante_no_se_auto_promueve(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")  # sin teléfono

    assert mama.es_principal is False
    assert mama.persona_id is None


def test_ocupante_con_telefono_reutiliza_persona_existente(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3001234567")

    # Mismo teléfono => misma Persona (aunque sean Ocupantes distintos aquí no
    # aplicaría en la práctica, pero confirma que no duplica Personas).
    assert papa.persona_id == hija.persona_id


def test_promover_sin_telefono_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    with pytest.raises(ValueError):
        promover_a_principal(db_session, mama)


def test_promover_con_telefono_degrada_al_anterior(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    promover_a_principal(db_session, hija)
    db_session.refresh(papa)
    db_session.refresh(hija)

    assert hija.es_principal is True
    assert papa.es_principal is False
    # Nunca 0 ni 2 principales: exactamente 1.
    principales = [o for o in listar_ocupantes(db_session, apto) if o.es_principal]
    assert len(principales) == 1


def test_listar_ordena_principal_primero(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    agregar_ocupante(db_session, apto, "Mamá")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    ocupantes = listar_ocupantes(db_session, apto)
    assert len(ocupantes) == 3
    assert ocupantes[0].es_principal is True


def test_indice_unico_impide_dos_principales_a_nivel_de_bd(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    # Forzar la violación directamente (sin pasar por promover_a_principal)
    # confirma que el índice único parcial protege a nivel de base de datos,
    # no solo por disciplina de la función de servicio.
    hija.es_principal = True
    with pytest.raises(Exception):
        db_session.flush()


# --------------------------------------------------------------------------- #
# Ticket 02 (.scratch/mis-datos) — marcado de baja + un teléfono, un
# apartamento activo a la vez.
# --------------------------------------------------------------------------- #
def test_dar_de_baja_marca_pero_no_borra(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    dar_de_baja_ocupante(db_session, hija)

    assert hija.desvinculado_en is not None
    # Sigue existiendo la fila -- solo consulta, nunca se borra.
    assert db_session.get(Ocupante, hija.id) is not None


def test_listar_ocupantes_excluye_dados_de_baja_por_defecto(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    dar_de_baja_ocupante(db_session, hija)

    activos = listar_ocupantes(db_session, apto)
    assert [o.id for o in activos] == [papa.id]

    con_historial = listar_ocupantes(db_session, apto, incluir_baja=True)
    assert {o.id for o in con_historial} == {papa.id, hija.id}


def test_dar_de_baja_es_idempotente(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    dar_de_baja_ocupante(db_session, hija)
    primera_fecha = hija.desvinculado_en
    dar_de_baja_ocupante(db_session, hija)  # segunda vez, no debe fallar

    assert hija.desvinculado_en == primera_fecha


def test_principal_no_puede_darse_de_baja_con_otros_activos(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    with pytest.raises(ValueError):
        dar_de_baja_ocupante(db_session, papa)


def test_principal_solo_puede_darse_de_baja_si_es_el_ultimo(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    dar_de_baja_ocupante(db_session, hija)  # se va la hija primero

    dar_de_baja_ocupante(db_session, papa)  # ahora el principal sí puede
    assert papa.desvinculado_en is not None
    assert listar_ocupantes(db_session, apto) == []


def test_promover_un_dado_de_baja_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    dar_de_baja_ocupante(db_session, hija)

    with pytest.raises(ValueError):
        promover_a_principal(db_session, hija)


def test_un_telefono_no_puede_ser_activo_en_dos_apartamentos(db_session):
    apto1 = _apto(db_session)
    apto2 = get_or_create_apartamento(db_session, "Las Flores", "Torre B", "202")
    agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto2, "Papá", telefono="3001234567")


def test_agregar_ocupante_con_telefono_sincroniza_apartamento_actual(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    persona = db_session.get(Persona, papa.persona_id)
    assert persona.apartamento_actual_id == apto.id


def test_asociar_telefono_sincroniza_apartamento_actual(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    asociar_telefono_a_ocupante(db_session, mama, "3021112233")

    persona = db_session.get(Persona, mama.persona_id)
    assert persona.apartamento_actual_id == apto.id


def test_desvincular_telefono_limpia_apartamento_actual(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    persona_id = hija.persona_id

    desvincular_telefono_ocupante(db_session, hija)

    persona = db_session.get(Persona, persona_id)
    assert persona.apartamento_actual_id is None


def test_dar_de_baja_limpia_apartamento_actual(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    persona_id = hija.persona_id

    dar_de_baja_ocupante(db_session, hija)

    persona = db_session.get(Persona, persona_id)
    assert persona.apartamento_actual_id is None


def test_dandose_de_baja_en_el_primero_permite_unirse_al_segundo(db_session):
    apto1 = _apto(db_session)
    apto2 = get_or_create_apartamento(db_session, "Las Flores", "Torre B", "202")
    papa1 = agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")

    dar_de_baja_ocupante(db_session, papa1)
    papa2 = agregar_ocupante(db_session, apto2, "Papá", telefono="3001234567")

    assert papa2.es_principal is True
    assert papa2.apartamento_id == apto2.id


# --------------------------------------------------------------------------- #
# Ticket 03 (.scratch/mis-datos) — gestión de Ocupantes por el principal.
# --------------------------------------------------------------------------- #
def test_ocupante_activo_de_persona_resuelve_el_unico_activo(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    persona = db_session.get(Persona, papa.persona_id)
    encontrado = ocupante_activo_de_persona(db_session, persona.id)
    assert encontrado.id == papa.id


def test_ocupante_activo_de_persona_none_si_no_es_ocupante(db_session):
    from app.domain.persona_service import get_or_create_persona

    persona = get_or_create_persona(db_session, "3009998877", "Suelto")
    assert ocupante_activo_de_persona(db_session, persona.id) is None


def test_asociar_telefono_a_ocupante_sin_telefono(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    asociar_telefono_a_ocupante(db_session, mama, "3021112233")

    assert mama.persona_id is not None
    persona = db_session.get(Persona, mama.persona_id)
    assert persona.telefono == "+573021112233"


def test_asociar_telefono_a_ocupante_que_ya_tiene_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        asociar_telefono_a_ocupante(db_session, papa, "3021112233")


def test_asociar_telefono_ya_activo_en_otro_apartamento_falla(db_session):
    apto1 = _apto(db_session)
    apto2 = get_or_create_apartamento(db_session, "Las Flores", "Torre B", "202")
    agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto2, "Mamá", telefono="3021112233")

    with pytest.raises(ValueError):
        asociar_telefono_a_ocupante(db_session, mama, "3001234567")


# --------------------------------------------------------------------------- #
# `.scratch/pendientes-cliente/issues/35` — editar un teléfono ya asociado.
# --------------------------------------------------------------------------- #
def test_editar_telefono_ocupante_cambia_la_persona_ligada(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", telefono="3021112233")

    editar_telefono_ocupante(db_session, hijo, "3029998877")

    persona = db_session.get(Persona, hijo.persona_id)
    assert persona.telefono == "+573029998877"
    assert persona.apartamento_actual_id == apto.id


def test_editar_telefono_ocupante_sin_telefono_previo_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin teléfono

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, hijo, "3029998877")


def test_editar_telefono_del_principal_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, papa, "3029998877")


def test_editar_telefono_ocupante_ya_activo_en_otro_apartamento_falla(db_session):
    apto1 = _apto(db_session)
    apto2 = get_or_create_apartamento(db_session, "Las Flores", "Torre B", "202")
    agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto1, "Hijo", telefono="3021112233")
    agregar_ocupante(db_session, apto2, "Mamá", telefono="3029998877")

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, hijo, "3029998877")


def test_desvincular_telefono_de_ocupante_no_principal(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    desvincular_telefono_ocupante(db_session, hija)

    assert hija.persona_id is None


def test_desvincular_telefono_del_principal_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        desvincular_telefono_ocupante(db_session, papa)


def test_maximo_5_ocupantes_activos_por_apartamento(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Uno", telefono="3000000001")
    agregar_ocupante(db_session, apto, "Dos", telefono="3000000002")
    agregar_ocupante(db_session, apto, "Tres", telefono="3000000003")
    agregar_ocupante(db_session, apto, "Cuatro", telefono="3000000004")
    agregar_ocupante(db_session, apto, "Cinco", telefono="3000000005")

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "Seis", telefono="3000000006")


def test_dar_de_baja_libera_espacio_bajo_el_limite_de_5(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Uno", telefono="3000000001")
    agregar_ocupante(db_session, apto, "Dos", telefono="3000000002")
    agregar_ocupante(db_session, apto, "Tres", telefono="3000000003")
    agregar_ocupante(db_session, apto, "Cuatro", telefono="3000000004")
    cinco = agregar_ocupante(db_session, apto, "Cinco", telefono="3000000005")

    dar_de_baja_ocupante(db_session, cinco)
    seis = agregar_ocupante(db_session, apto, "Seis", telefono="3000000006")
    assert seis.persona_id is not None
