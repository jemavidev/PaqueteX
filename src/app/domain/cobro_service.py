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
from datetime import datetime, timezone

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
class EstadisticasCobro:
    """Agregados de `Cobro` para un rango de fechas (.scratch/cobro-bodegaje,
    ticket 06) -- `desde`/`hasta` filtran por `Cobro.cobrado_en`."""

    cantidad: int
    monto_total: int
    por_apartamento: list[FilaEstadisticaApartamento]
    tiempo_promedio_bodegaje_horas: float | None


def estadisticas_cobro(session: Session, desde: datetime, hasta: datetime) -> EstadisticasCobro:
    """Agregados de cobros entre `desde` y `hasta` (ambos inclusive,
    `Cobro.cobrado_en`) -- cantidad y monto total, desglose por
    cliente/apartamento (snapshot del Paquete, ADR-0001 -- nunca la unidad
    ACTUAL de un residente que se haya mudado después; `recipient_phone` en
    el group_by además de Torre/Apartamento, spec.md línea 145-146 -- sin
    esto, dos clientes distintos del mismo apartamento se mezclaban en una
    sola fila), y tiempo promedio de bodegaje (horas reales entre Recibido y
    Entregado, solo sobre paquetes que sí tuvieron bodegaje --
    `bloques_bodegaje > 0`)."""
    base = session.query(Cobro).join(Paquete, Cobro.paquete_id == Paquete.id).filter(
        Cobro.cobrado_en >= desde, Cobro.cobrado_en <= hasta
    )

    cantidad = base.count()
    monto_total = base.with_entities(func.coalesce(func.sum(Cobro.monto_total), 0)).scalar()

    por_apartamento_rows = (
        base.with_entities(
            Paquete.snapshot_torre,
            Paquete.snapshot_apartamento,
            Paquete.recipient_phone,
            func.count(Cobro.id),
            func.coalesce(func.sum(Cobro.monto_total), 0),
        )
        .group_by(Paquete.snapshot_torre, Paquete.snapshot_apartamento, Paquete.recipient_phone)
        .order_by(func.sum(Cobro.monto_total).desc())
        .all()
    )
    por_apartamento = [
        FilaEstadisticaApartamento(
            torre=torre,
            apartamento=apto,
            recipient_phone=telefono,
            cantidad=cant,
            monto_total=monto,
        )
        for torre, apto, telefono, cant, monto in por_apartamento_rows
    ]

    tiempo_promedio = (
        base.filter(Cobro.bloques_bodegaje > 0)
        .with_entities(
            func.avg(
                func.extract("epoch", Paquete.delivered_at - Paquete.received_at) / 3600.0
            )
        )
        .scalar()
    )

    return EstadisticasCobro(
        cantidad=cantidad,
        monto_total=int(monto_total),
        por_apartamento=por_apartamento,
        tiempo_promedio_bodegaje_horas=(
            float(tiempo_promedio) if tiempo_promedio is not None else None
        ),
    )
