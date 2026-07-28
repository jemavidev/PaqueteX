# -*- coding: utf-8 -*-
"""
Ruta `/mis-paquetes` — historial de paquetes del cliente (Grupo 10, Ronda 2).

Protegida por `current_customer`. "Los paquetes que ha manejado" el cliente:
donde su teléfono aparece como Anunciante O como Destinatario — cubre tanto
"lo que anuncié" como "lo que me anunciaron a mí". Cada fila enlaza a su
detalle en `/consultar` vía `access_code` (misma pantalla que ya usa
cualquiera con el código en la mano, sin duplicar la vista de detalle).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.paquete import Paquete
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
    return templates.TemplateResponse(
        "customer/paquetes.html",
        {"request": request, "persona": persona, "paquetes": paquetes},
    )
