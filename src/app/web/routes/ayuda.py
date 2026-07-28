# -*- coding: utf-8 -*-
"""
Ruta `/ayuda` — página estática de preguntas frecuentes (Grupo 10, Ronda 2).

Pública, sin sesión. Contenido tomado de la sección "Preguntas frecuentes" de
`docs/refactoring/GUIA_USUARIO_FINAL.md` — mantenida a mano en el template,
no generada en runtime desde el `.md` (evita acoplar la app a un archivo de
documentación que vive fuera del árbol servido).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/ayuda", response_class=HTMLResponse)
def ayuda(request: Request):
    return templates.TemplateResponse("ayuda/form.html", {"request": request})
