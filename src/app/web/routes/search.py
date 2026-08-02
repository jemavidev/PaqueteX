# -*- coding: utf-8 -*-
"""
Ruta `/consultar` — consultar el estado de un paquete (vista pública, sin
sesión).

Busca SOLO por `access_code` o `guide_number` exactos (Grupo 2 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`) — a propósito, NUNCA
por teléfono: el `access_code` únicamente lo conoce quien anunció, así que es
la única llave de consulta pública. El timeline se arma con los timestamps de
transición que el Paquete ya tiene, e incluye quién hizo cada hito (Grupo 11
de la Ronda 2 — revierte la decisión original de ocultar el actor). El hito
"Recibido" también expone `guide_number` (corrección en vivo 2026-08-01) —
existía en el modelo pero nunca llegaba a la plantilla, así que el cliente
nunca podía verlo.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from sqlalchemy import or_

from app.domain.actor_service import nombre_usuario
from app.domain.paquete import Paquete
from app.domain.paquete_foto_service import listar_fotos
from app.domain.persona import Persona

from ..db import get_db
from ..templating import templates

router = APIRouter()


def _actor_anunciado(session: Session, paquete: Paquete) -> str | None:
    """Quién anunció: el `Usuario` staff si anunció vía `/announce`, o el
    nombre de la `Persona` anunciante si fue el propio cliente vía
    `/anunciar` (caso normal — `announced_by_usuario_id` es `None`)."""
    nombre_staff = nombre_usuario(session, paquete.announced_by_usuario_id)
    if nombre_staff is not None:
        return f"{nombre_staff} (staff)"
    persona = session.get(Persona, paquete.announced_by_persona_id)
    if persona is not None and persona.nombre:
        return f"{persona.nombre} (cliente)"
    return None


def _timeline(session: Session, paquete: Paquete) -> list[dict]:
    """Los hitos OCURRIDOS del Paquete, en orden, cada uno con quién lo hizo."""
    hitos = [
        ("Anunciado", paquete.announced_at, None, _actor_anunciado(session, paquete)),
        (
            "Recibido",
            paquete.received_at,
            None,
            _actor_staff(session, paquete.received_by_usuario_id),
            paquete.package_type,
            paquete.package_condition,
            paquete.guide_number,
        ),
        (
            "Entregado",
            paquete.delivered_at,
            None,
            _actor_staff(session, paquete.delivered_by_usuario_id),
        ),
        (
            "Cancelado",
            paquete.cancelled_at,
            paquete.cancel_reason,
            _actor_staff(session, paquete.cancelled_by_usuario_id),
        ),
    ]
    resultado = []
    for hito in hitos:
        titulo, cuando = hito[0], hito[1]
        if cuando is None:
            continue
        motivo = hito[2]
        actor = hito[3]
        tipo = hito[4] if len(hito) > 4 else None
        condicion = hito[5] if len(hito) > 5 else None
        guia = hito[6] if len(hito) > 6 else None
        resultado.append(
            {
                "titulo": titulo,
                "cuando": cuando,
                "motivo": motivo,
                "actor": actor,
                "tipo": tipo,
                "condicion": condicion,
                "guia": guia,
            }
        )
    return resultado


def _actor_staff(session: Session, usuario_id) -> str | None:
    nombre = nombre_usuario(session, usuario_id)
    return f"{nombre} (staff)" if nombre else None


@router.get("/consultar", response_class=HTMLResponse)
def search(request: Request, q: str = None, db: Session = Depends(get_db)):
    termino = (q or "").strip()
    if not termino:
        return templates.TemplateResponse(
            "search/form.html", {"request": request, "q": ""}
        )

    paquete = (
        db.query(Paquete)
        .filter(
            or_(Paquete.access_code == termino, Paquete.guide_number == termino)
        )
        .one_or_none()
    )
    if paquete is not None:
        return templates.TemplateResponse(
            "search/form.html",
            {
                "request": request,
                "q": termino,
                "paquete": paquete,
                "timeline": _timeline(db, paquete),
                "fotos": listar_fotos(db, paquete),
            },
        )

    return templates.TemplateResponse(
        "search/form.html", {"request": request, "q": termino, "sin_resultados": True}
    )
