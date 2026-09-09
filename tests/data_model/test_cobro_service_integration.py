# -*- coding: utf-8 -*-
"""
Seam A — `TarifaCobro` vigente y `estadisticas_cobro`, contra el Postgres
efímero construido con `alembic upgrade head`. Comportamiento externo:
valores por defecto sin fila, que la fila se materialice y persista al
usarla, y agregados correctos de cobros por rango de fechas.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.apartamento import Apartamento
from app.domain.cobro_service import (
    DesgloseCobro,
    estadisticas_cobro,
    obtener_tarifas_vigentes,
    registrar_cobro,
)
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session) -> Usuario:
    u = Usuario(nombre="Operador", rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def _entregar_con_cobro(session, staff, monto_total, tel, apartamento=None, motivo_anulacion=None):
    p = announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apartamento,
    )
    receive(session, p, staff)
    deliver(session, p, staff)
    registrar_cobro(
        session,
        p,
        DesgloseCobro(monto_base=monto_total, bloques_bodegaje=0, monto_bodegaje=0, monto_total=monto_total),
        staff,
        motivo_anulacion=motivo_anulacion,
    )
    return p


def test_sin_fila_devuelve_los_valores_por_defecto(db_session):
    tarifas = obtener_tarifas_vigentes(db_session)

    assert tarifas.base_normal == 1500
    assert tarifas.base_extra_dimensionado == 2000
    assert tarifas.bodegaje_normal_24h == 1000
    assert tarifas.bodegaje_extra_dimensionado_24h == 1500


def test_la_fila_se_materializa_y_persiste(db_session):
    tarifas = obtener_tarifas_vigentes(db_session)
    tarifas.base_normal = 1800
    db_session.flush()

    tarifas_de_nuevo = obtener_tarifas_vigentes(db_session)
    assert tarifas_de_nuevo.base_normal == 1800
    assert tarifas_de_nuevo.id == tarifas.id


def test_estadisticas_cuenta_y_suma_dentro_del_rango(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    _entregar_con_cobro(db_session, staff, 2000, tel="3002222222")
    db_session.commit()

    stats = estadisticas_cobro(
        db_session, ahora - timedelta(hours=1), ahora + timedelta(hours=1)
    )
    assert stats.cantidad == 2
    assert stats.monto_total == 3500


def test_estadisticas_fuera_de_rango_no_se_cuentan(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    db_session.commit()

    stats = estadisticas_cobro(
        db_session, ahora + timedelta(days=1), ahora + timedelta(days=2)
    )
    assert stats.cantidad == 0
    assert stats.monto_total == 0


def test_estadisticas_desglosa_por_apartamento(db_session):
    staff = _usuario(db_session)
    apto = db_session.query(Apartamento).first()
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111", apartamento=apto)
    db_session.commit()

    stats = estadisticas_cobro(
        db_session, ahora - timedelta(hours=1), ahora + timedelta(hours=1)
    )
    assert len(stats.por_apartamento) == 1
    fila = stats.por_apartamento[0]
    assert fila.torre == apto.torre
    assert fila.apartamento == apto.apartamento
    assert fila.cantidad == 1
    assert fila.monto_total == 1500


def test_estadisticas_no_mezcla_distintos_clientes_del_mismo_apartamento(db_session):
    # spec.md línea 145-146: "desglose por cliente/apartamento (agrupando
    # por los campos snapshot snapshot_torre/snapshot_apartamento/
    # recipient_phone...)" -- encontrado en code-review sin recipient_phone
    # en el group_by, dos clientes distintos del mismo apartamento se
    # mezclaban en una sola fila.
    staff = _usuario(db_session)
    apto = db_session.query(Apartamento).first()
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111", apartamento=apto)
    _entregar_con_cobro(db_session, staff, 2000, tel="3002222222", apartamento=apto)
    db_session.commit()

    stats = estadisticas_cobro(
        db_session, ahora - timedelta(hours=1), ahora + timedelta(hours=1)
    )
    assert len(stats.por_apartamento) == 2
    telefonos = {fila.recipient_phone for fila in stats.por_apartamento}
    assert telefonos == {"+573001111111", "+573002222222"}
    for fila in stats.por_apartamento:
        assert fila.torre == apto.torre
        assert fila.apartamento == apto.apartamento
        assert fila.cantidad == 1


def test_estadisticas_incluye_cobros_anulados_en_el_total(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 0, tel="3001111111", motivo_anulacion="Reclamo")
    db_session.commit()

    stats = estadisticas_cobro(
        db_session, ahora - timedelta(hours=1), ahora + timedelta(hours=1)
    )
    assert stats.cantidad == 1
    assert stats.monto_total == 0
