# -*- coding: utf-8 -*-
"""
Seam A — Ocupante (ADR-0006), contra el Postgres efímero.

Comportamiento observable: un Apartamento con Ocupantes siempre tiene
exactamente 1 principal (con Teléfono real); promover exige Teléfono y degrada
al anterior en la misma transacción; listar ordena principal primero.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    MAX_OCUPANTES_ACTIVOS,
    agregar_ocupante,
    agregar_telefono_a_persona_de_ocupante,
    agregar_whatsapp_a_persona_de_ocupante,
    anunciante_para_ocupante,
    asociar_telefono_a_ocupante,
    asociar_whatsapp_a_ocupante,
    cambios_recientes_de_apartamento,
    confirmar_ocupante,
    dar_de_baja_ocupante,
    desvincular_telefono_ocupante,
    desvincular_whatsapp_ocupante,
    editar_telefono_ocupante,
    editar_whatsapp_ocupante,
    hay_otro_ocupante_activo,
    identificar_contacto_para_unidad,
    listar_ocupantes,
    mensaje_ya_ocupante_activo,
    mover_ocupante,
    ocupante_activo_de_persona,
    ocupante_activo_por_contacto,
    ocupantes_activos_de_personas,
    promover_a_principal,
    reasignar_apartamento,
    residentes_por_torre_apartamento,
    telefono_notificacion_ocupante,
)
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

_PW = "Contrasena1"


def _apto(db_session):
    return resolver_apartamento(db_session, "TORRE 1", "101")


def _staff(session):
    # Idempotente dentro de un mismo test -- `create_initial_admin` falla si
    # ya existe un ADMIN, y varios tests confirman más de una vez.
    admin = session.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).first()
    if admin is not None:
        return admin
    return create_initial_admin(session, "admin@club.com", "Admin", _PW)


def _agregar_confirmado(session, apto, nombre, telefono=None):
    """Fixture de conveniencia: crea un Ocupante y lo confirma de inmediato
    (por staff) -- para tests que no son SOBRE el flujo de confirmación en
    sí, pero necesitan un principal ya establecido."""
    ocupante = agregar_ocupante(session, apto, nombre, telefono)
    return confirmar_ocupante(session, ocupante, _staff(session))


def test_primer_ocupante_sin_telefono_falla(db_session):
    apto = _apto(db_session)
    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "Mamá")


def test_primer_ocupante_con_telefono_nace_pending_sin_principal(db_session):
    # Catálogo cerrado / confirmación (.scratch/apartamento-catalogo-
    # confirmacion, ticket 06): ya no se auto-promueve al crear, ni siquiera
    # el primero de un Apartamento vacío -- eso pasa recién al confirmar.
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    assert papa.es_principal is False
    assert papa.confirmado_en is None
    assert papa.persona_id is not None
    persona = db_session.get(Persona, papa.persona_id)
    assert persona.telefono == "+573001234567"


def test_staff_confirma_al_primero_y_lo_promueve_a_principal(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    confirmado = confirmar_ocupante(db_session, papa, _staff(db_session))

    assert confirmado.confirmado_en is not None
    assert confirmado.es_principal is True


def test_confirmar_promueve_aunque_hubo_un_principal_viejo_ya_desvinculado(db_session):
    # Issue 166 (.scratch/pendientes-cliente) -- bug real: `hay_principal`
    # no filtraba `desvinculado_en IS NULL`, así que un Principal VIEJO (ya
    # dado de baja, su fila conserva `es_principal=True` como historial)
    # bloqueaba la promoción para siempre, aunque la unidad llevara meses
    # vacía. Reproduce exactamente el caso reportado: mover a un residente
    # a una unidad que ya tuvo Principal antes, ahora vacía -- debe poder
    # promoverse igual que a cualquier unidad genuinamente vacía.
    apto = _apto(db_session)
    viejo_principal = _agregar_confirmado(db_session, apto, "Papá Viejo", "3001111111")
    dar_de_baja_ocupante(db_session, viejo_principal)  # se fue -- unidad vacía otra vez

    nuevo = agregar_ocupante(db_session, apto, "Daniela", telefono="3001234567")
    confirmado = confirmar_ocupante(db_session, nuevo, _staff(db_session))

    assert confirmado.confirmado_en is not None
    assert confirmado.es_principal is True


def test_principal_confirmado_confirma_a_un_segundo_sin_tocar_quien_es_principal(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    persona_papa = db_session.get(Persona, papa.persona_id)
    confirmado = confirmar_ocupante(db_session, mama, persona_papa)

    assert confirmado.confirmado_en is not None
    assert confirmado.es_principal is False  # no lo toca -- papá sigue siendo
    db_session.refresh(papa)
    assert papa.es_principal is True


def test_actor_sin_permiso_no_puede_confirmar(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    extrano = agregar_ocupante(
        db_session, resolver_apartamento(db_session, "TORRE 2", "202"), "Extraño", "3029990000"
    )
    persona_extrana = db_session.get(Persona, extrano.persona_id)

    with pytest.raises(PermissionError):
        confirmar_ocupante(db_session, papa, persona_extrana)


def test_confirmar_dos_veces_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(db_session, papa, _staff(db_session))

    with pytest.raises(ValueError):
        confirmar_ocupante(db_session, papa, _staff(db_session))


def test_rechazar_un_pending_reutiliza_dar_de_baja_y_nunca_fue_principal(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    dar_de_baja_ocupante(db_session, papa)

    assert papa.desvinculado_en is not None
    assert papa.confirmado_en is None  # nunca llegó a confirmarse
    assert papa.es_principal is False


def test_pending_cuenta_para_el_limite(db_session):
    apto = _apto(db_session)
    for i in range(MAX_OCUPANTES_ACTIVOS):  # ninguno confirmado
        agregar_ocupante(db_session, apto, f"Ocupante{i}", telefono=f"30000000{i:02d}")

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "DeMas", telefono="3000009999")


def test_pending_sincroniza_apartamento_actual_igual_que_confirmado(db_session):
    # Sin gate funcional: anunciar/recibir dependen de `apartamento_actual_id`,
    # que se sincroniza igual sin importar si el Ocupante está confirmado.
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    persona = db_session.get(Persona, papa.persona_id)
    assert persona.apartamento_actual_id == apto.id


def test_segundo_ocupante_no_se_auto_promueve(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")  # sin teléfono

    assert mama.es_principal is False
    assert mama.persona_id is None


def test_ocupante_con_telefono_reutiliza_persona_existente(db_session):
    # `get_or_create_persona` nunca duplica la Persona -- mismo teléfono
    # siempre resuelve a la misma fila, incluso entre Apartamentos distintos
    # (acá el segundo intento falla por otro motivo: ese teléfono ya es
    # Ocupante activo, ver `test_un_telefono_no_puede_ser_activo_en_dos_
    # apartamentos` -- este test solo confirma que la Persona se REUTILIZA,
    # no que agregarla dos veces como Ocupante esté permitido).
    from app.domain.persona_service import get_or_create_persona

    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    papa = agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")

    persona = get_or_create_persona(db_session, "3001234567", "Papá")
    assert persona.id == papa.persona_id

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto2, "Papá Otra Vez", telefono="3001234567")


def test_agregar_ocupante_con_telefono_ya_registrado_usa_el_nombre_existente(db_session):
    # Conversación 2026-08-16 (pedido explícito del cliente): si el contacto
    # YA es una Persona conocida (acá, una que existe pero no está
    # ACTIVAMENTE ocupando nada en este momento -- el otro caso, activo en
    # otra unidad, ya lo bloquea `_persona_ya_es_ocupante_activo` antes de
    # llegar a este punto), el nombre que se guarda es el registrado, no el
    # recién tecleado -- evita que la misma Persona muestre nombres
    # distintos según qué Ocupante se mire.
    from app.domain.persona_service import get_or_create_persona

    apto = _apto(db_session)
    persona = get_or_create_persona(db_session, "3005551111", "Nombre Real Registrado")

    ocupante = agregar_ocupante(db_session, apto, "Nombre Que Alguien Tipeo Distinto", telefono="3005551111")

    assert ocupante.persona_id == persona.id
    assert ocupante.nombre == "NOMBRE REAL REGISTRADO"


def test_agregar_ocupante_con_whatsapp_ya_registrado_usa_el_nombre_existente(db_session):
    from app.domain.persona_service import get_or_create_persona_por_whatsapp

    apto = _apto(db_session)
    persona = get_or_create_persona_por_whatsapp(db_session, "nombre.real", "Nombre Real Registrado")

    ocupante = agregar_ocupante(db_session, apto, "Nombre Distinto Tipeo", whatsapp_usuario="nombre.real")

    assert ocupante.persona_id == persona.id
    assert ocupante.nombre == "NOMBRE REAL REGISTRADO"


def test_agregar_ocupante_con_telefono_nuevo_si_usa_el_nombre_tecleado(db_session):
    # Contraparte del test anterior: sin Persona previa para ese teléfono,
    # el nombre tecleado SÍ se usa -- no hay identidad registrada que
    # proteger.
    apto = _apto(db_session)

    ocupante = agregar_ocupante(db_session, apto, "Nombre Nuevo De Verdad", telefono="3005552222")

    assert ocupante.nombre == "NOMBRE NUEVO DE VERDAD"


def test_agregar_ocupante_reutiliza_nombre_registrado_tras_desvincular(db_session):
    # El escenario concreto que motivó el pedido: alguien ya registrado se
    # desvincula de su unidad, y luego alguien intenta darlo de alta de
    # nuevo (en la misma unidad u otra) con un nombre distinto -- el nombre
    # real registrado sigue mandando; para usar otro nombre con ese
    # contacto hay que desvincularlo primero (ya se cumple acá) y el nuevo
    # alta TAMBIÉN respeta el nombre real, no cualquier texto libre.
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 3", "303")

    original = agregar_ocupante(db_session, apto1, "Nombre Real Registrado", telefono="3005553333")
    dar_de_baja_ocupante(db_session, original)

    reingreso = agregar_ocupante(db_session, apto2, "Intento De Renombrar", telefono="3005553333")

    assert reingreso.persona_id == original.persona_id
    assert reingreso.nombre == "NOMBRE REAL REGISTRADO"


def test_promover_sin_telefono_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    with pytest.raises(ValueError):
        promover_a_principal(db_session, mama)


def test_promover_con_telefono_degrada_al_anterior(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    promover_a_principal(db_session, hija)
    db_session.refresh(papa)
    db_session.refresh(hija)

    assert hija.es_principal is True
    assert papa.es_principal is False
    # Nunca 0 ni 2 principales: exactamente 1.
    principales = [o for o in listar_ocupantes(db_session, apto) if o.es_principal]
    assert len(principales) == 1


def test_promover_con_dos_principales_historicos_no_revienta(db_session):
    # Issue 167 (.scratch/pendientes-cliente) -- bug real reportado en vivo,
    # efecto secundario directo de [[166]]: antes del fix del índice único,
    # la base de datos hacía IMPOSIBLE que existiera más de una fila
    # `es_principal=True` por unidad (activa o no) -- así que esta consulta,
    # sin filtrar `desvinculado_en`, nunca podía encontrar más de una. Con
    # el índice ya corregido para permitir historial (uno activo + viejos ya
    # desvinculados), la unidad puede tener DOS o más filas `es_principal=
    # True` con el tiempo -- reventaba con `MultipleResultsFound` en vez de
    # degradar solo al activo.
    apto = _apto(db_session)
    viejo_principal = _agregar_confirmado(db_session, apto, "Papá Viejo", "3001111111")
    dar_de_baja_ocupante(db_session, viejo_principal)  # se va -- su fila sigue es_principal=True
    principal_actual = _agregar_confirmado(db_session, apto, "Mamá Actual", "3002222222")
    nueva = agregar_ocupante(db_session, apto, "Hija Nueva", telefono="3003333333")

    promover_a_principal(db_session, nueva)
    db_session.refresh(principal_actual)
    db_session.refresh(nueva)

    assert nueva.es_principal is True
    assert principal_actual.es_principal is False  # degradado -- el activo, no el viejo
    principales_activos = [o for o in listar_ocupantes(db_session, apto) if o.es_principal]
    assert len(principales_activos) == 1


def test_promover_confirma_al_que_estaba_pending(db_session):
    """.scratch/ocupante-principal-escenarios, ticket 03 -- promover a
    principal (por cualquier vía) ya no puede dejar a alguien
    es_principal=True sin confirmar."""
    apto = _apto(db_session)
    _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    assert hija.confirmado_en is None  # todavía pending

    promover_a_principal(db_session, hija)
    db_session.refresh(hija)

    assert hija.es_principal is True
    assert hija.confirmado_en is not None


def test_promover_no_pisa_confirmado_en_si_ya_estaba_confirmado(db_session):
    apto = _apto(db_session)
    _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    confirmar_ocupante(db_session, hija, _staff(db_session))
    confirmado_original = hija.confirmado_en

    promover_a_principal(db_session, hija)
    db_session.refresh(hija)

    assert hija.confirmado_en == confirmado_original


def test_listar_ordena_principal_primero(db_session):
    apto = _apto(db_session)
    _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    agregar_ocupante(db_session, apto, "Mamá")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    ocupantes = listar_ocupantes(db_session, apto)
    assert len(ocupantes) == 3
    assert ocupantes[0].es_principal is True


def test_indice_unico_impide_dos_principales_a_nivel_de_bd(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
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


def test_residentes_por_torre_apartamento_solo_unidades_con_ocupante_activo(db_session):
    # Issue 85 (.scratch/pendientes-cliente) -- buscador de "Asignar
    # apartamento": una unidad ausente del dict está libre.
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")
    dar_de_baja_ocupante(db_session, hija)

    residentes = residentes_por_torre_apartamento(db_session)
    assert residentes[apto.torre][apto.apartamento] == [papa.nombre]  # Hija, dada de baja, no cuenta

    # Otra unidad del catálogo, sin ningún Ocupante -- ausente del dict.
    from app.domain.apartamento_service import resolver_apartamento

    libre = resolver_apartamento(db_session, "TORRE 2", "201")
    assert libre.apartamento not in residentes.get(libre.torre, {})


# --------------------------------------------------------------------------- #
# `cambios_recientes_de_apartamento` (issue 165, .scratch/pendientes-cliente)
# -- ícono "cambio reciente" en /paquetes.
# --------------------------------------------------------------------------- #
def test_cambios_recientes_de_apartamento_encuentra_baja_reciente(db_session):
    apto_viejo = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto_viejo, "Ana", telefono="3001234567")
    persona_id = ocupante.persona_id
    dar_de_baja_ocupante(db_session, ocupante)

    resultado = cambios_recientes_de_apartamento(db_session, [persona_id])

    assert resultado[persona_id] == {"torre": apto_viejo.torre, "apartamento": apto_viejo.apartamento}


def test_cambios_recientes_de_apartamento_ignora_bajas_de_mas_de_30_dias(db_session):
    apto_viejo = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto_viejo, "Ana", telefono="3001234567")
    persona_id = ocupante.persona_id
    dar_de_baja_ocupante(db_session, ocupante)
    ocupante.desvinculado_en = datetime.now(timezone.utc) - timedelta(days=45)
    db_session.flush()

    resultado = cambios_recientes_de_apartamento(db_session, [persona_id])

    assert persona_id not in resultado


def test_cambios_recientes_de_apartamento_sin_ninguna_baja_no_aparece(db_session):
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")

    assert cambios_recientes_de_apartamento(db_session, [ocupante.persona_id]) == {}


def test_cambios_recientes_de_apartamento_usa_la_baja_mas_reciente(db_session):
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    apto3 = resolver_apartamento(db_session, "TORRE 3", "303")
    ocupante1 = agregar_ocupante(db_session, apto1, "Ana", telefono="3001234567")
    persona_id = ocupante1.persona_id
    ocupante2 = mover_ocupante(db_session, ocupante1, apto2)  # deja apto1 -> apto2
    mover_ocupante(db_session, ocupante2, apto3)  # deja apto2 -> apto3

    resultado = cambios_recientes_de_apartamento(db_session, [persona_id])

    assert resultado[persona_id] == {"torre": apto2.torre, "apartamento": apto2.apartamento}


def test_cambios_recientes_de_apartamento_sin_ids_no_consulta(db_session):
    assert cambios_recientes_de_apartamento(db_session, []) == {}


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
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    with pytest.raises(ValueError):
        dar_de_baja_ocupante(db_session, papa)


def test_principal_solo_puede_darse_de_baja_si_es_el_ultimo(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
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
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto2, "Papá", telefono="3001234567")


def test_un_telefono_no_puede_ser_activo_dos_veces_en_el_mismo_apartamento(db_session):
    # Bug real reproducido: `_persona_activa_en_otro_apartamento` excluía el
    # propio Apartamento del chequeo, así que agregar el mismo teléfono dos
    # veces a la MISMA unidad colaba un segundo Ocupante activo -- cualquier
    # llamada posterior a `ocupante_activo_de_persona` para esa Persona
    # (login, /mis-datos, announce, elegibilidad de OTP) revienta con
    # `MultipleResultsFound` (`.one_or_none()` asume como máximo una fila).
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "Papá Otra Vez", telefono="3001234567")


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
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    papa1 = agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")

    dar_de_baja_ocupante(db_session, papa1)
    papa2 = agregar_ocupante(db_session, apto2, "Papá", telefono="3001234567")

    # Nace pending como cualquier Ocupante nuevo (ticket 06) -- ya no se
    # auto-promueve; lo relevante acá es que SÍ pudo unirse al segundo tras
    # liberar el primero.
    assert papa2.es_principal is False
    assert papa2.confirmado_en is None
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


# --------------------------------------------------------------------------- #
# Issue 68 (.scratch/pendientes-cliente) — versión batch, badge de
# Principal/Secundario en la lista de `/residentes`.
# --------------------------------------------------------------------------- #
def test_ocupantes_activos_de_personas_resuelve_varias_a_la_vez(db_session):
    from app.domain.persona_service import get_or_create_persona

    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", telefono="3007654321")
    suelto = get_or_create_persona(db_session, "3009998877", "Suelto")

    papa_persona_id = papa.persona_id
    hijo_persona_id = hijo.persona_id

    resultado = ocupantes_activos_de_personas(
        db_session, [papa_persona_id, hijo_persona_id, suelto.id]
    )

    assert resultado[papa_persona_id].id == papa.id
    assert resultado[hijo_persona_id].id == hijo.id
    assert suelto.id not in resultado  # nunca fue Ocupante -- no aplica


def test_ocupantes_activos_de_personas_lista_vacia(db_session):
    assert ocupantes_activos_de_personas(db_session, []) == {}


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
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
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


def test_editar_telefono_ocupante_canal_doble_no_pierde_whatsapp(db_session):
    # Issue 229 (.scratch/pendientes-cliente): bug real encontrado en vivo --
    # editar el teléfono de una Persona que TAMBIÉN tiene WhatsApp re-ligaba
    # el Ocupante a una Persona nueva (sin el WhatsApp), perdiéndolo.
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(
        db_session, apto, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.whats"
    )
    persona_id_antes = hijo.persona_id

    editar_telefono_ocupante(db_session, hijo, "3029998877")

    assert hijo.persona_id == persona_id_antes  # NO se re-ligó a otra Persona
    persona = db_session.get(Persona, hijo.persona_id)
    assert persona.telefono == "+573029998877"
    assert persona.whatsapp_usuario == "hijo.whats"  # sigue intacto


def test_editar_telefono_ocupante_canal_doble_telefono_ya_en_otra_persona_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(
        db_session, apto, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.whats"
    )

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, hijo, "3001234567")  # ya es de Papá


def test_editar_telefono_ocupante_choca_con_persona_huerfana_canal_doble_falla(db_session):
    # Issue 233 (.scratch/pendientes-cliente, bug real encontrado en
    # revisión de código): una Persona HUÉRFANA (ya no es Ocupante activo de
    # nadie) que conserva su propio WhatsApp no debe re-ligarse en silencio
    # -- sobreescribiría ese WhatsApp ajeno si el mismo envío también cambia
    # el WhatsApp del Ocupante (issue 228, /editar unificado).
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    viejo = agregar_ocupante(
        db_session, apto, "Viejo", telefono="3009990000", whatsapp_usuario="viejo.whats"
    )
    dar_de_baja_ocupante(db_session, viejo)  # huérfana, pero conserva ambos canales

    hijo = agregar_ocupante(db_session, apto, "Hijo", telefono="3021112233")  # canal único

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, hijo, "3009990000")


# --------------------------------------------------------------------------- #
# `agregar_telefono_a_persona_de_ocupante` (issues 213/217/226) -- sin
# cobertura directa hasta la revisión de código de issue 233
# (.scratch/pendientes-cliente): solo se había probado a mano por curl.
# --------------------------------------------------------------------------- #
def test_agregar_telefono_a_persona_de_ocupante_agrega_sin_perder_whatsapp(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", whatsapp_usuario="hijo.whats")
    persona_id_antes = hijo.persona_id

    agregar_telefono_a_persona_de_ocupante(db_session, hijo, "3021112233")

    assert hijo.persona_id == persona_id_antes  # misma Persona, no se re-ligó
    persona = db_session.get(Persona, hijo.persona_id)
    assert persona.telefono == "+573021112233"
    assert persona.whatsapp_usuario == "hijo.whats"  # sigue intacto


def test_agregar_telefono_a_persona_de_ocupante_sin_persona_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto

    with pytest.raises(ValueError):
        agregar_telefono_a_persona_de_ocupante(db_session, hijo, "3021112233")


def test_agregar_telefono_a_persona_de_ocupante_ya_tiene_telefono_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(
        db_session, apto, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.whats"
    )

    with pytest.raises(ValueError):
        agregar_telefono_a_persona_de_ocupante(db_session, hijo, "3029998877")


def test_agregar_telefono_a_persona_de_ocupante_telefono_en_otra_persona_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", whatsapp_usuario="hijo.whats")

    with pytest.raises(ValueError):
        agregar_telefono_a_persona_de_ocupante(db_session, hijo, "3001234567")  # ya es de Papá


def test_editar_telefono_ocupante_sin_telefono_previo_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin teléfono

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, hijo, "3029998877")


def test_editar_telefono_del_principal_falla(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")

    with pytest.raises(ValueError):
        editar_telefono_ocupante(db_session, papa, "3029998877")


def test_editar_telefono_ocupante_ya_activo_en_otro_apartamento_falla(db_session):
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
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
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")

    with pytest.raises(ValueError):
        desvincular_telefono_ocupante(db_session, papa)


# --------------------------------------------------------------------------- #
# WhatsApp (.scratch/ocupante-principal-escenarios, ticket 06) -- mismo
# patrón que Teléfono arriba, resuelto por WhatsApp.
# --------------------------------------------------------------------------- #
def test_asociar_whatsapp_a_ocupante_sin_contacto(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    mama = agregar_ocupante(db_session, apto, "Mamá")

    asociar_whatsapp_a_ocupante(db_session, mama, "mama.whats")

    assert mama.persona_id is not None
    persona = db_session.get(Persona, mama.persona_id)
    assert persona.whatsapp_usuario == "mama.whats"
    assert persona.apartamento_actual_id == apto.id


def test_asociar_whatsapp_a_ocupante_que_ya_tiene_contacto_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")

    with pytest.raises(ValueError):
        asociar_whatsapp_a_ocupante(db_session, papa, "papa.whats")


def test_asociar_whatsapp_ya_activo_en_otro_apartamento_falla(db_session):
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    agregar_ocupante(db_session, apto1, "Papá", whatsapp_usuario="papa.whats")
    mama = agregar_ocupante(db_session, apto2, "Mamá", telefono="3021112233")

    with pytest.raises(ValueError):
        asociar_whatsapp_a_ocupante(db_session, mama, "papa.whats")


def test_editar_whatsapp_ocupante_cambia_la_persona_ligada(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", whatsapp_usuario="hijo.viejo")

    editar_whatsapp_ocupante(db_session, hijo, "hijo.nuevo")

    persona = db_session.get(Persona, hijo.persona_id)
    assert persona.whatsapp_usuario == "hijo.nuevo"
    assert persona.apartamento_actual_id == apto.id


def test_editar_whatsapp_ocupante_canal_doble_no_pierde_telefono(db_session):
    # Issue 229 (.scratch/pendientes-cliente): mismo bug real que
    # `editar_telefono_ocupante`, del lado de WhatsApp.
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(
        db_session, apto, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.viejo"
    )
    persona_id_antes = hijo.persona_id

    editar_whatsapp_ocupante(db_session, hijo, "hijo.nuevo")

    assert hijo.persona_id == persona_id_antes  # NO se re-ligó a otra Persona
    persona = db_session.get(Persona, hijo.persona_id)
    assert persona.whatsapp_usuario == "hijo.nuevo"
    assert persona.telefono == "+573021112233"  # sigue intacto


def test_editar_whatsapp_ocupante_canal_doble_whatsapp_ya_en_otra_persona_falla(db_session):
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    agregar_ocupante(db_session, apto1, "Papá", whatsapp_usuario="papa.whats")
    hijo = agregar_ocupante(
        db_session, apto2, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.whats"
    )

    with pytest.raises(ValueError):
        editar_whatsapp_ocupante(db_session, hijo, "papa.whats")  # ya es de Papá


def test_editar_whatsapp_ocupante_choca_con_persona_huerfana_canal_doble_falla(db_session):
    # Issue 233 (.scratch/pendientes-cliente) -- simétrico al de Teléfono:
    # una Persona huérfana con su propio Teléfono no debe re-ligarse en
    # silencio al cambiar el WhatsApp de otro Ocupante.
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    viejo = agregar_ocupante(
        db_session, apto, "Viejo", telefono="3009990000", whatsapp_usuario="viejo.whats"
    )
    dar_de_baja_ocupante(db_session, viejo)  # huérfana, pero conserva ambos canales

    hijo = agregar_ocupante(db_session, apto, "Hijo", whatsapp_usuario="hijo.whats")  # canal único

    with pytest.raises(ValueError):
        editar_whatsapp_ocupante(db_session, hijo, "viejo.whats")


# --------------------------------------------------------------------------- #
# `agregar_whatsapp_a_persona_de_ocupante` -- simétrico al bloque de
# Teléfono arriba, misma cobertura faltante señalada por issue 233.
# --------------------------------------------------------------------------- #
def test_agregar_whatsapp_a_persona_de_ocupante_agrega_sin_perder_telefono(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", telefono="3021112233")
    persona_id_antes = hijo.persona_id

    agregar_whatsapp_a_persona_de_ocupante(db_session, hijo, "hijo.whats")

    assert hijo.persona_id == persona_id_antes  # misma Persona, no se re-ligó
    persona = db_session.get(Persona, hijo.persona_id)
    assert persona.whatsapp_usuario == "hijo.whats"
    assert persona.telefono == "+573021112233"  # sigue intacto


def test_agregar_whatsapp_a_persona_de_ocupante_sin_persona_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto

    with pytest.raises(ValueError):
        agregar_whatsapp_a_persona_de_ocupante(db_session, hijo, "hijo.whats")


def test_agregar_whatsapp_a_persona_de_ocupante_ya_tiene_whatsapp_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(
        db_session, apto, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.whats"
    )

    with pytest.raises(ValueError):
        agregar_whatsapp_a_persona_de_ocupante(db_session, hijo, "hijo.nuevo")


def test_agregar_whatsapp_a_persona_de_ocupante_whatsapp_en_otra_persona_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", whatsapp_usuario="papa.whats")
    hijo = agregar_ocupante(db_session, apto, "Hijo", telefono="3021112233")

    with pytest.raises(ValueError):
        agregar_whatsapp_a_persona_de_ocupante(db_session, hijo, "papa.whats")  # ya es de Papá


def test_editar_whatsapp_ocupante_sin_contacto_previo_falla(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto

    with pytest.raises(ValueError):
        editar_whatsapp_ocupante(db_session, hijo, "hijo.nuevo")


def test_editar_whatsapp_del_principal_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", whatsapp_usuario="papa.whats")
    confirmar_ocupante(db_session, papa, _staff(db_session))

    with pytest.raises(ValueError):
        editar_whatsapp_ocupante(db_session, papa, "papa.nuevo")


def test_editar_whatsapp_ocupante_ya_activo_en_otro_apartamento_falla(db_session):
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    agregar_ocupante(db_session, apto1, "Papá", telefono="3001234567")
    hijo = agregar_ocupante(db_session, apto1, "Hijo", whatsapp_usuario="hijo.whats")
    agregar_ocupante(db_session, apto2, "Mamá", whatsapp_usuario="mama.whats")

    with pytest.raises(ValueError):
        editar_whatsapp_ocupante(db_session, hijo, "mama.whats")


def test_desvincular_whatsapp_de_ocupante_no_principal(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", whatsapp_usuario="hija.whats")

    desvincular_whatsapp_ocupante(db_session, hija)

    assert hija.persona_id is None


def test_desvincular_whatsapp_del_principal_falla(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", whatsapp_usuario="papa.whats")
    confirmar_ocupante(db_session, papa, _staff(db_session))

    with pytest.raises(ValueError):
        desvincular_whatsapp_ocupante(db_session, papa)


def test_maximo_ocupantes_activos_por_apartamento(db_session):
    apto = _apto(db_session)
    for i in range(MAX_OCUPANTES_ACTIVOS):
        agregar_ocupante(db_session, apto, f"Ocupante{i}", telefono=f"30000000{i:02d}")

    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "DeMas", telefono="3000009999")


def test_dar_de_baja_libera_espacio_bajo_el_limite(db_session):
    apto = _apto(db_session)
    for i in range(MAX_OCUPANTES_ACTIVOS - 1):
        agregar_ocupante(db_session, apto, f"Ocupante{i}", telefono=f"30000000{i:02d}")
    ultimo = agregar_ocupante(
        db_session, apto, f"Ocupante{MAX_OCUPANTES_ACTIVOS - 1}",
        telefono=f"30000000{MAX_OCUPANTES_ACTIVOS - 1:02d}",
    )

    dar_de_baja_ocupante(db_session, ultimo)
    de_mas = agregar_ocupante(db_session, apto, "DeMas", telefono="3000009999")
    assert de_mas.persona_id is not None


# --------------------------------------------------------------------------- #
# Issue 69 -- aviso de reasignación bloqueada. Ticket 13 (.scratch/ocupante-
# principal-escenarios) -- picker de Dirección restringido a unidades vacías.
# --------------------------------------------------------------------------- #
def test_hay_otro_ocupante_activo_true_con_companeros(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    agregar_ocupante(db_session, apto, "Hijo", telefono="3007654321")

    assert hay_otro_ocupante_activo(db_session, apto.id, papa.id) is True


def test_hay_otro_ocupante_activo_false_si_esta_solo(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")

    assert hay_otro_ocupante_activo(db_session, apto.id, papa.id) is False


def test_hay_otro_ocupante_activo_ignora_dados_de_baja(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo", telefono="3007654321")
    dar_de_baja_ocupante(db_session, hijo)

    assert hay_otro_ocupante_activo(db_session, apto.id, papa.id) is False


# --------------------------------------------------------------------------- #
# ADR-0007 / ticket 02 (.scratch/announce-rapido) -- Ocupante y Principal
# aceptan una Persona solo-WhatsApp (sin Teléfono) como contacto propio.
# --------------------------------------------------------------------------- #
def test_primer_ocupante_con_solo_whatsapp_nace_pending_sin_principal(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", whatsapp_usuario="papa.whats")

    assert papa.es_principal is False
    assert papa.confirmado_en is None
    assert papa.persona_id is not None
    persona = db_session.get(Persona, papa.persona_id)
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "papa.whats"


def test_staff_confirma_al_primero_solo_whatsapp_y_lo_promueve_a_principal(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(db_session, apto, "Papá", whatsapp_usuario="papa.whats")

    confirmado = confirmar_ocupante(db_session, papa, _staff(db_session))

    assert confirmado.confirmado_en is not None
    assert confirmado.es_principal is True


def test_promover_ocupante_solo_whatsapp_a_principal_funciona(db_session):
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, apto, "Hija", whatsapp_usuario="hija.whats")

    promover_a_principal(db_session, hija)
    db_session.refresh(papa)
    db_session.refresh(hija)

    assert hija.es_principal is True
    assert papa.es_principal is False


def test_telefono_y_whatsapp_juntos_guarda_los_dos_en_la_misma_persona(db_session):
    apto = _apto(db_session)
    papa = agregar_ocupante(
        db_session, apto, "Papá", telefono="3001234567", whatsapp_usuario="papa.whats"
    )

    persona = db_session.get(Persona, papa.persona_id)
    assert persona.telefono == "+573001234567"
    assert persona.whatsapp_usuario == "papa.whats"  # no se descarta


def test_telefono_y_whatsapp_juntos_no_pisa_un_whatsapp_ya_existente(db_session):
    from app.domain.persona_service import get_or_create_persona, update_datos_personales

    persona_previa = get_or_create_persona(db_session, "3001234567", "Papá")
    update_datos_personales(db_session, persona_previa, whatsapp_usuario="original.whats")

    apto = _apto(db_session)
    papa = agregar_ocupante(
        db_session, apto, "Papá", telefono="3001234567", whatsapp_usuario="otro.whats"
    )

    persona = db_session.get(Persona, papa.persona_id)
    assert persona.whatsapp_usuario == "original.whats"  # no se pisa


def test_primer_ocupante_sin_telefono_ni_whatsapp_sigue_fallando(db_session):
    apto = _apto(db_session)
    with pytest.raises(ValueError):
        agregar_ocupante(db_session, apto, "Mamá")


# --------------------------------------------------------------------------- #
# Ticket 05 (.scratch/announce-rapido) -- anunciante_para_ocupante: misma
# resolución que telefono_notificacion_ocupante (propio, si no el Principal),
# pero devuelve la Persona completa (no solo el Teléfono) porque el
# Anunciante SÍ puede identificarse por WhatsApp (ADR-0007).
# --------------------------------------------------------------------------- #
def test_anunciante_para_ocupante_con_telefono_propio(db_session):
    apto = _apto(db_session)
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    persona = anunciante_para_ocupante(db_session, hija)

    assert persona is not None
    assert persona.telefono == "+573021112233"


def test_anunciante_para_ocupante_con_solo_whatsapp_propio(db_session):
    apto = _apto(db_session)
    hija = agregar_ocupante(db_session, apto, "Hija", whatsapp_usuario="hija.whats")

    persona = anunciante_para_ocupante(db_session, hija)

    assert persona is not None
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "hija.whats"


def test_anunciante_para_ocupante_sin_contacto_cae_al_principal(db_session):
    apto = _apto(db_session)
    _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto propio

    persona = anunciante_para_ocupante(db_session, hijo)

    assert persona is not None
    assert persona.telefono == "+573001234567"  # el de Papá (Principal)


def test_anunciante_para_ocupante_sin_contacto_ni_principal_confirmado_da_none(db_session):
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")  # pending, NO confirmado
    hijo = agregar_ocupante(db_session, apto, "Hijo")  # sin contacto propio

    persona = anunciante_para_ocupante(db_session, hijo)

    assert persona is None  # todavía no hay Principal confirmado


def test_telefono_notificacion_ocupante_sigue_funcionando_igual_tras_el_refactor(db_session):
    # Mismo comportamiento de siempre (issue histórica, ticket 08 de
    # .scratch/mis-datos) -- el refactor que comparte lógica con
    # anunciante_para_ocupante no debe cambiar esto.
    papa = _agregar_confirmado(db_session, _apto(db_session), "Papá", "3001234567")
    apto = _apto(db_session)
    hijo = agregar_ocupante(db_session, apto, "Hijo")

    assert telefono_notificacion_ocupante(db_session, hijo) == "+573001234567"
    assert telefono_notificacion_ocupante(db_session, papa) == "+573001234567"


# --------------------------------------------------------------------------- #
# Ticket 01 (.scratch/announce-residente-correcto) — reasignar_apartamento:
# la tab "Dirección" de /residentes pasa a crear/ligar un Ocupante en vez de
# escribir Persona.apartamento_actual_id de forma aislada.
# --------------------------------------------------------------------------- #
def test_reasignar_apartamento_a_unidad_vacia_crea_ocupante_confirmado_y_principal(db_session):
    apto = _apto(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    staff = _staff(db_session)

    ocupante = reasignar_apartamento(db_session, persona, apto, staff)

    assert ocupante.apartamento_id == apto.id
    assert ocupante.persona_id == persona.id
    assert ocupante.confirmado_en is not None
    assert ocupante.es_principal is True
    db_session.refresh(persona)
    assert persona.apartamento_actual_id == apto.id


def test_reasignar_apartamento_a_unidad_con_principal_queda_pending(db_session):
    """Issue 161 (.scratch/pendientes-cliente): con la unidad YA ocupada,
    el nuevo Ocupante queda PENDING -- staff puede asignar la unidad, pero
    no salta el paso de confirmación (lo confirma después el Principal, o
    cualquier staff)."""
    apto = _apto(db_session)
    _agregar_confirmado(db_session, apto, "Papá", "3001234567")
    hija = get_or_create_persona(db_session, "3021112233", "Hija")
    staff = _staff(db_session)

    ocupante = reasignar_apartamento(db_session, hija, apto, staff)

    assert ocupante.confirmado_en is None
    assert ocupante.es_principal is False  # Papá se queda de principal


def test_reasignar_apartamento_a_unidad_con_solo_pendientes_queda_pending(db_session):
    """Issue 161 -- mismo criterio con una unidad que YA tiene gente pero
    NADIE confirmado todavía: el nuevo Ocupante igual queda pending, no se
    auto-promueve por haber llegado primero (eso lo decide quién se
    CONFIRMA primero, `confirmar_ocupante`/`promover_al_recibir`, no quién
    llega primero)."""
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Papá", telefono="3001234567")  # pending, sin principal
    hija = get_or_create_persona(db_session, "3021112233", "Hija")
    staff = _staff(db_session)

    ocupante = reasignar_apartamento(db_session, hija, apto, staff)

    assert ocupante.confirmado_en is None
    assert ocupante.es_principal is False


def test_reasignar_apartamento_bloquea_si_ya_es_ocupante_activo_de_otra_unidad(db_session):
    # El mensaje es el mismo que ya usa `agregar_ocupante` para este caso
    # (`_MENSAJE_YA_OCUPANTE_ACTIVO`) -- decisión deliberada: una sola fuente
    # de verdad para "ya es Ocupante activo en otro lado", en vez de que
    # `reasignar_apartamento` duplique su propio texto.
    apto1 = _apto(db_session)
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    staff = _staff(db_session)
    reasignar_apartamento(db_session, persona, apto1, staff)

    with pytest.raises(ValueError, match="ya es Ocupante activo"):
        reasignar_apartamento(db_session, persona, apto2, staff)

    db_session.refresh(persona)
    assert persona.apartamento_actual_id == apto1.id  # no se movió


def test_reasignar_apartamento_a_la_misma_unidad_es_no_op(db_session):
    apto = _apto(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    staff = _staff(db_session)
    original = reasignar_apartamento(db_session, persona, apto, staff)

    otra_vez = reasignar_apartamento(db_session, persona, apto, staff)

    assert otra_vez.id == original.id
    assert listar_ocupantes(db_session, apto) == [original]  # no se duplicó


def test_reasignar_apartamento_none_desvincula_al_ocupante(db_session):
    apto = _apto(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    staff = _staff(db_session)
    ocupante = reasignar_apartamento(db_session, persona, apto, staff)

    resultado = reasignar_apartamento(db_session, persona, None, staff)

    assert resultado is None
    db_session.refresh(ocupante)
    assert ocupante.desvinculado_en is not None
    db_session.refresh(persona)
    assert persona.apartamento_actual_id is None


def test_reasignar_apartamento_none_sin_ocupante_limpia_apartamento_actual_id_huerfano(db_session):
    # Dato huérfano de ANTES de este ticket (apartamento_actual_id puesto a
    # mano, sin Ocupante correspondiente) -- el respaldo debe poder
    # limpiarlo igual, no solo el camino nuevo.
    apto = _apto(db_session)
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    persona.apartamento_actual_id = apto.id
    db_session.flush()

    resultado = reasignar_apartamento(db_session, persona, None, _staff(db_session))

    assert resultado is None
    db_session.refresh(persona)
    assert persona.apartamento_actual_id is None


def test_reasignar_apartamento_none_sin_nada_que_desvincular_no_falla(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")

    resultado = reasignar_apartamento(db_session, persona, None, _staff(db_session))

    assert resultado is None


# --------------------------------------------------------------------------- #
# mover_ocupante (.scratch/ocupante-principal-escenarios, ticket 11)
# --------------------------------------------------------------------------- #
def test_mover_ocupante_no_principal_a_otra_unidad(db_session):
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    _agregar_confirmado(db_session, origen, "Papá", "3001234567")  # principal de origen
    hija = agregar_ocupante(db_session, origen, "Hija", telefono="3021112233")

    movida = mover_ocupante(db_session, hija, destino)

    assert movida.apartamento_id == destino.id
    assert movida.es_principal is False
    db_session.refresh(hija)
    assert hija.desvinculado_en is not None  # la fila anterior queda de baja, histórica


def test_mover_ocupante_conserva_el_telefono(db_session):
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    _agregar_confirmado(db_session, origen, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, origen, "Hija", telefono="3021112233")

    movida = mover_ocupante(db_session, hija, destino)

    persona = db_session.get(Persona, movida.persona_id)
    assert persona.telefono == "+573021112233"
    assert persona.apartamento_actual_id == destino.id


def test_mover_ocupante_sin_contacto_a_unidad_con_gente(db_session):
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    _agregar_confirmado(db_session, origen, "Papá", "3001234567")
    hijo = agregar_ocupante(db_session, origen, "Hijo")  # sin contacto
    _agregar_confirmado(db_session, destino, "Mamá", "3021112233")  # destino no vacío

    movida = mover_ocupante(db_session, hijo, destino)

    assert movida.apartamento_id == destino.id
    assert movida.persona_id is None


def test_mover_ocupante_principal_solo_se_mueve_directo(db_session):
    """Issue 159 (.scratch/pendientes-cliente, revierte el ticket 11 de
    .scratch/ocupante-principal-escenarios): un Principal SOLO en su
    unidad se mueve directo -- no hay a quién degradar, la unidad vieja
    queda vacía."""
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    papa = _agregar_confirmado(db_session, origen, "Papá", "3001234567")  # único activo

    movido = mover_ocupante(db_session, papa, destino)

    assert movido.apartamento_id == destino.id
    assert movido.es_principal is False
    db_session.refresh(papa)
    assert papa.desvinculado_en is not None
    assert listar_ocupantes(db_session, origen) == []


def test_mover_ocupante_principal_con_otro_con_contacto_lo_degrada_y_promueve(db_session):
    """Issue 159 -- con otro Ocupante activo (con contacto propio) en la
    unidad, se lo promueve automáticamente ANTES de mover al Principal, que
    llega al destino como Residente normal."""
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    papa = _agregar_confirmado(db_session, origen, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, origen, "Hija", telefono="3021112233")

    movido = mover_ocupante(db_session, papa, destino)

    assert movido.apartamento_id == destino.id
    assert movido.es_principal is False
    db_session.refresh(hija)
    assert hija.es_principal is True  # promovida en la unidad ORIGEN
    assert hija.apartamento_id == origen.id
    assert hija.confirmado_en is not None


def test_mover_ocupante_principal_sin_candidato_con_contacto_falla(db_session):
    """Issue 159 -- si el único otro Ocupante activo NO tiene Teléfono ni
    WhatsApp propio, no hay a quién promover: se rechaza (degradar a
    alguien sin contacto violaría el invariante de Principal)."""
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    papa = _agregar_confirmado(db_session, origen, "Papá", "3001234567")
    agregar_ocupante(db_session, origen, "Hijo")  # sin contacto

    with pytest.raises(ValueError):
        mover_ocupante(db_session, papa, destino)

    db_session.refresh(papa)
    assert papa.desvinculado_en is None
    assert papa.apartamento_id == origen.id


def test_mover_ocupante_ya_dado_de_baja_falla(db_session):
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    _agregar_confirmado(db_session, origen, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, origen, "Hija", telefono="3021112233")
    dar_de_baja_ocupante(db_session, hija)

    with pytest.raises(ValueError):
        mover_ocupante(db_session, hija, destino)


def test_mover_ocupante_a_unidad_llena_falla(db_session):
    origen = _apto(db_session)
    destino = resolver_apartamento(db_session, "TORRE 2", "202")
    _agregar_confirmado(db_session, origen, "Papá", "3001234567")
    hija = agregar_ocupante(db_session, origen, "Hija", telefono="3021112233")
    for i in range(MAX_OCUPANTES_ACTIVOS):
        agregar_ocupante(db_session, destino, f"Relleno{i}", telefono=f"30500000{i:02d}")

    with pytest.raises(ValueError):
        mover_ocupante(db_session, hija, destino)


def test_ocupante_activo_por_contacto_por_telefono(db_session):
    apto = _apto(db_session)
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    encontrado = ocupante_activo_por_contacto(db_session, telefono="3021112233")

    assert encontrado is not None
    assert encontrado.id == hija.id


def test_ocupante_activo_por_contacto_por_whatsapp(db_session):
    apto = _apto(db_session)
    hija = agregar_ocupante(db_session, apto, "Hija", whatsapp_usuario="hija.whats")

    encontrado = ocupante_activo_por_contacto(db_session, whatsapp_usuario="hija.whats")

    assert encontrado is not None
    assert encontrado.id == hija.id


def test_ocupante_activo_por_contacto_sin_match_es_none(db_session):
    assert ocupante_activo_por_contacto(db_session, telefono="3099999999") is None


def test_mensaje_ya_ocupante_activo_no_principal_menciona_la_unidad(db_session):
    apto = _apto(db_session)
    hija = agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    mensaje = mensaje_ya_ocupante_activo(db_session, hija)

    assert "TORRE 1" in mensaje
    assert "101" in mensaje
    assert "Mover acá" in mensaje


def test_mensaje_ya_ocupante_activo_principal_tambien_ofrece_mover(db_session):
    """Issue 159 (.scratch/pendientes-cliente) -- un Principal ya no queda
    bloqueado en seco: `mover_ocupante` degrada automáticamente si hace
    falta, así que el mensaje ahora también ofrece "Mover acá"."""
    apto = _apto(db_session)
    papa = _agregar_confirmado(db_session, apto, "Papá", "3001234567")

    mensaje = mensaje_ya_ocupante_activo(db_session, papa)

    assert "PRINCIPAL" in mensaje
    assert "Mover acá" in mensaje


def test_identificar_contacto_sin_match_devuelve_encontrado_false(db_session):
    assert identificar_contacto_para_unidad(db_session, "3099999999", None) == {
        "encontrado": False
    }


def test_identificar_contacto_encontrado_sin_conflicto(db_session):
    # Issue 154 -- extraído de `nuevo_residente_identificar` (packages.py)
    # para reusarlo también desde /residentes tab Residentes.
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(db_session, "3021112233", "Hija")

    resultado = identificar_contacto_para_unidad(db_session, "3021112233", None)

    assert resultado == {"encontrado": True, "nombre": "HIJA", "conflicto": None}


def test_identificar_contacto_conflicto_con_otra_unidad(db_session):
    from app.domain.apartamento_service import resolver_apartamento

    apto1 = _apto(db_session)
    hija = agregar_ocupante(db_session, apto1, "Hija", telefono="3021112233")
    apto2 = resolver_apartamento(db_session, "TORRE 2", "202")

    resultado = identificar_contacto_para_unidad(db_session, "3021112233", apto2)

    assert resultado["encontrado"] is True
    assert resultado["conflicto"] == {
        "es_principal": False,
        "torre": "TORRE 1",
        "apartamento": "101",
        "persona_id": str(hija.persona_id),
    }


def test_identificar_contacto_sin_conflicto_si_ya_es_de_esta_misma_unidad(db_session):
    # El "conflicto" es relativo a `apto_actual` -- si el contacto ya es
    # Ocupante de ESA MISMA unidad (no de otra), no hay nada que avisar.
    apto = _apto(db_session)
    agregar_ocupante(db_session, apto, "Hija", telefono="3021112233")

    resultado = identificar_contacto_para_unidad(db_session, "3021112233", apto)

    assert resultado == {"encontrado": True, "nombre": "HIJA", "conflicto": None}
