# -*- coding: utf-8 -*-
"""
Servicio de dominio de `MovimientoSaldoContraEntrega` (módulo "Gestión de
dinero contra entrega", `.scratch/dinero-contra-entrega`).
"""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .persona import Persona
from .saldo_contra_entrega import MovimientoSaldoContraEntrega
from .usuario import Usuario


def registrar_movimiento_saldo(
    session: Session,
    persona_id,
    monto: int,
    staff: Usuario,
    paquete_id=None,
) -> MovimientoSaldoContraEntrega:
    """Registra un movimiento de saldo -- único punto que crea uno, reusado
    igual para depósito, pago a mensajero (`monto` negativo), ajuste en
    Entregar, y recuperación posterior. NO valida el signo del saldo
    resultante: puede quedar negativo (una deuda) sin bloquear nada."""
    movimiento = MovimientoSaldoContraEntrega(
        persona_id=persona_id,
        monto=monto,
        paquete_id=paquete_id,
        registrado_por_usuario_id=staff.id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(movimiento)
    session.flush()
    return movimiento


def saldo_de_persona(session: Session, persona_id) -> int:
    """La suma de todos los movimientos de `persona_id` -- 0 si nunca tuvo
    ninguno. Sin campo desnormalizado: siempre se recalcula."""
    total = (
        session.query(func.coalesce(func.sum(MovimientoSaldoContraEntrega.monto), 0))
        .filter(MovimientoSaldoContraEntrega.persona_id == persona_id)
        .scalar()
    )
    return int(total)


def movimientos_de_persona(session: Session, persona_id) -> list[MovimientoSaldoContraEntrega]:
    """Historial de movimientos de `persona_id`, más recientes primero --
    para que el propio residente vea a qué paquete se aplicó cada uno
    (.scratch/dinero-contra-entrega, ticket 05). NUNCA el de otro
    residente, aunque comparta apartamento -- el saldo es utilizable por
    compañeros de unidad, pero le pertenece a quien lo depositó."""
    return (
        session.query(MovimientoSaldoContraEntrega)
        .filter(MovimientoSaldoContraEntrega.persona_id == persona_id)
        .order_by(MovimientoSaldoContraEntrega.created_at.desc())
        .all()
    )


def personas_con_saldo_no_cero(session: Session, q: str = None) -> list[tuple[Persona, int]]:
    """`[(Persona, saldo)]` para toda Persona cuyo saldo (suma de sus
    movimientos) sea distinto de cero -- una sola consulta agregada (nunca
    un `saldo_de_persona` por cada residente del padrón), para el listado
    de `/residentes/saldos-contra-entrega`. `q` filtra por nombre parcial."""
    query = (
        session.query(Persona, func.sum(MovimientoSaldoContraEntrega.monto))
        .join(
            MovimientoSaldoContraEntrega,
            MovimientoSaldoContraEntrega.persona_id == Persona.id,
        )
        .group_by(Persona.id)
        .having(func.sum(MovimientoSaldoContraEntrega.monto) != 0)
        .order_by(Persona.nombre.asc())
    )
    if q:
        query = query.filter(Persona.nombre.ilike(f"%{q}%"))
    return [(persona, int(saldo)) for persona, saldo in query.all()]


def personas_con_historial_en_apartamento(session: Session, apartamento_id) -> list[Persona]:
    """Personas del apartamento ACTUAL dado (no un snapshot congelado de
    ningún paquete) que tienen al menos un movimiento de saldo registrado --
    para poblar el selector "de quién se descuenta" en Recibir. Si el
    residente se muda, deja de aparecer acá para su unidad vieja y empieza a
    aparecer para la nueva, aunque el saldo en sí siga siendo suyo."""
    return (
        session.query(Persona)
        .join(
            MovimientoSaldoContraEntrega,
            MovimientoSaldoContraEntrega.persona_id == Persona.id,
        )
        .filter(Persona.apartamento_actual_id == apartamento_id)
        .distinct()
        .all()
    )


def saldos_de_personas(session: Session, persona_ids) -> dict:
    """`{persona_id: saldo}` para un lote de ids -- UNA sola consulta
    agrupada (mismo criterio "un puñado fijo de consultas" que el resto de
    `packages.py::_listar`), en vez de `saldo_de_persona` por cada paquete
    RECIBIDO de la página."""
    ids = list(persona_ids)
    if not ids:
        return {}
    filas = (
        session.query(
            MovimientoSaldoContraEntrega.persona_id,
            func.sum(MovimientoSaldoContraEntrega.monto),
        )
        .filter(MovimientoSaldoContraEntrega.persona_id.in_(ids))
        .group_by(MovimientoSaldoContraEntrega.persona_id)
        .all()
    )
    return {persona_id: int(total) for persona_id, total in filas}


def personas_con_historial_por_apartamentos(session: Session, apartamento_ids) -> dict:
    """`{apartamento_id: [Persona, ...]}` para un lote de apartamentos --
    UNA sola consulta (mismo criterio que `saldos_de_personas`), en vez de
    `personas_con_historial_en_apartamento` por cada paquete ANUNCIADO de
    la página."""
    ids = list(apartamento_ids)
    if not ids:
        return {}
    personas = (
        session.query(Persona)
        .join(
            MovimientoSaldoContraEntrega,
            MovimientoSaldoContraEntrega.persona_id == Persona.id,
        )
        .filter(Persona.apartamento_actual_id.in_(ids))
        .distinct()
        .all()
    )
    resultado: dict = {aid: [] for aid in ids}
    for persona in personas:
        resultado[persona.apartamento_actual_id].append(persona)
    return resultado
