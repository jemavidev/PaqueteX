# -*- coding: utf-8 -*-
"""
Sincronización de snapshot entre paquetes "hermanos" del mismo destinatario
(.scratch/paquetes-residentes-conexion) -- caso real reportado: "TOMAS
LIBANO" con 2 paquetes ANUNCIADO/RECIBIDO que deberían compartir apartamento
y no lo hacían, porque `Paquete.snapshot_*`/`recipient_*` son texto congelado
(ADR-0001) sin ninguna cascada hacia otros Paquetes del mismo destinatario.

Comportamiento observable: al llamar `sincronizar_snapshot_a_hermanos` con el
id de una Persona cuyo dato relevante ya cambió, sus paquetes hermanos en
`ESTADOS_CORREGIBLES` con destinatario CONFIRMADO a esa misma Persona reciben
el mismo apartamento/teléfono/nombre -- nunca los que ya son ENTREGADO/
CANCELADO, y nunca los que no tienen a esa Persona confirmada como
destinatario (sin cascada por match heurístico de teléfono/nombre).

`recipient_name` SÍ se propaga (ver el docstring del módulo para el porqué
hacía falta separar resolver/aplicar en 2 pasos: la confirmación debe
resolverse ANTES de renombrar, no después -- de lo contrario el propio
renombre invalida el criterio de confirmación de los paquetes que
necesitaban el cambio). El atajo de un solo paso (`sincronizar_snapshot_a_
hermanos`) sigue existiendo para los triggers que corrigen el PAQUETE, no la
Persona -- ver `test_sincronizar_en_un_solo_paso_no_propaga_nombre_si_ya_se_
renombro_antes` para el caso donde ese atajo NO alcanza.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import agregar_ocupante
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import corregir_apartamento, corregir_destinatario, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.paquete_sincronizacion_service import (
    aplicar_snapshot_de_persona,
    paquetes_hermanos_confirmados,
    sincronizar_snapshot_a_hermanos,
)
from app.domain.persona import Persona
from app.domain.staff_service import create_initial_admin

pytestmark = pytest.mark.integration


def _anunciar_yo_mismo(session, tel="3009998877", nombre="Tomas Libano"):
    return announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )


def _staff(session):
    admin = create_initial_admin(session, "staff@club.com", "Operador", "Contrasena1")
    session.commit()
    return admin


def test_asignar_apartamento_a_un_paquete_propaga_al_hermano_sin_apartamento(db_session):
    staff = _staff(db_session)
    p1 = _anunciar_yo_mismo(db_session)
    p2 = _anunciar_yo_mismo(db_session)
    db_session.commit()
    assert p1.snapshot_apartamento is None
    assert p2.snapshot_apartamento is None

    # Mismo efecto real que "Asignar apartamento" en /paquetes cuando el
    # destinatario es "para mí mismo": corrige el snapshot del paquete Y
    # vincula a la Persona como Ocupante real de esa unidad (lo que fija
    # `Persona.apartamento_actual_id` -- sin esto, la propagación no tendría
    # de dónde leer "dónde vive ahora").
    persona = db_session.get(Persona, p1.announced_by_persona_id)
    apto = resolver_apartamento(db_session, "TORRE 2", "302")
    corregir_apartamento(db_session, p2, staff, apto)
    agregar_ocupante(db_session, apto, persona.nombre, telefono=persona.telefono)
    db_session.commit()

    sincronizar_snapshot_a_hermanos(db_session, p1.announced_by_persona_id, staff)
    db_session.commit()

    db_session.expire_all()
    assert p1.snapshot_conjunto == apto.conjunto
    assert p1.snapshot_torre == apto.torre
    assert p1.snapshot_apartamento == apto.apartamento


def test_no_propaga_a_paquetes_entregados_o_cancelados(db_session):
    staff = _staff(db_session)
    p_entregado = _anunciar_yo_mismo(db_session)
    p_vivo = _anunciar_yo_mismo(db_session)
    db_session.commit()
    receive(db_session, p_entregado, staff, "GUIA-1")
    deliver(db_session, p_entregado, staff)
    db_session.commit()
    assert p_entregado.estado is EstadoPaquete.ENTREGADO

    apto = resolver_apartamento(db_session, "TORRE 3", "101")
    corregir_apartamento(db_session, p_vivo, staff, apto)
    db_session.commit()

    sincronizar_snapshot_a_hermanos(db_session, p_vivo.announced_by_persona_id, staff)
    db_session.commit()

    db_session.expire_all()
    # El paquete ya ENTREGADO es historial cerrado -- su snapshot document el
    # contexto real de ESA entrega, nunca se toca después del hecho.
    assert p_entregado.snapshot_apartamento is None


def test_propaga_telefono_al_hermano_confirmado(db_session):
    staff = _staff(db_session)
    p1 = _anunciar_yo_mismo(db_session, tel="3001112222")
    p2 = _anunciar_yo_mismo(db_session, tel="3001112222")
    db_session.commit()

    persona = db_session.get(Persona, p1.announced_by_persona_id)
    # Simula lo que hace `/residentes` al cambiar el teléfono de una Persona
    # -- acá se muta directo (esta suite prueba el servicio de dominio en
    # aislado, la ruta real de /residentes se prueba en su propio archivo
    # de tests web).
    persona.telefono = "3009990000"
    db_session.commit()

    sincronizar_snapshot_a_hermanos(db_session, persona.id, staff)
    db_session.commit()

    db_session.expire_all()
    assert p1.recipient_phone == "3009990000"
    assert p2.recipient_phone == "3009990000"


def test_propaga_recipient_name_resolviendo_antes_de_renombrar(db_session):
    # Punto 1 corregido: `recipient_name` SÍ se propaga, siempre que el
    # caller resuelva "quién está confirmado" ANTES de renombrar a la
    # Persona y aplique el nombre nuevo DESPUÉS -- mismo orden que ya usan
    # las rutas reales de `/residentes` (`customers_manage_update`).
    staff = _staff(db_session)
    p1 = _anunciar_yo_mismo(db_session, nombre="Tomas Libano")
    db_session.commit()
    persona = db_session.get(Persona, p1.announced_by_persona_id)

    hermanos = paquetes_hermanos_confirmados(db_session, persona)
    persona.nombre = "TOMAS LIBANO ACTUALIZADO"  # ya normalizado -- `update_datos_personales` es quien normaliza en la ruta real
    db_session.commit()
    modificados = aplicar_snapshot_de_persona(db_session, hermanos, persona, staff)
    db_session.commit()

    db_session.expire_all()
    assert p1.recipient_name == "TOMAS LIBANO ACTUALIZADO"
    assert [p.id for p in modificados] == [p1.id]


def test_sincronizar_en_un_solo_paso_no_propaga_nombre_si_ya_se_renombro_antes(db_session):
    # Por qué el atajo de un solo paso NO alcanza para renombres: llamarlo
    # DESPUÉS de que la Persona ya se renombró ve el nombre YA NUEVO al
    # resolver candidatos (`Ocupante.nombre` ya se actualizó en cascada,
    # `persona_service.update_datos_personales`, issue 189) -- el paquete
    # con el nombre VIEJO deja de "coincidir" justo cuando más lo necesita.
    # Por eso `/residentes` nunca usa este atajo para sus 3 triggers, solo
    # las 2 funciones por separado en el orden correcto (ver el test de
    # arriba).
    staff = _staff(db_session)
    p1 = _anunciar_yo_mismo(db_session, nombre="Tomas Libano")
    db_session.commit()

    persona = db_session.get(Persona, p1.announced_by_persona_id)
    nombre_original = p1.recipient_name
    persona.nombre = "Tomas Libano Actualizado"
    db_session.commit()

    modificados = sincronizar_snapshot_a_hermanos(db_session, persona.id, staff)

    db_session.expire_all()
    assert p1.recipient_name == nombre_original
    assert modificados == []


def test_no_propaga_a_hermano_sin_destinatario_confirmado(db_session):
    # `p2` lo anuncia el mismo teléfono, pero para "Alguien Random" -- sin
    # Apartamento resuelto todavía, el único candidato posible es el propio
    # Anunciante, así que ese destinatario NUNCA está confirmado a él. El
    # sync no debe tocarlo aunque comparta anunciante/teléfono con `p1`.
    staff = _staff(db_session)
    p1 = _anunciar_yo_mismo(db_session, tel="3005556666", nombre="Tomas Libano")
    p2 = announce(
        db_session,
        anunciante_telefono="3005556666",
        anunciante_nombre="Tomas Libano",
        destinatario=Destinatario.solo_nombre("Alguien Random"),
    )
    db_session.commit()

    apto = resolver_apartamento(db_session, "TORRE 5", "501")
    corregir_apartamento(db_session, p1, staff, apto)
    persona = db_session.get(Persona, p1.announced_by_persona_id)
    agregar_ocupante(db_session, apto, persona.nombre, telefono=persona.telefono)
    db_session.commit()

    sincronizar_snapshot_a_hermanos(db_session, persona.id, staff)
    db_session.commit()

    db_session.expire_all()
    assert p2.snapshot_apartamento is None


def test_no_contamina_hermano_confirmado_a_otra_persona_del_mismo_anunciante(db_session):
    # Bug real encontrado construyendo esto (caso "LAIS HERNANDEZ"/"RAFAEL
    # TORRES"): un Anunciante (ej. un portero) que anuncia paquetes para 2
    # residentes reales DISTINTOS de la misma unidad NO debe "sincronizar"
    # el paquete de uno con los datos del otro, ni con los suyos propios --
    # cada paquete solo debe seguir a la Persona a la que su destinatario
    # está REALMENTE confirmado (`persona_confirmada_del_destinatario`), no
    # a "cualquier candidato real" (`destinatario_coincide_con_candidato_
    # real` por sí sola no alcanza para esto).
    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 9", "901")
    rafael = agregar_ocupante(db_session, apto, "Rafael Torres", telefono="3011112222")
    agregar_ocupante(db_session, apto, "Lais Hernandez", telefono="3033334444")
    db_session.commit()

    p_rafael = announce(
        db_session, anunciante_telefono="3009990000", anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Rafael Torres"), apartamento=apto,
    )
    p_lais = announce(
        db_session, anunciante_telefono="3009990000", anunciante_nombre="Portero",
        destinatario=Destinatario.solo_nombre("Lais Hernandez"), apartamento=apto,
    )
    db_session.commit()
    corregir_destinatario(db_session, p_rafael, staff, "RAFAEL TORRES", "+573011112222")
    corregir_destinatario(db_session, p_lais, staff, "LAIS HERNANDEZ", "+573033334444")
    db_session.commit()

    portero = db_session.get(Persona, p_rafael.announced_by_persona_id)
    portero.telefono = "3009991111"  # el portero cambia SU propio teléfono
    db_session.commit()

    sincronizar_snapshot_a_hermanos(db_session, portero.id, staff)
    db_session.commit()

    db_session.expire_all()
    # Ninguno de los 2 paquetes de residentes reales debe seguir al
    # portero -- su destinatario no está confirmado a él.
    assert p_rafael.recipient_phone == "+573011112222"
    assert p_lais.recipient_phone == "+573033334444"

    # Sincronizar por Rafael específicamente solo toca SU paquete -- flujo
    # de 2 fases (Rafael NO es el anunciante de `p_rafael`, así que `paquetes_
    # abiertos_de_persona` solo lo encuentra por `recipient_phone ==
    # persona.telefono`; resolver DESPUÉS de cambiar ese mismo teléfono lo
    # dejaría sin encontrar, mismo motivo de fondo que ya motivó separar
    # resolver/aplicar en 2 pasos).
    persona_rafael = db_session.get(Persona, rafael.persona_id)
    hermanos_rafael = paquetes_hermanos_confirmados(db_session, persona_rafael)
    persona_rafael.telefono = "3011119999"
    db_session.commit()
    aplicar_snapshot_de_persona(db_session, hermanos_rafael, persona_rafael, staff)
    db_session.commit()

    db_session.expire_all()
    assert p_rafael.recipient_phone == "3011119999"
    assert p_lais.recipient_phone == "+573033334444"  # intacto
