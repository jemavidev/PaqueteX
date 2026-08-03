# -*- coding: utf-8 -*-
"""
Ruta `/cookies` — política de cookies.

Pública, sin sesión. Mismo patrón que `/terminos`/`/privacidad`/`/ayuda`:
página estática, contenido mantenido a mano en el template. Contenido base
traído de `paquetex.papyrus.com.co/cookies` (retroalimentación en vivo
2026-08-02) -- marcador de posición razonable, no una revisión legal.

"cookies" se deja igual (no se traduce, es el mismo préstamo del inglés que
usa el propio español -- a diferencia de `/terms`→`/terminos` y
`/privacy`→`/privacidad`, renombradas en la misma ronda).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/cookies", response_class=HTMLResponse)
def cookies(request: Request):
    return templates.TemplateResponse("cookies/form.html", {"request": request})
