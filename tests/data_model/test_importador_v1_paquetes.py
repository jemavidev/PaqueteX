# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 02) --
Paquetes y sus códigos. Seam: `sincronizar_desde_v1`, contra el Postgres
efímero, con instantáneas de la v1 armadas en memoria.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.importador_v1_service import (
    AnuncioV1,
    ClienteV1,
    InstantaneaV1,
    PaqueteV1,
    sincronizar_desde_v1,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
CLIENTE = ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan Perez")


def _paquete(id_v1=1, codigo="HSZN", estado="ENTREGADO", **extra):
    base = dict(
        id=id_v1,
        cliente_id="c-1",
        tracking_number=codigo,
        guide_number="GUIA-123",
        display_name=None,
        estado=estado,
        package_type="NORMAL",
        package_condition="BUENO",
        announced_at=T0,
        received_at=T0 + timedelta(hours=1),
        delivered_at=T0 + timedelta(days=1) if estado == "ENTREGADO" else None,
        cancelled_at=None,
    )
    base.update(extra)
    return PaqueteV1(**base)


def _sincronizar(db_session, paquetes=(), anuncios=(), clientes=(CLIENTE,)):
    return sincronizar_desde_v1(
        db_session,
        InstantaneaV1(clientes=list(clientes), paquetes=list(paquetes), anuncios=list(anuncios)),
    )


def test_crea_el_paquete_con_el_codigo_que_conoce_el_residente(db_session):
    reporte = _sincronizar(db_session, paquetes=[_paquete(display_name="Maria Perez")])

    assert reporte.paquetes.creados == 1
    paquete = db_session.query(Paquete).one()
    persona = db_session.query(Persona).one()
    assert paquete.access_code == "HSZN"
    assert paquete.origen_v1_id == "1"
    assert paquete.announced_by_persona_id == persona.id
    assert paquete.announced_by_phone == "+573001112233"
    assert paquete.recipient_name == "Maria Perez"
    assert paquete.recipient_phone == "+573001112233"
    assert paquete.guide_number == "GUIA-123"
    assert paquete.estado == EstadoPaquete.ENTREGADO
    assert paquete.package_type.value == "NORMAL"
    assert paquete.package_condition.value == "BUENO"
    assert paquete.delivered_at == T0 + timedelta(days=1)
    assert (paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento) == (None, None, None)


def test_sin_display_name_el_destinatario_es_el_cliente(db_session):
    _sincronizar(db_session, paquetes=[_paquete(display_name=None)])

    assert db_session.query(Paquete).one().recipient_name == "Juan Perez"


def _paquete_nativo(db_session, codigo):
    persona = Persona(telefono="+573009998877", nombre="Prueba v2")
    db_session.add(persona)
    db_session.flush()
    nativo = Paquete(
        access_code=codigo,
        announced_by_persona_id=persona.id,
        recipient_name="Prueba v2",
        estado=EstadoPaquete.RECIBIDO,
        announced_at=T0,
    )
    db_session.add(nativo)
    db_session.flush()
    return nativo


def test_segunda_pasada_identica_no_cambia_nada(db_session):
    _sincronizar(db_session, paquetes=[_paquete()])

    reporte = _sincronizar(db_session, paquetes=[_paquete()])

    assert (reporte.paquetes.creados, reporte.paquetes.actualizados, reporte.paquetes.sin_cambios) == (0, 0, 1)
    assert db_session.query(Paquete).count() == 1


def test_un_cambio_de_estado_en_la_v1_llega_y_revierte_lo_hecho_en_la_v2(db_session):
    _sincronizar(db_session, paquetes=[_paquete(estado="RECIBIDO")])
    paquete = db_session.query(Paquete).one()
    paquete.recipient_name = "Editado en la v2"
    db_session.flush()

    _sincronizar(db_session, paquetes=[_paquete(estado="ENTREGADO")])

    db_session.refresh(paquete)
    assert paquete.estado == EstadoPaquete.ENTREGADO
    assert paquete.delivered_at == T0 + timedelta(days=1)
    assert paquete.recipient_name == "Juan Perez"


def test_el_sufijo_de_anio_de_migrar_anio_no_se_deshace(db_session):
    _sincronizar(db_session, paquetes=[_paquete(codigo="HSZN")])
    paquete = db_session.query(Paquete).one()
    paquete.access_code = "HSZN25"
    db_session.flush()

    _sincronizar(db_session, paquetes=[_paquete(codigo="HSZN")])

    db_session.refresh(paquete)
    assert paquete.access_code == "HSZN25"


def test_una_correccion_de_apartamento_hecha_en_la_v2_se_respeta(db_session):
    _sincronizar(db_session, paquetes=[_paquete()])
    paquete = db_session.query(Paquete).one()
    paquete.snapshot_torre, paquete.snapshot_apartamento = "TORRE 3", "301"
    db_session.flush()

    _sincronizar(db_session, paquetes=[_paquete()])

    db_session.refresh(paquete)
    assert (paquete.snapshot_torre, paquete.snapshot_apartamento) == ("TORRE 3", "301")


def test_choque_con_un_paquete_nativo_le_cambia_el_codigo_al_nativo(db_session):
    nativo = _paquete_nativo(db_session, "HSZN")

    reporte = _sincronizar(db_session, paquetes=[_paquete(codigo="HSZN")])

    db_session.refresh(nativo)
    assert nativo.access_code != "HSZN"
    assert len(nativo.access_code) == 4
    assert db_session.query(Paquete).filter_by(origen_v1_id="1").one().access_code == "HSZN"
    assert any("HSZN" in c for c in reporte.choques)


def test_choque_con_otro_paquete_importado_no_crea_y_se_reporta(db_session):
    _sincronizar(db_session, paquetes=[_paquete(id_v1=1, codigo="HSZN")])

    reporte = _sincronizar(
        db_session, paquetes=[_paquete(id_v1=1, codigo="HSZN"), _paquete(id_v1=2, codigo="HSZN")]
    )

    assert reporte.paquetes.creados == 0
    assert any("paquete v1 2" in c for c in reporte.choques)


def test_los_paquetes_nativos_no_se_tocan(db_session):
    nativo = _paquete_nativo(db_session, "AAAA")

    _sincronizar(db_session, paquetes=[_paquete(codigo="HSZN")])

    db_session.refresh(nativo)
    assert nativo.access_code == "AAAA"
    assert nativo.recipient_name == "Prueba v2"
    assert nativo.origen_v1_id is None


def test_un_anuncio_pendiente_de_la_v1_llega_como_anunciado(db_session):
    anuncio = AnuncioV1(
        id=50, cliente_id="c-1", tracking_code="W6JQ", guide_number=None,
        nombre_destinatario="Maria Perez", announced_at=T0,
    )

    reporte = _sincronizar(db_session, anuncios=[anuncio])

    assert reporte.paquetes.creados == 1
    paquete = db_session.query(Paquete).one()
    assert paquete.access_code == "W6JQ"
    assert paquete.estado == EstadoPaquete.ANUNCIADO
    assert paquete.recipient_name == "Maria Perez"
    assert paquete.origen_v1_id == "anuncio:50"


def test_cuando_la_v1_recibe_el_anuncio_el_mismo_paquete_avanza(db_session):
    anuncio = AnuncioV1(
        id=50, cliente_id="c-1", tracking_code="W6JQ", guide_number=None,
        nombre_destinatario=None, announced_at=T0,
    )
    _sincronizar(db_session, anuncios=[anuncio])
    id_original = db_session.query(Paquete).one().id

    reporte = _sincronizar(
        db_session, paquetes=[_paquete(id_v1=7, codigo="W6JQ", estado="RECIBIDO", anuncio_id=50)]
    )

    assert reporte.paquetes.creados == 0
    paquete = db_session.query(Paquete).one()
    assert paquete.id == id_original
    assert paquete.origen_v1_id == "7"
    assert paquete.estado == EstadoPaquete.RECIBIDO


def test_datos_invalidos_o_cliente_ausente_se_reportan_sin_frenar(db_session):
    reporte = _sincronizar(
        db_session,
        paquetes=[
            _paquete(id_v1=1, codigo="AAAA", estado="PERDIDO"),
            _paquete(id_v1=2, codigo="BBBB", cliente_id="c-inexistente"),
            _paquete(id_v1=3, codigo="CCCC"),
        ],
    )

    assert reporte.paquetes.creados == 1
    assert any("paquete v1 1" in e for e in reporte.errores)
    assert any("paquete v1 2" in e for e in reporte.errores)
