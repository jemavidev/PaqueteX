# -*- coding: utf-8 -*-
"""
Servicio de dominio de `MovimientoSaldoContraEntrega` (módulo "Gestión de
dinero contra entrega", `.scratch/dinero-contra-entrega`).
"""

from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .paquete import Paquete
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


_POR_PAGINA = 20


def listar_movimientos_saldo(
    session: Session, q: str = None, tipo: str = None, pagina: int = 1
) -> tuple[list[MovimientoSaldoContraEntrega], int]:
    """Ledger global paginado de TODOS los movimientos de saldo contra
    entrega, de TODAS las Personas -- para el ledger de staff (.scratch/
    dinero-contra-entrega-control, ticket 02), a diferencia de
    `movimientos_de_persona` (acotada a una sola). Devuelve `(movimientos,
    total_paginas)`; cada movimiento trae `.persona_nombre`, `.registrado_
    por_nombre` y `.paquete_access_code` ya resueltos (batch, mismo criterio
    "un puñado fijo de consultas" que `packages.py::_listar`), nunca una
    consulta por fila.

    `q` filtra por nombre, teléfono o usuario de WhatsApp de la Persona
    dueña del movimiento (coincidencia parcial). `tipo` filtra por signo de
    `monto`: `'ingreso'` (positivo) o `'egreso'` (negativo) -- cualquier
    otro valor, incluido `None`, no filtra."""
    query = session.query(MovimientoSaldoContraEntrega)

    termino = (q or "").strip()
    if termino:
        ids_persona = [
            row.id
            for row in session.query(Persona.id).filter(
                or_(
                    Persona.nombre.ilike(f"%{termino}%"),
                    Persona.telefono.ilike(f"%{termino}%"),
                    Persona.whatsapp_usuario.ilike(f"%{termino}%"),
                )
            )
        ]
        query = query.filter(MovimientoSaldoContraEntrega.persona_id.in_(ids_persona))

    if tipo == "ingreso":
        query = query.filter(MovimientoSaldoContraEntrega.monto > 0)
    elif tipo == "egreso":
        query = query.filter(MovimientoSaldoContraEntrega.monto < 0)

    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))
    pagina = max(1, min(pagina, total_paginas))
    movimientos = (
        query.order_by(MovimientoSaldoContraEntrega.created_at.desc())
        .offset((pagina - 1) * _POR_PAGINA)
        .limit(_POR_PAGINA)
        .all()
    )

    if movimientos:
        personas = {
            p.id: p
            for p in session.query(Persona)
            .filter(Persona.id.in_({m.persona_id for m in movimientos}))
            .all()
        }
        usuarios = {
            u.id: u
            for u in session.query(Usuario)
            .filter(Usuario.id.in_({m.registrado_por_usuario_id for m in movimientos}))
            .all()
        }
        paquete_ids = {m.paquete_id for m in movimientos if m.paquete_id is not None}
        paquetes = (
            {p.id: p for p in session.query(Paquete).filter(Paquete.id.in_(paquete_ids)).all()}
            if paquete_ids
            else {}
        )
        for movimiento in movimientos:
            persona = personas.get(movimiento.persona_id)
            movimiento.persona_nombre = persona.nombre if persona else None
            usuario = usuarios.get(movimiento.registrado_por_usuario_id)
            movimiento.registrado_por_nombre = usuario.nombre if usuario else None
            paquete = paquetes.get(movimiento.paquete_id) if movimiento.paquete_id else None
            movimiento.paquete_access_code = paquete.access_code if paquete else None

    return movimientos, total_paginas


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
