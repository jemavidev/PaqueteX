# -*- coding: utf-8 -*-
"""
Ruta `/entrar` — punto de entrada único de login (Grupo 10, Ronda 2).

Unifica los botones "Iniciar sesión" (residente) y "Staff" del header en uno
solo: esta pantalla, con un selector Cliente/Staff que cambia qué formulario
se ve. NO reemplaza `/otp` ni `/ingresar` — ambos siguen siendo los targets
reales de cada sub-formulario (mismos nombres de campo, mismo POST), esta es
solo la puerta visual que los envuelve. Selector 100% client-side (CSS, sin
JS de más) para que no dependa de nada nuevo.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/entrar", response_class=HTMLResponse)
def entrar_form(request: Request):
    return templates.TemplateResponse("auth/entrar.html", {"request": request})
