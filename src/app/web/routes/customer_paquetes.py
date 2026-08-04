# -*- coding: utf-8 -*-
"""
Ruta `/mis-paquetes` — historial de paquetes del cliente (Grupo 10, Ronda 2;
rediseño en pestañas por estado, `.scratch/pendientes-cliente/issues/42`).

Protegida por `current_customer`. "Los paquetes que ha manejado" el cliente:
donde su teléfono aparece como Anunciante O como Destinatario — cubre tanto
"lo que anuncié" como "lo que me anunciaron a mí". Cada paquete se muestra
en su propia tarjeta con timeline expandible EN LA MISMA vista (ya no manda
a `/consultar`) — mismo timeline/fotos/`dias_desde_recibido` que esa vista
pública, vía `paquete_timeline_service` compartido, para contar la misma
historia con el mismo código.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_foto_service import listar_fotos
from app.domain.paquete_timeline_service import (
    dias_desde_recibido,
    fecha_relevante,
    timeline_de_paquete,
    ubicacion_paquete,
    verbo_estado,
)
from app.domain.persona import Persona

from ..db import get_db
from ..security import current_customer
from ..templating import templates

router = APIRouter()


@router.get("/mis-paquetes", response_class=HTMLResponse)
def mis_paquetes(
    request: Request,
    persona: Persona = Depends(current_customer),
    db: Session = Depends(get_db),
):
    paquetes = (
        db.query(Paquete)
        .filter(
            or_(
                Paquete.announced_by_phone == persona.telefono,
                Paquete.recipient_phone == persona.telefono,
            )
        )
        .order_by(Paquete.announced_at.desc())
        .all()
    )

    conteos = {estado.value: 0 for estado in EstadoPaquete}
    items = []
    for p in paquetes:
        conteos[p.estado.value] += 1
        items.append(
            {
                "paquete": p,
                "ubicacion": ubicacion_paquete(p),
                "fecha_relevante": fecha_relevante(p),
                "verbo_estado": verbo_estado(p),
                "timeline": timeline_de_paquete(db, p),
                "fotos": listar_fotos(db, p),
                "dias_desde_recibido": dias_desde_recibido(p),
            }
        )

    return templates.TemplateResponse(
        "customer/paquetes.html",
        {
            "request": request,
            "persona": persona,
            "items": items,
            "conteos": conteos,
        },
    )
