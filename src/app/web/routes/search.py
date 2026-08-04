# -*- coding: utf-8 -*-
"""
Ruta `/consultar` — consultar el estado de un paquete (vista pública, sin
sesión).

Busca SOLO por `access_code` o `guide_number` exactos (Grupo 2 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`) — a propósito, NUNCA
por teléfono: el `access_code` únicamente lo conoce quien anunció, así que es
la única llave de consulta pública. El timeline (con actor por hito, y
`dias_desde_recibido`) vive en `paquete_timeline_service` — compartido con
`/mis-paquetes`, que cuenta la misma historia del mismo paquete para el
cliente autenticado.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from sqlalchemy import or_

from app.domain.paquete import Paquete
from app.domain.paquete_foto_service import listar_fotos
from app.domain.paquete_timeline_service import dias_desde_recibido, timeline_de_paquete

from ..db import get_db
from ..templating import templates

router = APIRouter()


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
                "timeline": timeline_de_paquete(db, paquete),
                "fotos": listar_fotos(db, paquete),
                "dias_desde_recibido": dias_desde_recibido(paquete),
            },
        )

    return templates.TemplateResponse(
        "search/form.html", {"request": request, "q": termino, "sin_resultados": True}
    )
