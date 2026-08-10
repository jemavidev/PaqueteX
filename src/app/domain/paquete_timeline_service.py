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
from .usuario import Usuario


def actor_staff(session: Session, usuario_id) -> str | None:
    return nombre_usuario(session, usuario_id)


def actor_anunciado(session: Session, paquete: Paquete) -> str | None:
    """Quién anunció: el `Usuario` staff si anunció vía `/announce`, o el
    nombre de la `Persona` anunciante si fue el propio cliente vía
    `/anunciar` (caso normal -- `announced_by_usuario_id` es `None`). Solo
    el nombre -- sin "(cliente)"/"(staff)" (pedido del cliente,
    .scratch/pendientes-cliente): quién es cada quien ya se sobreentiende
    por el verbo del hito (Anunció/Recibió/Entregó/Canceló)."""
    nombre_staff = nombre_usuario(session, paquete.announced_by_usuario_id)
    if nombre_staff is not None:
        return nombre_staff
    persona = session.get(Persona, paquete.announced_by_persona_id)
    if persona is not None and persona.nombre:
        return persona.nombre
    return None


def _armar_timeline(
    paquete: Paquete,
    actor_anunciado_nombre: str | None,
    actor_recibido: str | None,
    actor_entregado: str | None,
    actor_cancelado: str | None,
) -> list[dict]:
    """Arma los hitos OCURRIDOS del Paquete, en orden, a partir de nombres de
    actor YA resueltos -- compartida por `timeline_de_paquete` (resuelve uno
    por uno) y `timelines_de_paquetes` (todo pre-cargado en batch), para no
    duplicar la forma del timeline entre las dos."""
    hitos = [
        ("Anunciado", paquete.announced_at, None, actor_anunciado_nombre),
        (
            "Recibido",
            paquete.received_at,
            None,
            actor_recibido,
            paquete.package_type,
            paquete.package_condition,
            paquete.guide_number,
        ),
        ("Entregado", paquete.delivered_at, None, actor_entregado),
        ("Cancelado", paquete.cancelled_at, paquete.cancel_reason, actor_cancelado),
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


def timeline_de_paquete(session: Session, paquete: Paquete) -> list[dict]:
    """Los hitos OCURRIDOS del Paquete, en orden, cada uno con quién lo hizo."""
    return _armar_timeline(
        paquete,
        actor_anunciado(session, paquete),
        actor_staff(session, paquete.received_by_usuario_id),
        actor_staff(session, paquete.delivered_by_usuario_id),
        actor_staff(session, paquete.cancelled_by_usuario_id),
    )


def timelines_de_paquetes(session: Session, paquetes: list[Paquete]) -> dict:
    """Versión batch de `timeline_de_paquete` -- MISMA lógica (comparten
    `_armar_timeline`), pero resuelve los actores (Usuario/Persona) de TODA
    la lista de paquetes en un puñado fijo de consultas en vez de repetir
    `nombre_usuario`/`session.get(Persona)` por cada hito de cada paquete
    (auditoría de rendimiento 2026-08-10, `.scratch/pendientes-cliente`:
    `/mis-paquetes` no pagina el historial de un Apartamento, así que sin
    esto el N+1 escala con todo el histórico, no solo una página).

    Returns:
        `{paquete.id: hitos}`, mismo formato de `timeline_de_paquete` para
        cada Paquete de `paquetes`.
    """
    usuario_ids = set()
    persona_ids = set()
    for p in paquetes:
        usuario_ids.update(
            {
                p.announced_by_usuario_id,
                p.received_by_usuario_id,
                p.delivered_by_usuario_id,
                p.cancelled_by_usuario_id,
            }
        )
        if p.announced_by_persona_id is not None:
            persona_ids.add(p.announced_by_persona_id)
    usuario_ids.discard(None)

    usuarios = {}
    if usuario_ids:
        usuarios = {
            u.id: u.nombre
            for u in session.query(Usuario).filter(Usuario.id.in_(usuario_ids)).all()
        }
    personas = {}
    if persona_ids:
        personas = {
            p.id: p.nombre
            for p in session.query(Persona).filter(Persona.id.in_(persona_ids)).all()
        }

    def _actor_staff(usuario_id) -> str | None:
        return usuarios.get(usuario_id) if usuario_id is not None else None

    def _actor_anunciado(p: Paquete) -> str | None:
        nombre_staff = _actor_staff(p.announced_by_usuario_id)
        if nombre_staff is not None:
            return nombre_staff
        if p.announced_by_persona_id is None:
            return None
        return personas.get(p.announced_by_persona_id) or None

    return {
        p.id: _armar_timeline(
            p,
            _actor_anunciado(p),
            _actor_staff(p.received_by_usuario_id),
            _actor_staff(p.delivered_by_usuario_id),
            _actor_staff(p.cancelled_by_usuario_id),
        )
        for p in paquetes
    }


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
