# -*- coding: utf-8 -*-
"""
Seam A — `TarifaCobro` vigente y `estadisticas_cobro`, contra el Postgres
efímero construido con `alembic upgrade head`. Comportamiento externo:
valores por defecto sin fila, que la fila se materialice y persista al
usarla, y agregados correctos de cobros por rango de fechas -- incluidos los
filtros combinables y la paginación de `.scratch/estadisticas-cobro-
interactivas` (Tipo de paquete, Cobrado/Anulado, Usuario, Por usuario, Serie
diaria).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.apartamento import Apartamento
from app.domain.cobro import Cobro
from app.domain.cobro_service import (
    DesgloseCobro,
    FiltrosEstadisticasCobro,
    estadisticas_cobro,
    obtener_tarifas_vigentes,
    registrar_cobro,
)
from app.domain.paquete import TipoPaquete
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session, nombre="Operador") -> Usuario:
    u = Usuario(nombre=nombre, rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def _entregar_con_cobro(
    session, staff, monto_total, tel, apartamento=None, motivo_anulacion=None, tipo=None
):
    p = announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
        apartamento=apartamento,
    )
    receive(session, p, staff, package_type=tipo)
    deliver(session, p, staff)
    registrar_cobro(
        session,
        p,
        DesgloseCobro(monto_base=monto_total, bloques_bodegaje=0, monto_bodegaje=0, monto_total=monto_total),
        staff,
        motivo_anulacion=motivo_anulacion,
    )
    return p


def _mover_cobro_a(session, paquete, cuando):
    """Reescribe `Cobro.cobrado_en` DIRECTO en la fila ya creada -- la única
    forma de ubicar un cobro en un día puntual sin depender del reloj real,
    ya que `registrar_cobro` siempre usa `datetime.now()`."""
    cobro = session.query(Cobro).filter(Cobro.paquete_id == paquete.id).one()
    cobro.cobrado_en = cuando
    session.flush()
    return cobro


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
        db_session,
        FiltrosEstadisticasCobro(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1)),
    )
    assert stats.cantidad == 2
    assert stats.monto_total == 3500


def test_estadisticas_fuera_de_rango_no_se_cuentan(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    db_session.commit()

    stats = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(desde=ahora + timedelta(days=1), hasta=ahora + timedelta(days=2)),
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
        db_session,
        FiltrosEstadisticasCobro(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1)),
    )
    assert len(stats.por_apartamento) == 1
    fila = stats.por_apartamento[0]
    assert fila.torre == apto.torre
    assert fila.apartamento == apto.apartamento
    assert fila.cantidad == 1
    assert fila.monto_total == 1500
    # Una sola página con un puñado de filas -- confirma que el campo nuevo
    # de paginación existe y no exagera el total con un solo grupo.
    assert stats.total_paginas_apartamento == 1
    assert stats.total_paginas_usuario == 1


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
        db_session,
        FiltrosEstadisticasCobro(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1)),
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
        db_session,
        FiltrosEstadisticasCobro(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1)),
    )
    assert stats.cantidad == 1
    assert stats.monto_total == 0


def test_estadisticas_filtra_por_tipo_de_paquete(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111", tipo=TipoPaquete.NORMAL)
    _entregar_con_cobro(db_session, staff, 2000, tel="3002222222", tipo=TipoPaquete.EXTRA_DIMENSIONADO)
    db_session.commit()

    stats_normal = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1), tipo=TipoPaquete.NORMAL
        ),
    )
    assert stats_normal.cantidad == 1
    assert stats_normal.monto_total == 1500

    stats_extra = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=ahora - timedelta(hours=1),
            hasta=ahora + timedelta(hours=1),
            tipo=TipoPaquete.EXTRA_DIMENSIONADO,
        ),
    )
    assert stats_extra.cantidad == 1
    assert stats_extra.monto_total == 2000


def test_estadisticas_filtra_cobrados_vs_anulados(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    _entregar_con_cobro(db_session, staff, 0, tel="3002222222", motivo_anulacion="Reclamo")
    db_session.commit()

    rango = dict(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1))

    solo_cobrados = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango, anulado=False))
    assert solo_cobrados.cantidad == 1
    assert solo_cobrados.monto_total == 1500

    solo_anulados = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango, anulado=True))
    assert solo_anulados.cantidad == 1
    assert solo_anulados.monto_total == 0

    ambos = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango))
    assert ambos.cantidad == 2


def test_estadisticas_filtra_por_usuario(db_session):
    maria = _usuario(db_session, nombre="María")
    juan = _usuario(db_session, nombre="Juan")
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, maria, 1500, tel="3001111111")
    _entregar_con_cobro(db_session, juan, 2000, tel="3002222222")
    _entregar_con_cobro(db_session, juan, 2000, tel="3003333333")
    db_session.commit()

    stats_juan = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1), usuario_id=juan.id
        ),
    )
    assert stats_juan.cantidad == 2
    assert stats_juan.monto_total == 4000


def test_estadisticas_por_usuario_desglosa_e_ignora_el_filtro_de_usuario(db_session):
    maria = _usuario(db_session, nombre="María")
    juan = _usuario(db_session, nombre="Juan")
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, maria, 1500, tel="3001111111")
    _entregar_con_cobro(db_session, juan, 2000, tel="3002222222")
    _entregar_con_cobro(db_session, juan, 2000, tel="3003333333")
    db_session.commit()

    # Filtrado a Juan -- pero la tabla `por_usuario` sigue mostrando a los
    # DOS, para poder comparar (historia 11 del spec).
    stats = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1), usuario_id=juan.id
        ),
    )
    por_nombre = {fila.nombre: fila for fila in stats.por_usuario}
    assert set(por_nombre) == {"María", "Juan"}
    assert por_nombre["María"].cantidad == 1
    assert por_nombre["María"].monto_total == 1500
    assert por_nombre["Juan"].cantidad == 2
    assert por_nombre["Juan"].monto_total == 4000


def test_estadisticas_serie_diaria_incluye_dias_sin_cobros_en_cero(db_session):
    staff = _usuario(db_session)
    hoy = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    ayer = hoy - timedelta(days=1)
    anteayer = hoy - timedelta(days=2)

    p = _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    _mover_cobro_a(db_session, p, ayer)
    db_session.commit()

    stats = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=anteayer.replace(hour=0, minute=0, second=0, microsecond=0),
            hasta=hoy.replace(hour=23, minute=59, second=59, microsecond=999999),
        ),
    )
    assert len(stats.serie_diaria) == 3
    por_fecha = {fila.fecha: fila for fila in stats.serie_diaria}
    assert por_fecha[anteayer.date()].cantidad == 0
    assert por_fecha[anteayer.date()].monto_total == 0
    assert por_fecha[ayer.date()].cantidad == 1
    assert por_fecha[ayer.date()].monto_total == 1500
    assert por_fecha[hoy.date()].cantidad == 0
    # Orden cronológico ascendente (el más antiguo primero, decisión
    # explícita del `grilling`).
    assert [fila.fecha for fila in stats.serie_diaria] == [anteayer.date(), ayer.date(), hoy.date()]


def test_estadisticas_serie_diaria_agrupa_varios_cobros_del_mismo_dia(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    _entregar_con_cobro(db_session, staff, 2000, tel="3002222222")
    db_session.commit()

    stats = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1)),
    )
    assert len(stats.serie_diaria) == 1
    assert stats.serie_diaria[0].cantidad == 2
    assert stats.serie_diaria[0].monto_total == 3500


def test_estadisticas_serie_diaria_pagina_por_dia(db_session):
    # 25 días sin ningún cobro -- alcanza para probar el mecanismo de
    # paginación compartido (20 filas por página) sin crear entidades de
    # más; `por_apartamento`/`por_usuario` reusan el mismo helper.
    staff = _usuario(db_session)
    hoy = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    desde = (hoy - timedelta(days=24)).replace(hour=0, minute=0, second=0, microsecond=0)
    hasta = hoy.replace(hour=23, minute=59, second=59, microsecond=999999)

    pagina_1 = estadisticas_cobro(
        db_session, FiltrosEstadisticasCobro(desde=desde, hasta=hasta, pagina_diario=1)
    )
    assert len(pagina_1.serie_diaria) == 20
    assert pagina_1.total_paginas_diario == 2
    assert pagina_1.serie_diaria[0].fecha == desde.date()

    pagina_2 = estadisticas_cobro(
        db_session, FiltrosEstadisticasCobro(desde=desde, hasta=hasta, pagina_diario=2)
    )
    assert len(pagina_2.serie_diaria) == 5
    assert pagina_2.total_paginas_diario == 2
    assert pagina_2.serie_diaria[-1].fecha == hasta.date()
    # Sin solape entre páginas.
    assert pagina_1.serie_diaria[-1].fecha + timedelta(days=1) == pagina_2.serie_diaria[0].fecha


def test_estadisticas_combina_varios_filtros_a_la_vez(db_session):
    staff_a = _usuario(db_session, nombre="María")
    staff_b = _usuario(db_session, nombre="Juan")
    ahora = datetime.now(timezone.utc)
    # Solo este calza los 3 filtros combinados a la vez.
    _entregar_con_cobro(
        db_session, staff_a, 2000, tel="3001111111", tipo=TipoPaquete.EXTRA_DIMENSIONADO
    )
    _entregar_con_cobro(
        db_session, staff_a, 0, tel="3002222222", tipo=TipoPaquete.EXTRA_DIMENSIONADO, motivo_anulacion="X"
    )
    _entregar_con_cobro(db_session, staff_b, 2000, tel="3003333333", tipo=TipoPaquete.EXTRA_DIMENSIONADO)
    _entregar_con_cobro(db_session, staff_a, 1500, tel="3004444444", tipo=TipoPaquete.NORMAL)
    db_session.commit()

    stats = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=ahora - timedelta(hours=1),
            hasta=ahora + timedelta(hours=1),
            tipo=TipoPaquete.EXTRA_DIMENSIONADO,
            anulado=False,
            usuario_id=staff_a.id,
        ),
    )
    assert stats.cantidad == 1
    assert stats.monto_total == 2000


def test_estadisticas_por_apartamento_pagina(db_session):
    # Catálogo real (804 apartamentos sembrados) -- de sobra para 21 filas
    # distintas sin crear entidades de más, mismo mecanismo de paginación
    # que ya se probó a fondo en `test_estadisticas_serie_diaria_pagina_por_dia`.
    staff = _usuario(db_session)
    apartamentos = db_session.query(Apartamento).limit(21).all()
    assert len(apartamentos) == 21
    ahora = datetime.now(timezone.utc)
    for i, apto in enumerate(apartamentos):
        _entregar_con_cobro(db_session, staff, 1000 + i, tel=f"300111{i:04d}", apartamento=apto)
    db_session.commit()

    rango = dict(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1))
    pagina_1 = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango, pagina_apartamento=1))
    assert len(pagina_1.por_apartamento) == 20
    assert pagina_1.total_paginas_apartamento == 2

    pagina_2 = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango, pagina_apartamento=2))
    assert len(pagina_2.por_apartamento) == 1
    assert pagina_2.total_paginas_apartamento == 2


def test_estadisticas_por_usuario_pagina(db_session):
    staff_list = [_usuario(db_session, nombre=f"Staff{i}") for i in range(21)]
    ahora = datetime.now(timezone.utc)
    for i, staff in enumerate(staff_list):
        _entregar_con_cobro(db_session, staff, 1000 + i, tel=f"300222{i:04d}")
    db_session.commit()

    rango = dict(desde=ahora - timedelta(hours=1), hasta=ahora + timedelta(hours=1))
    pagina_1 = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango, pagina_usuario=1))
    assert len(pagina_1.por_usuario) == 20
    assert pagina_1.total_paginas_usuario == 2

    pagina_2 = estadisticas_cobro(db_session, FiltrosEstadisticasCobro(**rango, pagina_usuario=2))
    assert len(pagina_2.por_usuario) == 1
    assert pagina_2.total_paginas_usuario == 2


def test_estadisticas_pagina_fuera_de_rango_no_rompe(db_session):
    staff = _usuario(db_session)
    ahora = datetime.now(timezone.utc)
    _entregar_con_cobro(db_session, staff, 1500, tel="3001111111")
    db_session.commit()

    stats = estadisticas_cobro(
        db_session,
        FiltrosEstadisticasCobro(
            desde=ahora - timedelta(hours=1),
            hasta=ahora + timedelta(hours=1),
            pagina_apartamento=999,
            pagina_usuario=999,
            pagina_diario=999,
        ),
    )
    # Sin excepción, listas vacías, pero el total de páginas sigue
    # reflejando el tamaño real del resultado (no el número de página
    # pedido).
    assert stats.por_apartamento == []
    assert stats.total_paginas_apartamento == 1
    assert stats.por_usuario == []
    assert stats.total_paginas_usuario == 1
    assert stats.serie_diaria == []
    assert stats.total_paginas_diario == 1
