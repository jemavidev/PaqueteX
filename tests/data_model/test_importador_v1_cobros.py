# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 04) --
cobros históricos: copia fiel de lo que cobró la v1, sin recalcular. Seam:
`sincronizar_desde_v1`, contra el Postgres efímero.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.cobro import Cobro
from app.domain.estadisticas_tablero_service import calcular_tablero
from app.domain.importador_v1_service import (
    OPERADOR_V1_NOMBRE,
    ClienteV1,
    HistorialV1,
    InstantaneaV1,
    PaqueteV1,
    sincronizar_desde_v1,
)
from app.domain.paquete import Paquete
from app.domain.registro_sms import RegistroSms
from app.domain.saldo_contra_entrega import MovimientoSaldoContraEntrega
from app.domain.usuario import Usuario

pytestmark = pytest.mark.integration

ENTREGA = datetime(2026, 9, 15, 17, 0, tzinfo=timezone.utc)  # 12:00 en Bogotá
CLIENTE = ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan Perez")


def _paquete(id_v1=1, estado="ENTREGADO", total=Decimal("1500.00"), tipo="NORMAL"):
    return PaqueteV1(
        id=id_v1, cliente_id="c-1", tracking_number=f"C{id_v1:03d}", guide_number=None,
        display_name=None, estado=estado, package_type=tipo, package_condition="BUENO",
        announced_at=ENTREGA - timedelta(days=2), received_at=ENTREGA - timedelta(days=1),
        delivered_at=ENTREGA if estado == "ENTREGADO" else None, total_amount=total,
    )


def _sincronizar(db_session, *paquetes):
    historial = [
        HistorialV1(paquete_id=p.id, estado="ENTREGADO", changed_by="operator_1", changed_at=ENTREGA)
        for p in paquetes
        if p.estado == "ENTREGADO"
    ]
    return sincronizar_desde_v1(
        db_session, InstantaneaV1(clientes=[CLIENTE], paquetes=list(paquetes), historial=historial)
    )


def test_cada_paquete_entregado_tiene_su_cobro_fiel_a_la_v1(db_session):
    reporte = _sincronizar(
        db_session, _paquete(1, total=Decimal("1500.00")), _paquete(2, total=Decimal("2000.00"), tipo="EXTRA_DIMENSIONADO")
    )

    assert reporte.cobros.creados == 2
    cobro = db_session.query(Cobro).join(Paquete, Paquete.id == Cobro.paquete_id).filter(Paquete.origen_v1_id == "1").one()
    tecnico = db_session.query(Usuario).filter_by(nombre=OPERADOR_V1_NOMBRE).one()
    assert (cobro.monto_base, cobro.bloques_bodegaje, cobro.monto_bodegaje, cobro.monto_total) == (1500, 0, 0, 1500)
    assert cobro.motivo_anulacion is None
    assert cobro.cobrado_en == ENTREGA
    assert cobro.cobrado_por_usuario_id == tecnico.id


def test_los_paquetes_no_entregados_no_tienen_cobro(db_session):
    _sincronizar(db_session, _paquete(1, estado="RECIBIDO"))

    assert db_session.query(Cobro).count() == 0


def test_la_primera_entrega_no_se_exime_se_copia_lo_que_cobro_la_v1(db_session):
    """La v2 exime la primera entrega de cada teléfono; la v1 no lo hacía y el
    cobro importado es copia fiel de la v1 (spec, historia 38)."""
    _sincronizar(db_session, _paquete(1))

    assert db_session.query(Cobro).one().monto_total == 1500


def test_cuando_la_v1_entrega_el_paquete_aparece_el_cobro(db_session):
    _sincronizar(db_session, _paquete(1, estado="RECIBIDO"))

    reporte = _sincronizar(db_session, _paquete(1, estado="ENTREGADO"))

    assert reporte.cobros.creados == 1
    assert db_session.query(Cobro).count() == 1


def test_segunda_pasada_no_duplica_el_cobro(db_session):
    _sincronizar(db_session, _paquete(1))

    reporte = _sincronizar(db_session, _paquete(1))

    assert reporte.cobros.creados == 0
    assert reporte.cobros.sin_cambios == 1
    assert db_session.query(Cobro).count() == 1


def test_si_en_la_v1_deja_de_estar_entregado_el_cobro_se_borra_y_se_reporta(db_session):
    _sincronizar(db_session, _paquete(1))

    reporte = _sincronizar(db_session, _paquete(1, estado="RECIBIDO"))

    assert db_session.query(Cobro).count() == 0
    assert reporte.cobros.borrados == 1
    assert any("paquete v1 1" in e for e in reporte.errores)


def test_importar_cobros_no_crea_movimientos_de_saldo_ni_avisos(db_session):
    _sincronizar(db_session, _paquete(1))

    assert db_session.query(MovimientoSaldoContraEntrega).count() == 0
    assert db_session.query(RegistroSms).count() == 0


def test_las_estadisticas_de_cobro_incluyen_los_cobros_importados(db_session):
    _sincronizar(db_session, _paquete(1, total=Decimal("1500.00")), _paquete(2, total=Decimal("2000.00")))

    tablero = calcular_tablero(db_session, ENTREGA + timedelta(hours=1))

    assert tablero.panorama.ingresos.hoy == 3500
