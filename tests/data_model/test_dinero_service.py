# -*- coding: utf-8 -*-
"""
Vista "Dinero" (pedido del cliente 2026-11-01) -- `dinero_service.listar_dinero` contra el Postgres efímero: una fila
por movimiento de saldo, por Cobro y por "Entregado sin cobrar"; tipo deducido; totales sobre TODO lo filtrado;
"En caja"; filtros; y la regla de deuda pendiente.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.cobro_service import DesgloseCobro, registrar_cobro
from app.domain.dinero_service import (
    POR_PAGINA,
    TIPO_ABONO_DEUDA,
    TIPO_AJUSTE,
    TIPO_COBRO,
    TIPO_COBRO_ANULADO,
    TIPO_DEPOSITO,
    TIPO_PAGO_MENSAJERO,
    TIPO_SIN_COBRAR,
    FiltrosDinero,
    listar_dinero,
)
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo
from app.domain.usuario import RolUsuario, Usuario
from app.domain.zona_horaria import ZONA_HORARIA_APP

pytestmark = pytest.mark.integration


def _staff(session, nombre="Operador") -> Usuario:
    u = Usuario(nombre=nombre, rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def _paquete(session, staff, tel="3001234567", nombre="Ana", entregar=True):
    p = announce(session, anunciante_telefono=tel, anunciante_nombre=nombre, destinatario=Destinatario.yo_mismo())
    receive(session, p, staff, "GUIA-1")
    if entregar:
        deliver(session, p, staff)
    return p


def _cobro(session, staff, paquete, total, bodegaje=0, motivo=None):
    return registrar_cobro(
        session,
        paquete,
        DesgloseCobro(monto_base=total - bodegaje, bloques_bodegaje=1 if bodegaje else 0, monto_bodegaje=bodegaje,
                      monto_total=total),
        staff,
        motivo_anulacion=motivo,
    )


def _filas(session, **filtros):
    return listar_dinero(session, FiltrosDinero(**filtros)).filas


def _por_tipo(filas):
    return sorted((f.tipo, f.monto) for f in filas)


def test_clasifica_cada_movimiento_de_saldo(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    p = _paquete(db_session, staff, entregar=False)
    registrar_movimiento_saldo(db_session, ana.id, 5000, staff)  # sin deuda -> depósito
    registrar_movimiento_saldo(db_session, ana.id, -8000, staff, paquete_id=p.id)  # Recibir -> mensajero
    registrar_movimiento_saldo(db_session, ana.id, 2000, staff, paquete_id=p.id)  # debía 3000 -> abono
    registrar_movimiento_saldo(db_session, ana.id, -500, staff)  # negativo manual -> ajuste

    filas = _filas(db_session)

    assert [(f.tipo, f.monto) for f in filas] == [
        (TIPO_AJUSTE, -500),
        (TIPO_ABONO_DEUDA, 2000),
        (TIPO_PAGO_MENSAJERO, -8000),
        (TIPO_DEPOSITO, 5000),
    ]
    abono = filas[1]
    assert abono.detalle == "Debía $3,000 · sigue debiendo $1,000"
    assert abono.cliente_nombre == "ANA" and abono.persona_id == ana.id
    assert abono.access_code == p.access_code
    assert abono.operador_nombre == "Operador"


def test_la_clasificacion_no_cambia_al_filtrar_por_fecha(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    deuda = registrar_movimiento_saldo(db_session, ana.id, -3000, staff)
    deuda.created_at = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    abono = registrar_movimiento_saldo(db_session, ana.id, 3000, staff)
    abono.created_at = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    db_session.flush()

    filas = _filas(db_session, desde=date(2026, 10, 15))

    assert [(f.tipo, f.detalle) for f in filas] == [(TIPO_ABONO_DEUDA, "Debía $3,000 · deuda saldada")]


def test_cobros_normal_y_anulado_con_su_bodegaje(db_session):
    staff = _staff(db_session)
    normal = _paquete(db_session, staff, "3001111111", "Beto")
    anulado = _paquete(db_session, staff, "3002222222", "Caro")
    _cobro(db_session, staff, normal, 2500, bodegaje=1000)
    _cobro(db_session, staff, anulado, 1000, bodegaje=1000, motivo="Cortesía")

    filas = {f.cliente_nombre: f for f in _filas(db_session)}

    assert (filas["BETO"].tipo, filas["BETO"].monto) == (TIPO_COBRO, 2500)
    assert filas["BETO"].detalle == "Servicio $1,500 + bodegaje $1,000"
    # Anular exonera solo el servicio: el bodegaje SÍ se cobró y entra en la caja.
    assert (filas["CARO"].tipo, filas["CARO"].monto) == (TIPO_COBRO_ANULADO, 1000)
    assert filas["CARO"].detalle == "Servicio anulado: Cortesía · se cobró el bodegaje $1,000"
    assert filas["CARO"].persona_id is not None  # el destinatario se resuelve aunque el Cobro no tenga Persona


def test_cobro_de_primera_entrega_sin_cargo_no_mueve_plata(db_session):
    staff = _staff(db_session)
    _cobro(db_session, staff, _paquete(db_session, staff), 0)

    (fila,) = _filas(db_session)
    assert (fila.tipo, fila.monto, fila.detalle) == (TIPO_COBRO, 0, "Sin cargo (primera entrega)")


def test_entregado_sin_cobrar_y_cuando_deja_de_estar_pendiente(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    registrar_movimiento_saldo(db_session, ana.id, -3000, staff)
    p = _paquete(db_session, staff)
    p.saldo_no_cobrado = 3000
    p.motivo_no_cobro = "paga_despues: vuelve mañana"
    db_session.flush()

    (sin_cobrar,) = _filas(db_session, tipo=TIPO_SIN_COBRAR)
    assert sin_cobrar.monto == 0
    assert sin_cobrar.detalle == "Debe $3,000 · Paga después — vuelve mañana"
    assert sin_cobrar.pendiente is True
    assert sin_cobrar.persona_id == ana.id
    assert listar_dinero(db_session, FiltrosDinero()).totales.deudas_pendientes == 3000

    registrar_movimiento_saldo(db_session, ana.id, 3000, staff)  # pagó después

    (sin_cobrar,) = _filas(db_session, tipo=TIPO_SIN_COBRAR)
    assert sin_cobrar.pendiente is False
    assert _filas(db_session, solo_deudas=True) == []
    assert listar_dinero(db_session, FiltrosDinero()).totales.deudas_pendientes == 0


def test_deudas_pendientes_cuenta_una_vez_por_persona(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    registrar_movimiento_saldo(db_session, ana.id, -5000, staff)
    for debe in (5000, 5000):  # dos entregas debiendo: la segunda ya incluía la primera
        p = _paquete(db_session, staff)
        p.saldo_no_cobrado = debe
        p.motivo_no_cobro = "sin_efectivo"
    db_session.flush()

    resultado = listar_dinero(db_session, FiltrosDinero(solo_deudas=True))

    assert len(resultado.filas) == 2
    assert resultado.totales.deudas_pendientes == 5000


def test_en_caja_es_todo_el_saldo_mas_todos_los_cobros(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    registrar_movimiento_saldo(db_session, ana.id, 20000, staff)  # depósito
    registrar_movimiento_saldo(db_session, ana.id, -12000, staff, paquete_id=_paquete(db_session, staff).id)
    _cobro(db_session, staff, _paquete(db_session, staff, "3001111111", "Beto"), 1500)
    _cobro(db_session, staff, _paquete(db_session, staff, "3002222222", "Caro"), 1000, bodegaje=1000, motivo="X")

    totales = listar_dinero(db_session, FiltrosDinero()).totales

    assert (totales.cantidad, totales.entro, totales.salio) == (4, 22500, 12000)
    assert totales.neto == 10500


def test_totales_cubren_todo_lo_filtrado_no_solo_la_pagina(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    for _ in range(POR_PAGINA + 5):
        registrar_movimiento_saldo(db_session, ana.id, 1000, staff)

    primera = listar_dinero(db_session, FiltrosDinero(), pagina=1)
    segunda = listar_dinero(db_session, FiltrosDinero(), pagina=2)

    assert (len(primera.filas), len(segunda.filas), primera.total_paginas) == (POR_PAGINA, 5, 2)
    assert primera.totales.cantidad == POR_PAGINA + 5
    assert primera.totales.entro == (POR_PAGINA + 5) * 1000
    # Más recientes primero.
    assert primera.filas[0].fecha >= primera.filas[-1].fecha >= segunda.filas[0].fecha


def test_filtros_por_texto_tipo_operador_y_fechas(db_session):
    juan = _staff(db_session, "Juan")
    luisa = _staff(db_session, "Luisa")
    ana = get_or_create_persona(db_session, "3001234567", "Ana Gómez")
    beto = get_or_create_persona(db_session, "3007654321", "Beto Ruiz")
    m1 = registrar_movimiento_saldo(db_session, ana.id, 1000, juan)
    m1.created_at = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    m2 = registrar_movimiento_saldo(db_session, beto.id, 2000, luisa)
    # 23:30 hora de Colombia del 20-oct = 04:30 UTC del 21: cuenta como el 20 (día local).
    m2.created_at = datetime(2026, 10, 20, 23, 30, tzinfo=ZONA_HORARIA_APP)
    paquete = _paquete(db_session, juan, "3007654321", "Beto Ruiz")
    _cobro(db_session, juan, paquete, 1500)
    db_session.flush()

    assert _por_tipo(_filas(db_session, q="gómez")) == [(TIPO_DEPOSITO, 1000)]
    assert _por_tipo(_filas(db_session, q="7654321")) == [(TIPO_COBRO, 1500), (TIPO_DEPOSITO, 2000)]
    assert _por_tipo(_filas(db_session, q=paquete.access_code.lower())) == [(TIPO_COBRO, 1500)]
    assert _por_tipo(_filas(db_session, tipo=TIPO_COBRO)) == [(TIPO_COBRO, 1500)]
    assert _por_tipo(_filas(db_session, operador_id=luisa.id)) == [(TIPO_DEPOSITO, 2000)]
    assert _por_tipo(_filas(db_session, desde=date(2026, 10, 20), hasta=date(2026, 10, 20))) == [(TIPO_DEPOSITO, 2000)]
    assert _por_tipo(_filas(db_session, hasta=date(2026, 10, 15))) == [(TIPO_DEPOSITO, 1000)]


def test_filtro_por_apartamento(db_session):
    from app.domain.apartamento_service import resolver_apartamento

    staff = _staff(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    p = announce(
        db_session, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(), apartamento=apto,
    )
    receive(db_session, p, staff, "G-1")
    deliver(db_session, p, staff)
    _cobro(db_session, staff, p, 1500)
    _cobro(db_session, staff, _paquete(db_session, staff, "3001111111", "Beto"), 1500)

    (fila,) = _filas(db_session, q="torre 1 101")
    assert fila.direccion == "Torre 1 · Apt 101"


def test_filtro_por_cliente_trae_sus_movimientos_y_los_cobros_de_sus_paquetes(db_session):
    staff = _staff(db_session)
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    beto = get_or_create_persona(db_session, "3007654321", "Beto")
    registrar_movimiento_saldo(db_session, ana.id, -2000, staff)
    registrar_movimiento_saldo(db_session, beto.id, 9000, staff)
    _cobro(db_session, staff, _paquete(db_session, staff), 1500)
    _cobro(db_session, staff, _paquete(db_session, staff, "3007654321", "Beto"), 1500)

    resultado = listar_dinero(db_session, FiltrosDinero(cliente_id=ana.id))

    assert _por_tipo(resultado.filas) == [(TIPO_AJUSTE, -2000), (TIPO_COBRO, 1500)]
    assert resultado.cliente.id == ana.id
    assert resultado.saldo_cliente == -2000


def test_sin_movimientos_lista_vacia_y_totales_en_cero(db_session):
    resultado = listar_dinero(db_session, FiltrosDinero())
    assert resultado.filas == []
    assert (resultado.totales.cantidad, resultado.totales.neto, resultado.total_paginas) == (0, 0, 1)


def test_hay_filtros():
    assert not FiltrosDinero().hay_filtros()
    assert not FiltrosDinero(q="   ").hay_filtros()
    assert FiltrosDinero(q="ana").hay_filtros()
    assert FiltrosDinero(solo_deudas=True).hay_filtros()
    assert FiltrosDinero(hasta=date.today() - timedelta(days=1)).hay_filtros()
