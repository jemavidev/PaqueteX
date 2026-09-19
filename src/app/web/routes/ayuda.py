# -*- coding: utf-8 -*-
"""
Ruta `/ayuda` — página estática de preguntas frecuentes (Grupo 10, Ronda 2).

Pública, sin sesión. Contenido tomado de la sección "Preguntas frecuentes" de
`docs/refactoring/GUIA_USUARIO_FINAL.md` — mantenida a mano en el template,
no generada en runtime desde el `.md` (evita acoplar la app a un archivo de
documentación que vive fuera del árbol servido).

Datos de contacto/horarios y de la empresa operadora se leen en vivo desde
`ConfiguracionConjunto`/`ConfiguracionEmpresa` (grilling 2026-09-18, issue
350, `.scratch/pendientes-cliente/spec.md`) -- antes vivían fijos en el
template.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.configuracion_conjunto_service import obtener_datos_operativos
from app.domain.configuracion_empresa_service import obtener_datos_empresa

from ..config import whatsapp_soporte_numero
from ..db import get_db
from ..templating import templates

router = APIRouter()


@router.get("/ayuda", response_class=HTMLResponse)
def ayuda(request: Request, db: Session = Depends(get_db)):
    operativos = obtener_datos_operativos(db)
    return templates.TemplateResponse(
        "ayuda/form.html",
        {
            "request": request,
            "operativos": operativos,
            "empresa": obtener_datos_empresa(db),
            # `numero_whatsapp` ya lo resuelve `base.html` (global de
            # Jinja) para el footer -- acá se sobrescribe con el mismo
            # criterio "BD gana, si no el de siempre" para que el botón
            # "Contactar" de esta página y el del footer nunca diverjan.
            "numero_whatsapp": operativos.numero_whatsapp or (whatsapp_soporte_numero() or None),
        },
    )
