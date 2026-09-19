# -*- coding: utf-8 -*-
"""
Ruta `/como-funciona` — explicación en lenguaje simple (no legal) del flujo
completo de PAQUETEX para un residente (2026-09-18, grilling con Jesús sobre
las páginas públicas informativas). Pública, sin sesión -- mismo patrón que
`/ayuda`. Enlazada desde dentro de `/ayuda` y desde la confirmación de
`/anunciar`; a propósito NO se agrega al header/footer global (nav ya muy
afinada en rondas recientes, issues 339-349 de `.scratch/pendientes-cliente/
spec.md`).

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


@router.get("/como-funciona", response_class=HTMLResponse)
def como_funciona(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "como_funciona/form.html", {"request": request, "empresa": obtener_datos_empresa(db)}
    )
