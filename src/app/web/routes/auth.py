# -*- coding: utf-8 -*-
"""
Rutas de autenticación de staff — `/ingresar`, `/salir`, `/mi-sesion`.

Login con email + contraseña (server-rendered). La sesión guarda el `usuario_id`;
`current_staff` la lee para producir el actor de las acciones. Mensajes de error
GENÉRICOS (no revelan si el email existe).

También vive aquí `/salir-todo` (Grupo 10, Ronda 2): el header pasó de un
botón de logout por sesión a uno único que cierra AMBAS sesiones
(cliente+staff) si están coexistiendo — mecanismo nuevo, pero las sesiones
siguen siendo cookies/keys independientes por dentro (`/salir` y
`/otp/salir` individuales se conservan intactos, sin cambios).
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.staff_service import verify_credentials
from app.domain.usuario import Usuario

from ..db import get_db
from ..rate_limit import rate_limit
from ..security import (
    CUSTOMER_NOMBRE_SESSION_KEY,
    CUSTOMER_SESSION_KEY,
    NOMBRE_SESSION_KEY,
    ROLE_SESSION_KEY,
    SESSION_KEY,
    current_staff,
)
from ..templating import templates

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."

router = APIRouter()


@router.get("/ingresar", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/ingresar")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    permitido: bool = Depends(rate_limit("auth_login", 10, 60)),
    email: str = Form(None),
    password: str = Form(None),
):
    def _error():
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Email o contraseña incorrectos.",
                "email": email or "",
            },
            status_code=400,
        )

    if not permitido:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": _MENSAJE_RATE_LIMIT, "email": email or ""},
            status_code=429,
        )

    if not (email or "").strip() or not (password or ""):
        return _error()

    usuario = verify_credentials(db, email, password)
    if usuario is None:
        return _error()

    request.session[SESSION_KEY] = str(usuario.id)
    # Dato derivado para el menú (DEC-09) -- require_admin sigue siendo la
    # única puerta real de las rutas de administración.
    request.session[ROLE_SESSION_KEY] = usuario.rol.value
    request.session[NOMBRE_SESSION_KEY] = usuario.nombre
    # Corrección en vivo 2026-08-02: antes iba a /mi-sesion (ruta de prueba);
    # /paquetes es lo que un staff realmente quiere ver al entrar.
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/salir")
def logout(request: Request):
    # pop, no clear: la sesión de cliente (persona_id) es independiente y no debe
    # cerrarse al cerrar la de staff.
    request.session.pop(SESSION_KEY, None)
    request.session.pop(ROLE_SESSION_KEY, None)
    request.session.pop(NOMBRE_SESSION_KEY, None)
    return RedirectResponse("/ingresar", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/salir-todo")
def logout_todo(request: Request):
    """Cierra la sesión de STAFF y de CLIENTE a la vez, si hay alguna (o
    ambas) abiertas — el único botón de logout que el header muestra ahora
    (Grupo 10, Ronda 2). `pop` de las claves, nunca `request.session.
    clear()`, para no arrastrar por accidente alguna clave futura ajena a
    estas dos sesiones."""
    request.session.pop(SESSION_KEY, None)
    request.session.pop(ROLE_SESSION_KEY, None)
    request.session.pop(NOMBRE_SESSION_KEY, None)
    request.session.pop(CUSTOMER_SESSION_KEY, None)
    request.session.pop(CUSTOMER_NOMBRE_SESSION_KEY, None)
    return RedirectResponse("/anunciar", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/mi-sesion", response_class=HTMLResponse)
def me(request: Request, usuario: Usuario = Depends(current_staff)):
    return templates.TemplateResponse(
        "auth/me.html", {"request": request, "usuario": usuario}
    )
