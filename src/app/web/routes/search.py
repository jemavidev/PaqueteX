# -*- coding: utf-8 -*-
"""
Ruta `/search` — consultar el estado de un paquete (vista pública, sin sesión).

Busca por `tracking_number` exacto (este ticket) o, si no coincide, por teléfono
normalizado contra `announced_by_phone`/`recipient_phone` (ticket 02). El timeline
se arma con los timestamps de transición que el Paquete ya tiene — sin exponer al
operador (`*_by_usuario`), que es solo para auditoría interna.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from sqlalchemy import or_

from app.domain.paquete import Paquete
from app.domain.telefono import normalizar_telefono

from ..db import get_db
from ..templating import templates

router = APIRouter()


def _timeline(paquete: Paquete) -> list[dict]:
    """Los hitos OCURRIDOS del Paquete, en orden, sin exponer al operador."""
    hitos = [
        ("Anunciado", paquete.announced_at, None),
        ("Recibido", paquete.received_at, None),
        ("Entregado", paquete.delivered_at, None),
        ("Cancelado", paquete.cancelled_at, paquete.cancel_reason),
    ]
    return [
        {"titulo": titulo, "cuando": cuando, "motivo": motivo}
        for titulo, cuando, motivo in hitos
        if cuando is not None
    ]


@router.get("/consultar", response_class=HTMLResponse)
def search(request: Request, q: str = None, db: Session = Depends(get_db)):
    termino = (q or "").strip()
    if not termino:
        return templates.TemplateResponse(
            "search/form.html", {"request": request, "q": ""}
        )

    paquete = db.query(Paquete).filter(Paquete.tracking_number == termino).one_or_none()
    if paquete is not None:
        return templates.TemplateResponse(
            "search/form.html",
            {
                "request": request,
                "q": termino,
                "paquete": paquete,
                "timeline": _timeline(paquete),
            },
        )

    # No coincide con ningún tracking: se interpreta como teléfono.
    try:
        telefono = normalizar_telefono(termino)
    except ValueError:
        telefono = None

    if telefono is not None:
        paquetes = (
            db.query(Paquete)
            .filter(
                or_(
                    Paquete.announced_by_phone == telefono,
                    Paquete.recipient_phone == telefono,
                )
            )
            .order_by(Paquete.announced_at.desc())
            .all()
        )
        if paquetes:
            return templates.TemplateResponse(
                "search/form.html",
                {"request": request, "q": termino, "paquetes": paquetes},
            )

    return templates.TemplateResponse(
        "search/form.html", {"request": request, "q": termino, "sin_resultados": True}
    )
