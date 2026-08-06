# -*- coding: utf-8 -*-
"""
Ruta `/entrar` — punto de entrada único de login (Grupo 10, Ronda 2).

Unifica los botones "Iniciar sesión" (residente) y "Staff" del header en uno
solo: esta pantalla, con un selector Cliente/Staff que cambia qué formulario
se ve. NO reemplaza `/otp` ni `/ingresar` — ambos siguen siendo los targets
reales de cada sub-formulario (mismos nombres de campo, mismo POST), esta es
solo la puerta visual que los envuelve. Selector 100% client-side (CSS, sin
JS de más) para que no dependa de nada nuevo.

Pedido del cliente (versión móvil, `.scratch/pendientes-cliente`): si ya hay
sesión activa, `/entrar` no debe mostrar el formulario de nuevo -- redirige
directo al área por defecto de esa sesión. Mismo destino y mismo chequeo
LIVIANO (presencia en `request.session`, sin verificar contra la BD) que ya
usa `base.html` para el link de marca del header (`destino_marca`) -- staff
a `/paquetes`, cliente a `/mis-datos`. Si coexisten ambas sesiones (Grupo 10,
Ronda 2: staff y cliente pueden estar logueados a la vez), staff gana -- es
el mismo criterio de prioridad que ya usa `destino_marca`.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..security import CUSTOMER_SESSION_KEY, SESSION_KEY
from ..templating import templates

router = APIRouter()


@router.get("/entrar", response_class=HTMLResponse)
def entrar_form(request: Request):
    if request.session.get(SESSION_KEY):
        return RedirectResponse("/paquetes", status_code=303)
    if request.session.get(CUSTOMER_SESSION_KEY):
        return RedirectResponse("/mis-datos", status_code=303)
    return templates.TemplateResponse("auth/entrar.html", {"request": request})
