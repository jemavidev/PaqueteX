# -*- coding: utf-8 -*-
"""
Ruta `/mis-paquetes` — historial de paquetes del Apartamento del cliente
(Grupo 10, Ronda 2, rediseño en pestañas `.scratch/pendientes-cliente/
issues/42`; alcance ampliado a todo el Apartamento en
`.scratch/mis-paquetes-vista-apartamento/issues/01`).

Protegida por `current_customer`. "Los paquetes que ha manejado" la unidad:
de CUALQUIER Ocupante activo del mismo Apartamento que la sesión actual
(incluida ella misma), donde ese Teléfono aparece como Anunciante O como
Destinatario — cubre tanto "lo que anunciamos" como "lo que nos anunciaron
a nosotros". Sin Apartamento asignado, el alcance es idéntico al de antes
(solo el propio teléfono) — `telefonos_activos_del_apartamento_de` resuelve
esa diferencia. Cada paquete se muestra en su propia tarjeta con timeline
expandible EN LA MISMA vista (ya no manda a `/consultar`) — mismo
timeline/fotos/`dias_desde_recibido` que esa vista pública, vía
`paquete_timeline_service` compartido, para contar la misma historia con
el mismo código.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.ocupante_service import telefonos_activos_del_apartamento_de
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_foto_service import fotos_por_paquetes
from app.domain.paquete_timeline_service import (
    dias_desde_recibido,
    fecha_relevante,
    timelines_de_paquetes,
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
    telefonos = telefonos_activos_del_apartamento_de(db, persona)
    paquetes = (
        db.query(Paquete)
        .filter(
            or_(
                Paquete.announced_by_phone.in_(telefonos),
                Paquete.recipient_phone.in_(telefonos),
            )
        )
        .order_by(Paquete.announced_at.desc())
        .all()
    )

    timelines = timelines_de_paquetes(db, paquetes)
    fotos = fotos_por_paquetes(db, paquetes)

    conteos = {estado.value: 0 for estado in EstadoPaquete}
    items = []
    for p in paquetes:
        conteos[p.estado.value] += 1
        items.append(
            {
                "paquete": p,
                "fecha_relevante": fecha_relevante(p),
                "verbo_estado": verbo_estado(p),
                "timeline": timelines[p.id],
                "fotos": fotos[p.id],
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
