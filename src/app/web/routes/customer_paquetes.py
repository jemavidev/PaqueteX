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

Issue 235 (.scratch/pendientes-cliente, pedido explícito del cliente): el
alcance de TODA la unidad es exclusivo del Ocupante PRINCIPAL -- un
no-Principal (o alguien sin Ocupante activo, p.ej. sin Apartamento) solo ve
lo propio. El helper de dominio no cambia (sigue siendo "toda la unidad",
de propósito general); el gate vive acá, antes de decidir qué Teléfonos
consultar.

Issue 238 (.scratch/pendientes-cliente, bug real reportado en vivo tras
235): "lo propio" para un no-Principal es SOLO por `recipient_phone` --
NO por `announced_by_phone`. Un no-Principal que anuncia un paquete PARA
otro residente de su unidad (destinatario != "yo mismo") seguía viendo
ese paquete (con el nombre del OTRO residente) porque `announced_by_phone`
también matcheaba su propio teléfono -- justo lo que el pedido original de
235 excluye explícitamente ("solo paquetes que estén A NOMBRE DE quien
entró en la cuenta"). El Principal sigue viendo TODO (anunciado o
recibido, de cualquier Teléfono de la unidad) -- este filtro más estricto
es exclusivo del camino no-Principal.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.ocupante_service import (
    ocupante_activo_de_persona,
    telefonos_activos_del_apartamento_de,
)
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
    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    es_principal = mi_ocupante is not None and mi_ocupante.es_principal
    if es_principal:
        telefonos = telefonos_activos_del_apartamento_de(db, persona)
        condicion = or_(
            Paquete.announced_by_phone.in_(telefonos),
            Paquete.recipient_phone.in_(telefonos),
        )
    else:
        # Issue 238: solo por `recipient_phone` -- ver docstring del módulo.
        condicion = Paquete.recipient_phone == persona.telefono

    paquetes = (
        db.query(Paquete)
        .filter(condicion)
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
