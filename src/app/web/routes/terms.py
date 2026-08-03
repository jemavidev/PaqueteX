# -*- coding: utf-8 -*-
"""
Ruta `/terminos` — términos y condiciones del servicio de anuncios de paquetes.

Pública, sin sesión. Mismo patrón que `/ayuda`: página estática, contenido
mantenido a mano en el template. Creada para que el checkbox de T&C de
`/anunciar` enlace a algo real dentro del propio rebuild, en vez de a un
dominio externo (corrección en vivo 2026-08-01) — el texto es un marcador de
posición razonable, no una revisión legal.

Ruta renombrada de `/terms` a `/terminos` (retroalimentación en vivo
2026-08-02: "en las vistas veo que se llaman /terms, /privacy y /cookies"
-- el resto del rebuild usa rutas en español, `/terms` desentonaba). El
archivo/carpeta de la plantilla (`terms/form.html`) se deja igual, es un
detalle interno sin URL propia.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/terminos", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse("terms/form.html", {"request": request})
