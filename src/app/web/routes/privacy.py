# -*- coding: utf-8 -*-
"""
Ruta `/privacidad` — política de privacidad.

Pública, sin sesión. Mismo patrón que `/terminos`/`/ayuda`: página estática,
contenido mantenido a mano en el template. Contenido base traído de
`paquetex.papyrus.com.co/privacy` (retroalimentación en vivo 2026-08-02:
"llénala con el contenido de lo que existe en producción... más adelante
estaremos actualizando el contenido") -- marcador de posición razonable,
no una revisión legal.

Ruta renombrada de `/privacy` a `/privacidad` (retroalimentación en vivo
2026-08-02: el resto del rebuild usa rutas en español). El archivo/carpeta
de la plantilla (`privacy/form.html`) se deja igual, es un detalle interno
sin URL propia.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/privacidad", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse("privacy/form.html", {"request": request})
