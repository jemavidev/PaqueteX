# -*- coding: utf-8 -*-
"""
Ruta `/privacidad` — política de tratamiento de datos personales.

Pública, sin sesión. Mismo patrón que `/terminos`/`/ayuda`: página estática,
contenido mantenido a mano en el template. Reescrita en el grilling
2026-09-18 (Ley 1581 de 2012 + Decreto 1377 de 2013, Colombia) -- ver
`docs/manual-usuario/README.md`.

Ruta renombrada de `/privacy` a `/privacidad` (retroalimentación en vivo
2026-08-02: el resto del rebuild usa rutas en español). El archivo/carpeta
de la plantilla (`privacy/form.html`) se deja igual, es un detalle interno
sin URL propia.

Datos de la empresa operadora (responsable del tratamiento) se leen en vivo
desde `ConfiguracionEmpresa` (grilling 2026-09-18, issue 350) -- antes
vivían fijos en el template, con el NIT como placeholder "(por confirmar)".
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.configuracion_empresa_service import obtener_datos_empresa

from ..db import get_db
from ..templating import templates

router = APIRouter()


@router.get("/privacidad", response_class=HTMLResponse)
def privacy(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "privacy/form.html", {"request": request, "empresa": obtener_datos_empresa(db)}
    )
