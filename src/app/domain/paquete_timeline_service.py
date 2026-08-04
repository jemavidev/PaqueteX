# -*- coding: utf-8 -*-
"""
Timeline y datos derivados de un Paquete para mostrarlo (Seam A, sin
dependencias del framework web) -- compartido entre `/consultar` (público,
por access_code/guía) y `/mis-paquetes` (cliente autenticado, su propio
historial) para que ambas vistas cuenten exactamente la misma historia del
mismo paquete, con el mismo código.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .actor_service import nombre_usuario
from .paquete import EstadoPaquete, Paquete
from .persona import Persona


def actor_staff(session: Session, usuario_id) -> str | None:
    nombre = nombre_usuario(session, usuario_id)
    return f"{nombre} (staff)" if nombre else None


def actor_anunciado(session: Session, paquete: Paquete) -> str | None:
    """Quién anunció: el `Usuario` staff si anunció vía `/announce`, o el
    nombre de la `Persona` anunciante si fue el propio cliente vía
    `/anunciar` (caso normal -- `announced_by_usuario_id` es `None`)."""
    nombre_staff = nombre_usuario(session, paquete.announced_by_usuario_id)
    if nombre_staff is not None:
        return f"{nombre_staff} (staff)"
    persona = session.get(Persona, paquete.announced_by_persona_id)
    if persona is not None and persona.nombre:
        return f"{persona.nombre} (cliente)"
    return None


def timeline_de_paquete(session: Session, paquete: Paquete) -> list[dict]:
    """Los hitos OCURRIDOS del Paquete, en orden, cada uno con quién lo hizo."""
    hitos = [
        ("Anunciado", paquete.announced_at, None, actor_anunciado(session, paquete)),
        (
            "Recibido",
            paquete.received_at,
            None,
            actor_staff(session, paquete.received_by_usuario_id),
            paquete.package_type,
            paquete.package_condition,
            paquete.guide_number,
        ),
        ("Entregado", paquete.delivered_at, None, actor_staff(session, paquete.delivered_by_usuario_id)),
        ("Cancelado", paquete.cancelled_at, paquete.cancel_reason, actor_staff(session, paquete.cancelled_by_usuario_id)),
    ]
    resultado = []
    for hito in hitos:
        titulo, cuando = hito[0], hito[1]
        if cuando is None:
            continue
        resultado.append(
            {
                "titulo": titulo,
                "cuando": cuando,
                "motivo": hito[2],
                "actor": hito[3],
                "tipo": hito[4] if len(hito) > 4 else None,
                "condicion": hito[5] if len(hito) > 5 else None,
                "guia": hito[6] if len(hito) > 6 else None,
            }
        )
    return resultado


def dias_desde_recibido(paquete: Paquete) -> int | None:
    if paquete.received_at is None:
        return None
    return (datetime.now(timezone.utc) - paquete.received_at).days


_FECHA_POR_ESTADO = {
    EstadoPaquete.ANUNCIADO: "announced_at",
    EstadoPaquete.RECIBIDO: "received_at",
    EstadoPaquete.ENTREGADO: "delivered_at",
    EstadoPaquete.CANCELADO: "cancelled_at",
}

_VERBO_POR_ESTADO = {
    EstadoPaquete.ANUNCIADO: "Anunciado",
    EstadoPaquete.RECIBIDO: "Recibido",
    EstadoPaquete.ENTREGADO: "Entregado",
    EstadoPaquete.CANCELADO: "Cancelado",
}


def fecha_relevante(paquete: Paquete) -> datetime:
    """La fecha del hito MÁS RECIENTE según el estado actual -- no siempre
    `announced_at` (ej. un paquete Entregado: lo que importa a simple vista
    es cuándo se entregó, no cuándo se anunció originalmente)."""
    campo = _FECHA_POR_ESTADO.get(paquete.estado, "announced_at")
    return getattr(paquete, campo) or paquete.announced_at


def verbo_estado(paquete: Paquete) -> str:
    return _VERBO_POR_ESTADO.get(paquete.estado, paquete.estado.value.capitalize())
