# -*- coding: utf-8 -*-
"""
Vista "Dinero" (pedido del cliente 2026-11-01): UNA sola lista con cada movimiento de dinero de la portería, más
recientes primero, con filtros y una línea de totales. Junta tres fuentes que ya existen, sin tabla nueva:

- `MovimientoSaldoContraEntrega` (saldo contra entrega, `monto` con signo). El modelo NO guarda un tipo, así que se
  deduce de cómo lo crea cada ruta (`registrar_movimiento_saldo` es el único punto de creación):
    * negativo con paquete -> "Pago a mensajero" (Recibir, `packages.py::receive_action`, es el único camino que
      registra un negativo, siempre con el paquete que llegó);
    * negativo sin paquete -> "Ajuste" (solo puede venir del formulario manual de `/residentes/{id}`);
    * positivo cuando el saldo de esa Persona JUSTO ANTES era negativo -> "Abono de deuda" (el abono de Entregar o una
      recuperación posterior desde `/residentes`: en los dos casos la persona debía);
    * positivo sin deuda previa -> "Depósito".
  "Justo antes" = suma de sus movimientos anteriores (función de ventana en SQL, nunca una consulta por fila).
- `Cobro` (servicio + bodegaje al Entregar) -> "Cobro de servicio" por `monto_total`. Anulado (`motivo_anulacion`) ->
  "Cobro anulado": anular exonera SOLO el servicio, el bodegaje se sigue cobrando (`packages.py::deliver_action`
  guarda `monto_total = monto_bodegaje`), así que su `monto_total` SÍ entró a la caja y cuenta igual en los totales.
- `Paquete.saldo_no_cobrado` (0074, "Entregado sin cobrar") -> sin monto (no movió plata), solo el aviso de la deuda.

"En caja" = Neto sin ningún filtro = suma de TODOS los movimientos de saldo + `monto_total` de TODOS los Cobros
(anulados incluidos, por su bodegaje). Filtrado y paginado en SQL sobre un `UNION ALL` de las tres fuentes; el
enriquecimiento de la página (nombres, paquetes, operadores) va en lote, un puñado fijo de consultas.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import Integer, String, and_, case, cast, func, literal, null, or_, select, union_all
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .cobro import Cobro
from .paquete import Paquete, torre_sin_prefijo
from .paquete_service import personas_destinatarias
from .persona import Persona
from .saldo_contra_entrega import MovimientoSaldoContraEntrega
from .saldo_contra_entrega_service import saldo_de_persona, saldos_de_personas
from .usuario import Usuario
from .zona_horaria import ZONA_HORARIA_APP

TIPO_DEPOSITO = "deposito"
TIPO_ABONO_DEUDA = "abono_deuda"
TIPO_PAGO_MENSAJERO = "pago_mensajero"
TIPO_AJUSTE = "ajuste"
TIPO_COBRO = "cobro"
TIPO_COBRO_ANULADO = "cobro_anulado"
TIPO_SIN_COBRAR = "sin_cobrar"

# Orden de las píldoras del filtro y texto de cada tipo en la lista.
TIPOS = {
    TIPO_DEPOSITO: "Depósito",
    TIPO_ABONO_DEUDA: "Abono de deuda",
    TIPO_PAGO_MENSAJERO: "Pago a mensajero",
    TIPO_AJUSTE: "Ajuste",
    TIPO_COBRO: "Cobro de servicio",
    TIPO_COBRO_ANULADO: "Cobro anulado",
    TIPO_SIN_COBRAR: "Entregado sin cobrar",
}

# Mismos textos que el chip "Entregado sin cobrar" (`components/_cobrar_saldo.html::etiqueta_sin_cobrar`).
MOTIVOS_NO_COBRO = {
    "sin_efectivo": "No tiene efectivo",
    "paga_despues": "Paga después",
    "cliente_vip": "Cliente VIP",
    "otro": "Otro motivo",
}

POR_PAGINA = 20


@dataclass
class FiltrosDinero:
    """Filtros de la vista, ya validados (lo inválido llega como `None`/`False`, que no filtra)."""

    q: str = ""
    tipo: str | None = None
    operador_id: uuid.UUID | None = None
    desde: date | None = None
    hasta: date | None = None
    solo_deudas: bool = False
    cliente_id: uuid.UUID | None = None

    def hay_filtros(self) -> bool:
        return bool(
            self.q.strip() or self.tipo or self.operador_id or self.desde or self.hasta or self.solo_deudas
            or self.cliente_id
        )


@dataclass
class FilaDinero:
    fuente: str
    id: uuid.UUID
    fecha: datetime
    tipo: str
    monto: int
    detalle: str
    persona_id: uuid.UUID | None = None
    cliente_nombre: str | None = None
    direccion: str | None = None
    access_code: str | None = None
    operador_nombre: str | None = None
    # Solo "Entregado sin cobrar": ¿sigue debiendo? (ver `_sin_cobrar_pendientes`).
    pendiente: bool | None = None

    @property
    def etiqueta_tipo(self) -> str:
        return TIPOS.get(self.tipo, self.tipo)


@dataclass
class TotalesDinero:
    cantidad: int = 0
    entro: int = 0
    salio: int = 0  # en positivo
    deudas_pendientes: int = 0

    @property
    def neto(self) -> int:
        return self.entro - self.salio


@dataclass
class ResultadoDinero:
    filas: list = field(default_factory=list)
    pagina: int = 1
    total_paginas: int = 1
    totales: TotalesDinero = field(default_factory=TotalesDinero)
    cliente: Persona | None = None
    saldo_cliente: int = 0


def _uuid_nulo():
    return cast(null(), UUID(as_uuid=True))


def _movimientos():
    """Movimientos de saldo con su tipo deducido (ver docstring del módulo). `saldo_previo` = suma de los movimientos
    ANTERIORES de la misma Persona (ventana `ROWS ... 1 PRECEDING`), calculada sobre TODOS los movimientos antes de
    cualquier filtro -- un filtro de fechas no cambia cómo se clasifica un movimiento."""
    m = MovimientoSaldoContraEntrega
    base = select(
        m.id,
        m.created_at,
        m.monto,
        m.persona_id,
        m.paquete_id,
        m.registrado_por_usuario_id,
        func.sum(m.monto)
        .over(partition_by=m.persona_id, order_by=(m.created_at, m.id), rows=(None, -1))
        .label("saldo_previo"),
    ).subquery("movimientos_clasificados")
    saldo_previo = func.coalesce(base.c.saldo_previo, 0)
    tipo = case(
        (and_(base.c.monto < 0, base.c.paquete_id.isnot(None)), TIPO_PAGO_MENSAJERO),
        (base.c.monto < 0, TIPO_AJUSTE),
        (and_(base.c.monto > 0, saldo_previo < 0), TIPO_ABONO_DEUDA),
        (base.c.monto > 0, TIPO_DEPOSITO),
        else_=TIPO_AJUSTE,
    )
    return select(
        literal("saldo").label("fuente"),
        base.c.id.label("id"),
        base.c.created_at.label("fecha"),
        tipo.label("tipo"),
        base.c.monto.label("monto"),
        base.c.persona_id.label("persona_id"),
        base.c.paquete_id.label("paquete_id"),
        base.c.registrado_por_usuario_id.label("operador_id"),
        cast(saldo_previo, Integer).label("extra_monto"),
        cast(null(), String).label("extra_texto"),
    )


def _cobros():
    """Cada Cobro: `extra_monto` = bodegaje, `extra_texto` = motivo de anulación."""
    return select(
        literal("cobro").label("fuente"),
        Cobro.id,
        Cobro.cobrado_en,
        case((Cobro.motivo_anulacion.isnot(None), TIPO_COBRO_ANULADO), else_=TIPO_COBRO),
        Cobro.monto_total,
        _uuid_nulo(),
        Cobro.paquete_id,
        Cobro.cobrado_por_usuario_id,
        Cobro.monto_bodegaje,
        cast(Cobro.motivo_anulacion, String),
    )


def _sin_cobrar():
    """Cada paquete entregado debiendo: monto 0 (no movió plata), `extra_monto` = lo que quedó debiendo."""
    return select(
        literal("sin_cobrar").label("fuente"),
        Paquete.id,
        Paquete.delivered_at,
        literal(TIPO_SIN_COBRAR),
        literal(0),
        _uuid_nulo(),
        Paquete.id,
        Paquete.delivered_by_usuario_id,
        Paquete.saldo_no_cobrado,
        cast(Paquete.motivo_no_cobro, String),
    ).where(Paquete.saldo_no_cobrado > 0, Paquete.delivered_at.isnot(None))


def _sin_cobrar_pendientes(session: Session) -> tuple[dict, dict]:
    """`({paquete_id: pendiente}, {paquete_id: Persona | None})` de TODOS los "Entregado sin cobrar".

    Regla (a propósito simple): sigue pendiente mientras el saldo ACTUAL de esa Persona sea negativo -- si ya lo
    pagó (abono, depósito), su saldo volvió a 0 o más. Sin Persona identificable se da por pendiente: no hay cómo
    comprobar que se pagó. Son pocos paquetes (solo las entregas con deuda desde la 0074), así que se resuelven
    todos en Python con un puñado fijo de consultas."""
    paquetes = session.query(Paquete).filter(Paquete.saldo_no_cobrado > 0).all()
    personas = personas_destinatarias(session, paquetes)
    saldos = saldos_de_personas(session, {pe.id for pe in personas.values() if pe is not None})
    pendientes = {
        p.id: (personas[p.id] is None or saldos.get(personas[p.id].id, 0) < 0) for p in paquetes
    }
    return pendientes, personas


def _condicion_paquete_de_persona(session: Session, persona: Persona):
    """Paquetes cuyo destinatario es `persona`, en SQL -- la regla de `persona_destinataria` (teléfono con el mismo
    nombre, WhatsApp propio, o nombre ÚNICO)."""
    condiciones = []
    if persona.telefono:
        condiciones.append(
            and_(Paquete.recipient_phone == persona.telefono, Paquete.recipient_name == persona.nombre)
        )
    if persona.whatsapp_usuario:
        condiciones.append(Paquete.recipient_whatsapp == persona.whatsapp_usuario)
    homonimos = session.query(func.count(Persona.id)).filter(Persona.nombre == persona.nombre).scalar()
    if homonimos == 1:
        condiciones.append(Paquete.recipient_name == persona.nombre)
    return or_(*condiciones) if condiciones else literal(False)


def _condicion_texto(termino: str):
    """Cada palabra de `q` tiene que aparecer (parcial, sin mayúsculas) en algún dato: nombre/teléfono/WhatsApp del
    cliente, código de acceso, destinatario del paquete, o Torre/Apartamento (del paquete o, para un movimiento
    sin paquete, el apartamento actual de la Persona)."""
    condiciones = []
    for palabra in termino.split():
        patron = f"%{palabra}%"
        condiciones.append(
            or_(
                Persona.nombre.ilike(patron),
                Persona.telefono.ilike(patron),
                Persona.whatsapp_usuario.ilike(patron),
                Paquete.access_code.ilike(patron),
                Paquete.recipient_name.ilike(patron),
                Paquete.recipient_phone.ilike(patron),
                Paquete.snapshot_torre.ilike(patron),
                Paquete.snapshot_apartamento.ilike(patron),
                Apartamento.torre.ilike(patron),
                Apartamento.apartamento.ilike(patron),
            )
        )
    return and_(*condiciones)


def _consulta_filtrada(session: Session, filtros: FiltrosDinero, pendientes: dict):
    """`(union, condiciones, from_)` -- la unión de las tres fuentes y las condiciones de `filtros` sobre ella."""
    union = union_all(_movimientos(), _cobros(), _sin_cobrar()).subquery("dinero")
    from_ = (
        union.outerjoin(Paquete, Paquete.id == union.c.paquete_id)
        .outerjoin(Persona, Persona.id == union.c.persona_id)
        .outerjoin(Apartamento, Apartamento.id == Persona.apartamento_actual_id)
    )
    condiciones = []
    if filtros.tipo:
        condiciones.append(union.c.tipo == filtros.tipo)
    if filtros.operador_id:
        condiciones.append(union.c.operador_id == filtros.operador_id)
    if filtros.desde:
        condiciones.append(union.c.fecha >= datetime.combine(filtros.desde, time.min, tzinfo=ZONA_HORARIA_APP))
    if filtros.hasta:
        fin = datetime.combine(filtros.hasta + timedelta(days=1), time.min, tzinfo=ZONA_HORARIA_APP)
        condiciones.append(union.c.fecha < fin)
    if filtros.solo_deudas:
        ids = [pid for pid, pendiente in pendientes.items() if pendiente]
        condiciones.append(and_(union.c.tipo == TIPO_SIN_COBRAR, union.c.paquete_id.in_(ids)))
    if filtros.q.strip():
        condiciones.append(_condicion_texto(filtros.q.strip()))
    if filtros.cliente_id:
        persona = session.get(Persona, filtros.cliente_id)
        if persona is None:
            condiciones.append(literal(False))
        else:
            condiciones.append(
                or_(
                    union.c.persona_id == persona.id,
                    and_(union.c.persona_id.is_(None), _condicion_paquete_de_persona(session, persona)),
                )
            )
    return union, condiciones, from_


def _totales(session: Session, union, condiciones, from_, pendientes: dict, personas_sin_cobrar: dict):
    entro = func.coalesce(func.sum(case((union.c.monto > 0, union.c.monto), else_=0)), 0)
    salio = func.coalesce(func.sum(case((union.c.monto < 0, -union.c.monto), else_=0)), 0)
    cantidad, total_entro, total_salio = session.execute(
        select(func.count(), entro, salio).select_from(from_).where(*condiciones)
    ).one()
    # Deudas pendientes: lo que deben HOY las Personas detrás de los "Entregado sin cobrar" pendientes que quedan
    # dentro del filtro -- su saldo actual, contado UNA vez por Persona (dos entregas debiendo de la misma persona no
    # suman dos veces: la segunda ya incluía la primera). Sin Persona identificable: lo que quedó debiendo esa vez.
    ids_sin_cobrar = [
        fila[0]
        for fila in session.execute(
            select(union.c.paquete_id).select_from(from_).where(*condiciones, union.c.tipo == TIPO_SIN_COBRAR)
        )
    ]
    deuda_sin_persona = 0
    personas_con_deuda = set()
    for paquete_id in ids_sin_cobrar:
        if not pendientes.get(paquete_id):
            continue
        persona = personas_sin_cobrar.get(paquete_id)
        if persona is None:
            deuda_sin_persona += session.get(Paquete, paquete_id).saldo_no_cobrado or 0
        else:
            personas_con_deuda.add(persona.id)
    saldos = saldos_de_personas(session, personas_con_deuda)
    deudas = deuda_sin_persona + sum(max(0, -saldo) for saldo in saldos.values())
    return TotalesDinero(
        cantidad=int(cantidad), entro=int(total_entro), salio=int(total_salio), deudas_pendientes=deudas
    )


def direccion_corta(torre: str | None, apartamento: str | None) -> str | None:
    """"Torre 10 · Apt 101" -- mismo formato que la columna de `/paquetes` (`packages.py::_direccion_corta`)."""
    if not apartamento:
        return None
    torre = torre_sin_prefijo(torre)
    return f"Torre {torre} · Apt {apartamento}" if torre else f"Apt {apartamento}"


def _pesos(monto: int) -> str:
    return f"${monto:,}"


def _detalle_movimiento(tipo: str, monto: int, saldo_previo: int) -> str:
    saldo_despues = saldo_previo + monto
    if tipo == TIPO_ABONO_DEUDA:
        debia = _pesos(-saldo_previo)
        if saldo_despues < 0:
            return f"Debía {debia} · sigue debiendo {_pesos(-saldo_despues)}"
        if saldo_despues > 0:
            return f"Debía {debia} · deuda saldada, queda {_pesos(saldo_despues)} a favor"
        return f"Debía {debia} · deuda saldada"
    if tipo == TIPO_PAGO_MENSAJERO:
        return "Pagado al mensajero al recibir" + (
            f" · queda debiendo {_pesos(-saldo_despues)}" if saldo_despues < 0 else ""
        )
    if tipo == TIPO_DEPOSITO:
        return f"Saldo a favor: {_pesos(saldo_despues)}"
    return f"Ajuste manual del saldo · queda en {'-' if saldo_despues < 0 else ''}{_pesos(abs(saldo_despues))}"


def _detalle_cobro(tipo: str, monto: int, bodegaje: int, motivo: str | None) -> str:
    if tipo == TIPO_COBRO_ANULADO:
        texto = f"Servicio anulado: {motivo}"
        return texto + (f" · se cobró el bodegaje {_pesos(bodegaje)}" if bodegaje else "")
    if monto == 0:
        return "Sin cargo (primera entrega)"
    if bodegaje:
        return f"Servicio {_pesos(monto - bodegaje)} + bodegaje {_pesos(bodegaje)}"
    return "Servicio"


def _detalle_sin_cobrar(debe: int, motivo_no_cobro: str | None) -> str:
    """"Debe $R · <motivo> — <nota>" (el motivo se guarda como '<clave>' o '<clave>: <nota>', ver 0074)."""
    partes = (motivo_no_cobro or "").split(": ", 1)
    texto = MOTIVOS_NO_COBRO.get(partes[0], partes[0])
    if len(partes) > 1:
        texto = f"{texto} — {partes[1]}"
    return f"Debe {_pesos(debe)}" + (f" · {texto}" if texto else "")


def _construir_filas(session: Session, crudas, pendientes: dict, personas_sin_cobrar: dict) -> list[FilaDinero]:
    """Las filas de UNA página, con nombres/paquetes/operadores resueltos en lote."""
    paquete_ids = {c.paquete_id for c in crudas if c.paquete_id}
    paquetes = {p.id: p for p in session.query(Paquete).filter(Paquete.id.in_(paquete_ids))} if paquete_ids else {}
    # Cobros/"sin cobrar": la Persona sale del destinatario del paquete (no hay FK, ver `persona_destinataria`).
    paquetes_sin_persona = [paquetes[c.paquete_id] for c in crudas if c.persona_id is None and c.paquete_id in paquetes]
    destinatarias = personas_destinatarias(session, paquetes_sin_persona)
    persona_ids = {c.persona_id for c in crudas if c.persona_id}
    personas = {p.id: p for p in session.query(Persona).filter(Persona.id.in_(persona_ids))} if persona_ids else {}
    apto_ids = {p.apartamento_actual_id for p in personas.values() if p.apartamento_actual_id}
    apartamentos = (
        {a.id: a for a in session.query(Apartamento).filter(Apartamento.id.in_(apto_ids))} if apto_ids else {}
    )
    operador_ids = {c.operador_id for c in crudas if c.operador_id}
    operadores = (
        {u.id: u.nombre for u in session.query(Usuario).filter(Usuario.id.in_(operador_ids))} if operador_ids else {}
    )

    filas = []
    for c in crudas:
        paquete = paquetes.get(c.paquete_id)
        persona = personas.get(c.persona_id) if c.persona_id else destinatarias.get(c.paquete_id)
        if paquete is not None and paquete.snapshot_apartamento:
            direccion = direccion_corta(paquete.snapshot_torre, paquete.snapshot_apartamento)
        else:
            apto = apartamentos.get(persona.apartamento_actual_id) if persona is not None else None
            direccion = direccion_corta(apto.torre, apto.apartamento) if apto else None
        if c.fuente == "saldo":
            detalle = _detalle_movimiento(c.tipo, c.monto, c.extra_monto)
        elif c.fuente == "cobro":
            detalle = _detalle_cobro(c.tipo, c.monto, c.extra_monto, c.extra_texto)
        else:
            detalle = _detalle_sin_cobrar(c.extra_monto, c.extra_texto)
        filas.append(
            FilaDinero(
                fuente=c.fuente,
                id=c.id,
                fecha=c.fecha,
                tipo=c.tipo,
                monto=c.monto,
                detalle=detalle,
                persona_id=persona.id if persona is not None else None,
                cliente_nombre=persona.nombre if persona is not None else (paquete.recipient_name if paquete else None),
                direccion=direccion,
                access_code=paquete.access_code if paquete else None,
                operador_nombre=operadores.get(c.operador_id),
                pendiente=pendientes.get(c.paquete_id) if c.fuente == "sin_cobrar" else None,
            )
        )
    return filas


def listar_dinero(session: Session, filtros: FiltrosDinero, pagina: int = 1) -> ResultadoDinero:
    """La página `pagina` de la lista unificada (más recientes primero) + totales sobre TODO lo filtrado."""
    pendientes, personas_sin_cobrar = _sin_cobrar_pendientes(session)
    union, condiciones, from_ = _consulta_filtrada(session, filtros, pendientes)
    totales = _totales(session, union, condiciones, from_, pendientes, personas_sin_cobrar)

    total_paginas = max(1, -(-totales.cantidad // POR_PAGINA))
    pagina = max(1, min(pagina, total_paginas))
    crudas = session.execute(
        select(union)
        .select_from(from_)
        .where(*condiciones)
        .order_by(union.c.fecha.desc(), union.c.id.desc())
        .offset((pagina - 1) * POR_PAGINA)
        .limit(POR_PAGINA)
    ).all()

    resultado = ResultadoDinero(
        filas=_construir_filas(session, crudas, pendientes, personas_sin_cobrar),
        pagina=pagina,
        total_paginas=total_paginas,
        totales=totales,
    )
    if filtros.cliente_id:
        resultado.cliente = session.get(Persona, filtros.cliente_id)
        if resultado.cliente is not None:
            resultado.saldo_cliente = saldo_de_persona(session, resultado.cliente.id)
    return resultado


def operadores(session: Session) -> list[Usuario]:
    """Todo el staff (activo o no: un operador ya retirado igual pudo registrar movimientos), por nombre."""
    return session.query(Usuario).order_by(Usuario.nombre.asc()).all()
