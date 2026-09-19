# -*- coding: utf-8 -*-
"""
Ruta `/cookies` — política de cookies.

Pública, sin sesión. Mismo patrón que `/terminos`/`/privacidad`/`/ayuda`:
página estática, contenido mantenido a mano en el template. Reescrita en el
grilling 2026-09-18 -- ver `docs/manual-usuario/README.md`.

"cookies" se deja igual (no se traduce, es el mismo préstamo del inglés que
usa el propio español -- a diferencia de `/terms`→`/terminos` y
`/privacy`→`/privacidad`, renombradas en otra ronda).

Datos de la empresa operadora se leen en vivo desde `ConfiguracionEmpresa`
(grilling 2026-09-18, issue 350) -- antes vivían fijos en el template.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.configuracion_empresa_service import obtener_datos_empresa

from ..db import get_db
from ..templating import templates

router = APIRouter()


@router.get("/cookies", response_class=HTMLResponse)
def cookies(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "cookies/form.html", {"request": request, "empresa": obtener_datos_empresa(db)}
    )
