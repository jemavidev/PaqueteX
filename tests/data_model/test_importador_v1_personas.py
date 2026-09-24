# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 01) --
Personas. Seam: `sincronizar_desde_v1`, contra el Postgres efímero construido
con `alembic upgrade head`, alimentado con instantáneas de la v1 armadas en
memoria.
"""

import pytest

from app.domain.importador_v1_service import (
    ClienteV1,
    InstantaneaV1,
    ModoSincronizacion,
    sincronizar_desde_v1,
)
from app.domain.otp_cliente import OtpCliente
from app.domain.persona import Persona
from app.domain.registro_sms import RegistroSms

pytestmark = pytest.mark.integration


def _cliente(id_v1="c-1", telefono="+573001112233", nombre="Juan Perez", email=None):
    return ClienteV1(id=id_v1, telefono=telefono, nombre=nombre, email=email)


def test_primera_pasada_crea_una_persona_por_cliente_sin_apartamento(db_session):
    instantanea = InstantaneaV1(
        clientes=[
            _cliente("c-1", "+573001112233", "Juan Perez", "juan@example.com"),
            _cliente("c-2", "+573004445566", "Ana Gomez"),
        ]
    )

    reporte = sincronizar_desde_v1(db_session, instantanea)

    assert reporte.personas.creados == 2
    juan = db_session.query(Persona).filter_by(telefono="+573001112233").one()
    assert juan.nombre == "Juan Perez"
    assert juan.email == "juan@example.com"
    assert juan.origen_v1_id == "c-1"
    assert juan.apartamento_actual_id is None
    assert juan.terminos_aceptados_en is None


def test_segunda_pasada_identica_no_cambia_nada(db_session):
    instantanea = InstantaneaV1(clientes=[_cliente()])
    sincronizar_desde_v1(db_session, instantanea)

    reporte = sincronizar_desde_v1(db_session, instantanea)

    assert reporte.personas.creados == 0
    assert reporte.personas.actualizados == 0
    assert reporte.personas.sin_cambios == 1
    assert db_session.query(Persona).count() == 1


def test_la_v1_gana_sobre_lo_editado_en_la_v2(db_session):
    instantanea = InstantaneaV1(clientes=[_cliente(nombre="Juan Perez", email="juan@example.com")])
    sincronizar_desde_v1(db_session, instantanea)
    persona = db_session.query(Persona).one()
    persona.nombre = "Editado en la v2"
    persona.email = None
    db_session.flush()

    reporte = sincronizar_desde_v1(db_session, instantanea)

    assert reporte.personas.actualizados == 1
    db_session.refresh(persona)
    assert persona.nombre == "Juan Perez"
    assert persona.email == "juan@example.com"


def test_un_cambio_de_nombre_en_la_v1_llega_a_la_v2(db_session):
    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente(nombre="Juan Perez")]))

    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente(nombre="JUAN PEREZ GOMEZ")]))

    assert db_session.query(Persona).one().nombre == "JUAN PEREZ GOMEZ"


def test_adopta_la_persona_nativa_con_el_mismo_telefono(db_session):
    nativa = Persona(telefono="+573001112233", nombre="Prueba en la v2")
    db_session.add(nativa)
    db_session.flush()

    reporte = sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente(nombre="Juan Perez")]))

    assert reporte.personas.adoptados == 1
    assert reporte.personas.creados == 0
    assert db_session.query(Persona).count() == 1
    db_session.refresh(nativa)
    assert nativa.origen_v1_id == "c-1"
    assert nativa.nombre == "Juan Perez"


def test_no_toca_las_personas_nativas_que_no_vienen_de_la_v1(db_session):
    nativa = Persona(telefono="+573009998877", nombre="Solo en la v2")
    db_session.add(nativa)
    db_session.flush()

    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente()]))

    db_session.refresh(nativa)
    assert nativa.nombre == "Solo en la v2"
    assert nativa.origen_v1_id is None


def test_un_telefono_invalido_se_reporta_sin_frenar_la_pasada(db_session):
    instantanea = InstantaneaV1(
        clientes=[
            _cliente("c-malo", "12", "Telefono Roto"),
            _cliente("c-bueno", "+573004445566", "Ana Gomez"),
        ]
    )

    reporte = sincronizar_desde_v1(db_session, instantanea)

    assert reporte.personas.creados == 1
    assert any("c-malo" in e for e in reporte.errores)
    assert db_session.query(Persona).filter_by(origen_v1_id="c-bueno").count() == 1


def test_un_telefono_que_ya_tiene_otra_persona_se_reporta_como_choque(db_session):
    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente("c-1", "+573001112233")]))
    db_session.add(Persona(telefono="+573009998877", nombre="Otra, ya importada o nativa", origen_v1_id="c-otro"))
    db_session.flush()

    # En la v1, c-1 cambió al teléfono que en la v2 ya es de otra Persona.
    reporte = sincronizar_desde_v1(
        db_session,
        InstantaneaV1(
            clientes=[
                _cliente("c-1", "+573009998877"),
                _cliente("c-2", "+573004445566"),
                _cliente("c-otro", "+573009998877", "Otra, ya importada o nativa"),
            ]
        ),
    )

    assert any("c-1" in c for c in reporte.choques)
    assert reporte.personas.creados == 1  # c-2 entra igual
    assert db_session.query(Persona).filter_by(origen_v1_id="c-1").one().telefono == "+573001112233"


def test_un_cliente_nuevo_con_telefono_de_otra_persona_importada_es_choque(db_session):
    db_session.add(Persona(telefono="+573001112233", nombre="Importada antes", origen_v1_id="c-viejo"))
    db_session.flush()

    # En la v1, c-viejo cambió de teléfono y c-nuevo tomó el suyo: c-nuevo se
    # procesa primero y choca con c-viejo, que todavía no se actualizó.
    reporte = sincronizar_desde_v1(
        db_session,
        InstantaneaV1(clientes=[_cliente("c-nuevo", "+573001112233"), _cliente("c-viejo", "+573007776655")]),
    )

    assert any("c-nuevo" in c for c in reporte.choques)
    assert reporte.personas.creados == 0


def test_simular_reporta_lo_mismo_sin_dejar_nada_escrito(db_session):
    instantanea = InstantaneaV1(clientes=[_cliente("c-1"), _cliente("c-2", "+573004445566", "Ana Gomez")])

    reporte = sincronizar_desde_v1(db_session, instantanea, modo=ModoSincronizacion.SIMULAR)

    assert reporte.personas.creados == 2
    assert db_session.query(Persona).count() == 0


def test_simular_no_revierte_lo_que_ya_estaba_en_la_sesion(db_session):
    db_session.add(Persona(telefono="+573009998877", nombre="Ya estaba"))
    db_session.flush()

    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente()]), modo=ModoSincronizacion.SIMULAR)

    assert db_session.query(Persona).filter_by(nombre="Ya estaba").count() == 1


def test_importar_no_registra_ningun_aviso_ni_otp(db_session):
    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[_cliente()]))

    assert db_session.query(RegistroSms).count() == 0
    assert db_session.query(OtpCliente).count() == 0
