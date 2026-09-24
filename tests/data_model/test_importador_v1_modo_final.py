# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 08) -- modo
final: la pasada del día del corte solo termina bien si el espejo quedó
completo y limpio; ante cualquier choque, error o alerta, falla y no deja nada
escrito. Seam: `sincronizar_desde_v1`, contra el Postgres efímero.
"""

from datetime import datetime, timezone

import pytest

from app.domain.importador_v1_service import (
    ClienteV1,
    InstantaneaV1,
    ModoSincronizacion,
    PaqueteV1,
    sincronizar_desde_v1,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
CLIENTE = ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan Perez")


def _paquete(id_v1=1, codigo="HSZN", estado="RECIBIDO", cliente_id="c-1"):
    return PaqueteV1(
        id=id_v1, cliente_id=cliente_id, tracking_number=codigo, guide_number=None, display_name=None,
        estado=estado, package_type="NORMAL", package_condition="BUENO", announced_at=T0, received_at=T0,
    )


def _final(db_session, clientes=(CLIENTE,), paquetes=()):
    return sincronizar_desde_v1(
        db_session,
        InstantaneaV1(clientes=list(clientes), paquetes=list(paquetes)),
        modo=ModoSincronizacion.FINAL,
    )


def test_una_pasada_final_limpia_escribe_y_termina_bien(db_session):
    reporte = _final(db_session, paquetes=[_paquete()])

    assert reporte.exitosa
    assert db_session.query(Paquete).count() == 1


def test_un_error_hace_fallar_la_pasada_final_sin_dejar_nada_escrito(db_session):
    reporte = _final(db_session, paquetes=[_paquete(1), _paquete(2, "BBBB", cliente_id="c-inexistente")])

    assert not reporte.exitosa
    assert reporte.errores
    assert db_session.query(Paquete).count() == 0
    assert db_session.query(Persona).count() == 0


def test_un_choque_de_codigo_hace_fallar_la_pasada_final(db_session):
    nativa = Persona(telefono="+573009998877", nombre="Prueba v2")
    db_session.add(nativa)
    db_session.flush()
    db_session.add(Paquete(access_code="HSZN", announced_by_persona_id=nativa.id, recipient_name="Prueba v2",
                           estado=EstadoPaquete.RECIBIDO, announced_at=T0))
    db_session.flush()

    reporte = _final(db_session, paquetes=[_paquete(codigo="HSZN")])

    assert not reporte.exitosa
    assert reporte.choques
    assert db_session.query(Paquete).filter_by(access_code="HSZN").one().origen_v1_id is None


def test_en_modo_normal_los_errores_no_impiden_escribir_lo_demas(db_session):
    reporte = sincronizar_desde_v1(
        db_session,
        InstantaneaV1(clientes=[CLIENTE], paquetes=[_paquete(1), _paquete(2, "BBBB", cliente_id="c-inexistente")]),
    )

    assert reporte.exitosa  # la pasada normal "termina bien" aunque reporte errores
    assert db_session.query(Paquete).count() == 1


def test_una_alerta_de_borrado_nunca_es_exitosa(db_session):
    sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[CLIENTE], paquetes=[_paquete()]))

    reporte = sincronizar_desde_v1(db_session, InstantaneaV1(clientes=[CLIENTE], paquetes=[]))

    assert not reporte.exitosa
