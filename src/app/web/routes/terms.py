# -*- coding: utf-8 -*-
"""
Ruta `/terms` — términos y condiciones del servicio de anuncios de paquetes.

Pública, sin sesión. Mismo patrón que `/ayuda`: página estática, contenido
mantenido a mano en el template. Creada para que el checkbox de T&C de
`/anunciar` enlace a algo real dentro del propio rebuild, en vez de a un
dominio externo (corrección en vivo 2026-08-01) — el texto es un marcador de
posición razonable, no una revisión legal.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse("terms/form.html", {"request": request})
