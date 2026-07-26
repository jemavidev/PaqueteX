# -*- coding: utf-8 -*-
"""
Ruta `/consultar` — consultar el estado de un paquete (vista pública, sin
sesión).

Busca SOLO por `access_code` o `guide_number` exactos (Grupo 2 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`) — a propósito, NUNCA
por teléfono: el `access_code` únicamente lo conoce quien anunció, así que es
la única llave de consulta pública. El timeline se arma con los timestamps de
transición que el Paquete ya tiene — sin exponer al operador (`*_by_usuario`),
que es solo para auditoría interna.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from sqlalchemy import or_

from app.domain.paquete import Paquete
from app.domain.paquete_foto_service import listar_fotos

from ..db import get_db
from ..templating import templates

router = APIRouter()


def _timeline(paquete: Paquete) -> list[dict]:
    """Los hitos OCURRIDOS del Paquete, en orden, sin exponer al operador."""
    hitos = [
        ("Anunciado", paquete.announced_at, None),
        (
            "Recibido",
            paquete.received_at,
            None,
            paquete.package_type,
            paquete.package_condition,
        ),
        ("Entregado", paquete.delivered_at, None),
        ("Cancelado", paquete.cancelled_at, paquete.cancel_reason),
    ]
    resultado = []
    for hito in hitos:
        titulo, cuando = hito[0], hito[1]
        if cuando is None:
            continue
        motivo = hito[2]
        tipo = hito[3] if len(hito) > 3 else None
        condicion = hito[4] if len(hito) > 4 else None
        resultado.append(
            {
                "titulo": titulo,
                "cuando": cuando,
                "motivo": motivo,
                "tipo": tipo,
                "condicion": condicion,
            }
        )
    return resultado


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
                "timeline": _timeline(paquete),
                "fotos": listar_fotos(db, paquete),
            },
        )

    return templates.TemplateResponse(
        "search/form.html", {"request": request, "q": termino, "sin_resultados": True}
    )
