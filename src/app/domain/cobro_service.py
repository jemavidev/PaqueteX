# -*- coding: utf-8 -*-
"""
Servicio de dominio de `Cobro` -- cálculo y registro del cobro por recepción
de un paquete (módulo "Gestión de cobro y bodegaje", `.scratch/cobro-bodegaje`).

`calcular_cobro` es una función PURA: no toca la base de datos, solo resuelve
el desglose a partir de datos ya cargados (`Paquete`, `TarifaCobro`, un
instante "ahora" explícito, y si es primera entrega ya resuelto por el
caller vía `paquete_service.es_primera_entrega_a_telefono`) -- separado así
a propósito para poder probar toda la aritmética (exención de primera
entrega, bloques de bodegaje) sin sesión de BD ni HTTP de por medio.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cobro import Cobro
from .motivo_anulacion_cobro import MotivoAnulacionCobro
from .paquete import Paquete, TipoPaquete
from .tarifa_cobro import ID_SINGLETON, TarifaCobro
from .usuario import Usuario

# Grace period antes de que empiece a correr el bodegaje, y el tamaño de cada
# bloque de cobro adicional -- .scratch/cobro-bodegaje, pedido explícito del
# cliente ("pasado 1 minuto de estas 48 horas ya se estaría realizando el
# cobro de bodegaje... cada 24 horas adicionales seguirá incrementándose").
_HORAS_GRACIA_BODEGAJE = 48
_HORAS_POR_BLOQUE_BODEGAJE = 24

# Valores iniciales por defecto -- .scratch/cobro-bodegaje, pedido explícito
# del cliente. Solo se usan mientras ningún ADMIN haya editado `TarifaCobro`
# todavía (ver `obtener_tarifas_vigentes`).
_TARIFA_BASE_NORMAL_DEFECTO = 1500
_TARIFA_BASE_EXTRA_DIMENSIONADO_DEFECTO = 2000
_TARIFA_BODEGAJE_NORMAL_24H_DEFECTO = 1000
_TARIFA_BODEGAJE_EXTRA_DIMENSIONADO_24H_DEFECTO = 1500


@dataclass(frozen=True)
class DesgloseCobro:
    """Resultado de `calcular_cobro` -- listo para mostrarse en el modal
    Entregar y para persistirse tal cual en un `Cobro` nuevo."""

    monto_base: int
    bloques_bodegaje: int
    monto_bodegaje: int
    monto_total: int


def obtener_tarifas_vigentes(session: Session) -> TarifaCobro:
    """La fila vigente de tarifas -- si ningún ADMIN la editó todavía, se
    materializa con los valores por defecto (nunca `None`, para que
    `calcular_cobro` siempre tenga con qué trabajar)."""
    fila = session.get(TarifaCobro, ID_SINGLETON)
    if fila is None:
        fila = TarifaCobro(
            id=ID_SINGLETON,
            base_normal=_TARIFA_BASE_NORMAL_DEFECTO,
            base_extra_dimensionado=_TARIFA_BASE_EXTRA_DIMENSIONADO_DEFECTO,
            bodegaje_normal_24h=_TARIFA_BODEGAJE_NORMAL_24H_DEFECTO,
            bodegaje_extra_dimensionado_24h=_TARIFA_BODEGAJE_EXTRA_DIMENSIONADO_24H_DEFECTO,
        )
        session.add(fila)
        session.flush()
    return fila


def calcular_cobro(
    paquete: Paquete,
    tarifas: TarifaCobro,
    ahora: datetime,
    es_primera_entrega: bool,
) -> DesgloseCobro:
    """Desglose del cobro a aplicar a `paquete`: cargo base según su
    `TipoPaquete` (exento si `es_primera_entrega`) + bodegaje (bloques de 24h
    desde el minuto 48:01 de haber sido Recibido -- NUNCA exento, ni para
    primera entrega). Sin `received_at` (paquete nunca Recibido), el
    bodegaje queda en 0 por no haber nada que contar."""
    es_extra_dimensionado = paquete.package_type == TipoPaquete.EXTRA_DIMENSIONADO

    monto_base = (
        tarifas.base_extra_dimensionado if es_extra_dimensionado else tarifas.base_normal
    )
    if es_primera_entrega:
        monto_base = 0

    bloques_bodegaje = 0
    monto_bodegaje = 0
    if paquete.received_at is not None:
        horas_transcurridas = (ahora - paquete.received_at).total_seconds() / 3600
        if horas_transcurridas > _HORAS_GRACIA_BODEGAJE:
            bloques_bodegaje = math.ceil(
                (horas_transcurridas - _HORAS_GRACIA_BODEGAJE) / _HORAS_POR_BLOQUE_BODEGAJE
            )
            tarifa_bodegaje = (
                tarifas.bodegaje_extra_dimensionado_24h
                if es_extra_dimensionado
                else tarifas.bodegaje_normal_24h
            )
            monto_bodegaje = bloques_bodegaje * tarifa_bodegaje

    return DesgloseCobro(
        monto_base=monto_base,
        bloques_bodegaje=bloques_bodegaje,
        monto_bodegaje=monto_bodegaje,
        monto_total=monto_base + monto_bodegaje,
    )


def editar_tarifas(
    session: Session,
    base_normal: int,
    base_extra_dimensionado: int,
    bodegaje_normal_24h: int,
    bodegaje_extra_dimensionado_24h: int,
) -> TarifaCobro:
    """Edita las 4 tarifas fijas -- valores fijos, no un catálogo abierto
    (no se puede agregar un quinto tipo de cobro ni eliminar ninguno de los
    4). Cambiar una tarifa acá nunca reescribe un `Cobro` ya registrado
    (snapshot en el momento de cobrar).

    Raises:
        ValueError: si algún valor es negativo.
    """
    valores = {
        "base_normal": base_normal,
        "base_extra_dimensionado": base_extra_dimensionado,
        "bodegaje_normal_24h": bodegaje_normal_24h,
        "bodegaje_extra_dimensionado_24h": bodegaje_extra_dimensionado_24h,
    }
    for nombre, valor in valores.items():
        if valor < 0:
            raise ValueError(f"{nombre} no puede ser negativo.")

    fila = obtener_tarifas_vigentes(session)
    for nombre, valor in valores.items():
        setattr(fila, nombre, valor)
    session.flush()
    return fila


def listar_motivos_anulacion(session: Session) -> list[MotivoAnulacionCobro]:
    """Todos los motivos del catálogo de anulación, en orden de creación --
    mismo criterio que `motivo_cancelacion_service.listar_motivos`, para el
    selector que aparece al marcar "$0" en el modal Entregar."""
    return (
        session.query(MotivoAnulacionCobro)
        .order_by(MotivoAnulacionCobro.creado_en.asc())
        .all()
    )


def motivo_anulacion_valido(session: Session, etiqueta: str) -> bool:
    """¿Existe hoy en el catálogo un motivo de anulación con este texto
    exacto? -- usado por `packages.py::deliver_action` para rechazar
    server-side un motivo que ya no existe (ej. borrado por otro ADMIN justo
    antes del submit), mismo criterio que
    `motivo_cancelacion_service.motivo_valido`."""
    if not etiqueta:
        return False
    return (
        session.query(MotivoAnulacionCobro)
        .filter(MotivoAnulacionCobro.etiqueta == etiqueta)
        .first()
        is not None
    )


_MAX_LEN_ETIQUETA = 40


def crear_motivo_anulacion(session: Session, etiqueta: str) -> MotivoAnulacionCobro:
    """Crea un motivo nuevo en el catálogo de anulación -- mismo criterio que
    `motivo_cancelacion_service.crear_motivo`, sin la restricción de "no
    dejar el catálogo vacío" (anular a "$0" es opcional, a diferencia de
    cancelar un paquete, que exige motivo siempre).

    Raises:
        ValueError: si la etiqueta queda vacía tras `strip()`, supera los 40
            caracteres, o ya existe otro motivo con el mismo texto exacto.
    """
    limpio = (etiqueta or "").strip()
    if not limpio:
        raise ValueError("El motivo no puede quedar vacío.")
    if len(limpio) > _MAX_LEN_ETIQUETA:
        raise ValueError(f"El motivo no puede superar los {_MAX_LEN_ETIQUETA} caracteres.")

    ya_existe = (
        session.query(MotivoAnulacionCobro)
        .filter(MotivoAnulacionCobro.etiqueta == limpio)
        .first()
        is not None
    )
    if ya_existe:
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')

    motivo = MotivoAnulacionCobro(etiqueta=limpio)
    session.add(motivo)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f'Ya existe un motivo con el texto "{limpio}".')
    return motivo


def eliminar_motivo_anulacion(session: Session, motivo_id) -> None:
    """Borra un motivo del catálogo de anulación (borrado duro -- no toca
    ningún `Cobro` ya anulado con su texto, mismo criterio que
    `motivo_cancelacion_service.eliminar_motivo`).

    Raises:
        ValueError: si `motivo_id` no existe.
    """
    motivo = session.get(MotivoAnulacionCobro, motivo_id)
    if motivo is None:
        raise ValueError("Motivo no encontrado.")
    session.delete(motivo)
    session.flush()


def registrar_cobro(
    session: Session,
    paquete: Paquete,
    desglose: DesgloseCobro,
    actor: Usuario,
    motivo_anulacion: str = None,
) -> Cobro:
    """Persiste el `Cobro` de `paquete` -- llamar SOLO dentro de la misma
    transacción que `paquete_lifecycle.deliver()` (ver
    `packages.py::deliver_action`), nunca de forma aislada: un `Cobro` sin
    paquete `ENTREGADO` no tiene sentido de negocio.

    `motivo_anulacion` es obligatorio cuando `desglose.monto_total == 0` por
    anulación explícita del staff (ver `packages.py::deliver_action`, que ya
    distingue ese caso de un monto $0 por cálculo/primera entrega antes de
    llamar acá) -- esta función no lo re-valida, confía en el caller.
    """
    cobro = Cobro(
        id=uuid.uuid4(),
        paquete_id=paquete.id,
        monto_base=desglose.monto_base,
        bloques_bodegaje=desglose.bloques_bodegaje,
        monto_bodegaje=desglose.monto_bodegaje,
        monto_total=desglose.monto_total,
        motivo_anulacion=motivo_anulacion,
        cobrado_por_usuario_id=actor.id,
        cobrado_en=datetime.now(timezone.utc),
    )
    session.add(cobro)
    session.flush()
    return cobro


@dataclass(frozen=True)
class FilaEstadisticaApartamento:
    torre: str | None
    apartamento: str | None
    recipient_phone: str | None
    cantidad: int
    monto_total: int


@dataclass(frozen=True)
class FilaEstadisticaUsuario:
    """Una fila de la tabla comparativa "Por usuario" (.scratch/
    estadisticas-cobro-interactivas) -- un miembro del staff que registró al
    menos un cobro en el rango/filtros activos."""

    usuario_id: uuid.UUID
    nombre: str
    cantidad: int
    monto_total: int


@dataclass(frozen=True)
class FilaEstadisticaDiaria:
    """Una fila de la serie diaria -- un día del rango, incluidos los días
    sin ningún cobro (`cantidad=0`, `monto_total=0`)."""

    fecha: date
    cantidad: int
    monto_total: int


@dataclass(frozen=True)
class FiltrosEstadisticasCobro:
    """Filtros combinables (AND) de `estadisticas_cobro`, agrupados en un
    solo objeto (.scratch/estadisticas-cobro-interactivas) para no terminar
    con una firma de varios parámetros posicionales sueltos. `anulado=None`
    trae cobrados Y anulados (comportamiento de siempre); `True`/`False`
    acota a uno de los dos grupos (`Cobro.motivo_anulacion` no-nulo/nulo).
    Las 3 páginas son independientes entre sí (una por tabla paginada)."""

    desde: datetime
    hasta: datetime
    tipo: TipoPaquete | None = None
    anulado: bool | None = None
    usuario_id: uuid.UUID | None = None
    pagina_apartamento: int = 1
    pagina_usuario: int = 1
    pagina_diario: int = 1


@dataclass(frozen=True)
class EstadisticasCobro:
    """Agregados de `Cobro` para un rango de fechas y filtros
    (.scratch/cobro-bodegaje ticket 06, extendido en
    .scratch/estadisticas-cobro-interactivas) -- `desde`/`hasta` filtran por
    `Cobro.cobrado_en`."""

    cantidad: int
    monto_total: int
    por_apartamento: list[FilaEstadisticaApartamento]
    total_paginas_apartamento: int
    tiempo_promedio_bodegaje_horas: float | None
    por_usuario: list[FilaEstadisticaUsuario]
    total_paginas_usuario: int
    serie_diaria: list[FilaEstadisticaDiaria]
    total_paginas_diario: int


_FILAS_POR_PAGINA = 20


def _total_paginas(total_filas: int) -> int:
    return max(1, math.ceil(total_filas / _FILAS_POR_PAGINA))


def _base_filtrada(session: Session, filtros: FiltrosEstadisticasCobro, *, incluir_usuario: bool):
    """Query base compartida por todas las secciones de
    `estadisticas_cobro` -- rango de fechas + Tipo + Cobrado/Anulado
    siempre; Usuario solo cuando `incluir_usuario` (la tabla "Por usuario"
    lo omite a propósito, ver su docstring más abajo)."""
    query = session.query(Cobro).join(Paquete, Cobro.paquete_id == Paquete.id).filter(
        Cobro.cobrado_en >= filtros.desde, Cobro.cobrado_en <= filtros.hasta
    )
    if filtros.tipo is not None:
        query = query.filter(Paquete.package_type == filtros.tipo)
    if filtros.anulado is not None:
        condicion = (
            Cobro.motivo_anulacion.isnot(None) if filtros.anulado else Cobro.motivo_anulacion.is_(None)
        )
        query = query.filter(condicion)
    if incluir_usuario and filtros.usuario_id is not None:
        query = query.filter(Cobro.cobrado_por_usuario_id == filtros.usuario_id)
    return query


def estadisticas_cobro(session: Session, filtros: FiltrosEstadisticasCobro) -> EstadisticasCobro:
    """Agregados de cobros entre `filtros.desde` y `filtros.hasta` (ambos
    inclusive, `Cobro.cobrado_en`), combinados (AND) con Tipo de paquete y
    Cobrado/Anulado si vienen seteados -- cantidad y monto total, desglose
    por cliente/apartamento (snapshot del Paquete, ADR-0001 -- nunca la
    unidad ACTUAL de un residente que se haya mudado después;
    `recipient_phone` en el group_by además de Torre/Apartamento, spec.md
    línea 145-146 -- sin esto, dos clientes distintos del mismo apartamento
    se mezclaban en una sola fila), tiempo promedio de bodegaje (horas
    reales entre Recibido y Entregado, solo sobre paquetes que sí tuvieron
    bodegaje -- `bloques_bodegaje > 0`), comparativa "Por usuario", y serie
    diaria. Las 3 tablas ("por_apartamento", "por_usuario", "serie_diaria")
    paginan de forma independiente, `_FILAS_POR_PAGINA` filas cada una."""
    base = _base_filtrada(session, filtros, incluir_usuario=True)

    cantidad = base.count()
    monto_total = base.with_entities(func.coalesce(func.sum(Cobro.monto_total), 0)).scalar()

    # --- Por apartamento (paginado) -------------------------------------- #
    por_apartamento_query = (
        base.with_entities(
            Paquete.snapshot_torre,
            Paquete.snapshot_apartamento,
            Paquete.recipient_phone,
            func.count(Cobro.id),
            func.coalesce(func.sum(Cobro.monto_total), 0),
        )
        .group_by(Paquete.snapshot_torre, Paquete.snapshot_apartamento, Paquete.recipient_phone)
        .order_by(func.sum(Cobro.monto_total).desc())
    )
    total_paginas_apartamento = _total_paginas(por_apartamento_query.count())
    offset_apartamento = (filtros.pagina_apartamento - 1) * _FILAS_POR_PAGINA
    por_apartamento = [
        FilaEstadisticaApartamento(
            torre=torre,
            apartamento=apto,
            recipient_phone=telefono,
            cantidad=cant,
            monto_total=monto,
        )
        for torre, apto, telefono, cant, monto in (
            por_apartamento_query.offset(offset_apartamento).limit(_FILAS_POR_PAGINA).all()
        )
    ]

    # --- Tiempo promedio de bodegaje -------------------------------------- #
    tiempo_promedio = (
        base.filter(Cobro.bloques_bodegaje > 0)
        .with_entities(
            func.avg(
                func.extract("epoch", Paquete.delivered_at - Paquete.received_at) / 3600.0
            )
        )
        .scalar()
    )

    # --- Por usuario (paginado, IGNORA `filtros.usuario_id` a propósito --- #
    # historia 11 del spec: esta tabla sirve para COMPARAR a todo el staff,
    # así que acotarla al propio `<select>` de Usuario la dejaría sin
    # sentido -- respeta fecha/Tipo/Cobrado-Anulado igual que el resto.
    base_todos_usuarios = _base_filtrada(session, filtros, incluir_usuario=False)
    por_usuario_query = (
        base_todos_usuarios.join(Usuario, Cobro.cobrado_por_usuario_id == Usuario.id)
        .with_entities(
            Usuario.id,
            Usuario.nombre,
            func.count(Cobro.id),
            func.coalesce(func.sum(Cobro.monto_total), 0),
        )
        .group_by(Usuario.id, Usuario.nombre)
        .order_by(func.sum(Cobro.monto_total).desc())
    )
    total_paginas_usuario = _total_paginas(por_usuario_query.count())
    offset_usuario = (filtros.pagina_usuario - 1) * _FILAS_POR_PAGINA
    por_usuario = [
        FilaEstadisticaUsuario(usuario_id=uid, nombre=nombre, cantidad=cant, monto_total=monto)
        for uid, nombre, cant, monto in (
            por_usuario_query.offset(offset_usuario).limit(_FILAS_POR_PAGINA).all()
        )
    ]

    # --- Serie diaria (paginada, incluye días sin ningún cobro en $0) ----- #
    # Todos los días del rango, no solo los que tuvieron cobros (decisión
    # explícita del `grilling`) -- se calcula la sub-fecha de ESTA página
    # analíticamente (sin generar los `_FILAS_POR_PAGINA` días de más) y se
    # completan los que no aparezcan en la consulta con cantidad/monto en 0.
    # `func.date(...)` agrupa en el mismo UTC que ya usa el resto del
    # sistema para "día" (ver `admin_estadisticas_cobro`, "el día de hoy, en
    # UTC"), consistente con el propio rango `desde`/`hasta` (también UTC).
    fecha_desde = filtros.desde.date()
    fecha_hasta = filtros.hasta.date()
    total_dias = (fecha_hasta - fecha_desde).days + 1
    total_paginas_diario = _total_paginas(total_dias)
    offset_diario = (filtros.pagina_diario - 1) * _FILAS_POR_PAGINA
    pagina_fecha_inicio = fecha_desde + timedelta(days=offset_diario)
    pagina_fecha_fin = min(
        fecha_desde + timedelta(days=offset_diario + _FILAS_POR_PAGINA - 1), fecha_hasta
    )

    agregados_por_dia = {}
    if pagina_fecha_inicio <= pagina_fecha_fin:
        filas_dia = (
            base.with_entities(
                func.date(Cobro.cobrado_en),
                func.count(Cobro.id),
                func.coalesce(func.sum(Cobro.monto_total), 0),
            )
            .filter(
                func.date(Cobro.cobrado_en) >= pagina_fecha_inicio,
                func.date(Cobro.cobrado_en) <= pagina_fecha_fin,
            )
            .group_by(func.date(Cobro.cobrado_en))
            .all()
        )
        agregados_por_dia = {fecha: (cant, monto) for fecha, cant, monto in filas_dia}

    serie_diaria = []
    fecha_cursor = pagina_fecha_inicio
    while fecha_cursor <= pagina_fecha_fin:
        cant, monto = agregados_por_dia.get(fecha_cursor, (0, 0))
        serie_diaria.append(FilaEstadisticaDiaria(fecha=fecha_cursor, cantidad=cant, monto_total=monto))
        fecha_cursor += timedelta(days=1)

    return EstadisticasCobro(
        cantidad=cantidad,
        monto_total=int(monto_total),
        por_apartamento=por_apartamento,
        total_paginas_apartamento=total_paginas_apartamento,
        tiempo_promedio_bodegaje_horas=(
            float(tiempo_promedio) if tiempo_promedio is not None else None
        ),
        por_usuario=por_usuario,
        total_paginas_usuario=total_paginas_usuario,
        serie_diaria=serie_diaria,
        total_paginas_diario=total_paginas_diario,
    )
