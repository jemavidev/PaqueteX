# -*- coding: utf-8 -*-
"""
Cálculo puro del cobro por recepción de un paquete -- Seam 1 del módulo
"Gestión de cobro y bodegaje" (.scratch/cobro-bodegaje).

`calcular_cobro` no toca la base de datos: se prueba con instancias armadas a
mano, sin sesión ni fixtures de integración.
"""

from datetime import datetime, timedelta, timezone

from app.domain.cobro_service import DesgloseCobro, calcular_cobro
from app.domain.paquete import Paquete, TipoPaquete
from app.domain.tarifa_cobro import TarifaCobro

_AHORA = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _tarifas(**overrides) -> TarifaCobro:
    defaults = dict(
        base_normal=1500,
        base_extra_dimensionado=2000,
        bodegaje_normal_24h=1000,
        bodegaje_extra_dimensionado_24h=1500,
    )
    defaults.update(overrides)
    return TarifaCobro(**defaults)


def _paquete(package_type=TipoPaquete.NORMAL, horas_desde_recibido=None) -> Paquete:
    received_at = None
    if horas_desde_recibido is not None:
        received_at = _AHORA - timedelta(hours=horas_desde_recibido)
    return Paquete(
        package_type=package_type,
        received_at=received_at,
        recipient_phone="+573001234567",
        recipient_name="Ana",
        access_code="AB12",
    )


def test_cargo_base_normal_sin_bodegaje_ni_primera_entrega():
    p = _paquete(horas_desde_recibido=10)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose == DesgloseCobro(
        monto_base=1500, bloques_bodegaje=0, monto_bodegaje=0, monto_total=1500
    )


def test_cargo_base_extra_dimensionado():
    p = _paquete(package_type=TipoPaquete.EXTRA_DIMENSIONADO, horas_desde_recibido=10)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.monto_base == 2000


def test_primera_entrega_exime_el_cargo_base():
    p = _paquete(horas_desde_recibido=10)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=True)
    assert desglose.monto_base == 0


def test_primera_entrega_no_exime_el_bodegaje():
    p = _paquete(horas_desde_recibido=50)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=True)
    assert desglose.monto_base == 0
    assert desglose.monto_bodegaje == 1000
    assert desglose.monto_total == 1000


def test_sin_bodegaje_a_las_48_horas_exactas():
    p = _paquete(horas_desde_recibido=48)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.bloques_bodegaje == 0
    assert desglose.monto_bodegaje == 0


def test_un_bloque_a_las_48_horas_y_un_minuto():
    p = _paquete(horas_desde_recibido=48 + 1 / 60)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.bloques_bodegaje == 1
    assert desglose.monto_bodegaje == 1000


def test_sigue_en_un_bloque_a_las_71_horas_59():
    p = _paquete(horas_desde_recibido=71 + 59 / 60)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.bloques_bodegaje == 1


def test_dos_bloques_a_las_72_horas_y_un_minuto():
    p = _paquete(horas_desde_recibido=72 + 1 / 60)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.bloques_bodegaje == 2
    assert desglose.monto_bodegaje == 2000


def test_tarifa_de_bodegaje_distinta_por_tipo():
    p_normal = _paquete(package_type=TipoPaquete.NORMAL, horas_desde_recibido=50)
    p_extra = _paquete(package_type=TipoPaquete.EXTRA_DIMENSIONADO, horas_desde_recibido=50)
    tarifas = _tarifas()
    assert (
        calcular_cobro(p_normal, tarifas, _AHORA, es_primera_entrega=False).monto_bodegaje
        == 1000
    )
    assert (
        calcular_cobro(p_extra, tarifas, _AHORA, es_primera_entrega=False).monto_bodegaje
        == 1500
    )


def test_total_suma_base_y_bodegaje():
    p = _paquete(horas_desde_recibido=50)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.monto_total == desglose.monto_base + desglose.monto_bodegaje


def test_sin_received_at_no_hay_bodegaje():
    p = _paquete(horas_desde_recibido=None)
    desglose = calcular_cobro(p, _tarifas(), _AHORA, es_primera_entrega=False)
    assert desglose.bloques_bodegaje == 0
    assert desglose.monto_bodegaje == 0
