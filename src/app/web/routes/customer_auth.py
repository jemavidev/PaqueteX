# -*- coding: utf-8 -*-
"""
Rutas de autenticación de cliente — OTP por teléfono.

`/otp` (pedir OTP) → `/otp/verificar` (confirmar) abre
una **sesión de cliente independiente** de la de staff (`CUSTOMER_SESSION_KEY`).
Mensajes de error GENÉRICOS (no distingue causa del rechazo).
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.otp_sender import DevOtpSender
from app.domain.otp_service import request_otp, verify_otp
from app.domain.persona import Persona

from ..db import get_db
from ..rate_limit import rate_limit
from ..security import CUSTOMER_SESSION_KEY, current_customer
from ..templating import templates

router = APIRouter()

# Envío real de SMS = rebanada de notificaciones (brief §10, override fail-closed
# de staging). Aquí el puerto ya existe; esta es la implementación de desarrollo.
_sender = DevOtpSender()

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."


@router.get("/otp", response_class=HTMLResponse)
def customer_login_form(request: Request):
    return templates.TemplateResponse("auth/customer_login.html", {"request": request})


@router.post("/otp/solicitar", response_class=HTMLResponse)
def customer_request_otp(
    request: Request,
    db: Session = Depends(get_db),
    permitido: bool = Depends(rate_limit("customer_request_otp", 5, 60)),
    telefono: str = Form(None),
):
    if not permitido:
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": _MENSAJE_RATE_LIMIT},
            status_code=429,
        )

    if not (telefono or "").strip():
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": "El teléfono es obligatorio."},
            status_code=400,
        )

    try:
        request_otp(db, telefono, _sender)
    except ValueError:
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": "Teléfono inválido."},
            status_code=400,
        )

    return templates.TemplateResponse(
        "auth/customer_verify.html", {"request": request, "telefono": telefono}
    )


@router.post("/otp/verificar")
def customer_verify_otp(
    request: Request,
    db: Session = Depends(get_db),
    telefono: str = Form(None),
    codigo: str = Form(None),
):
    def _error():
        return templates.TemplateResponse(
            "auth/customer_verify.html",
            {
                "request": request,
                "telefono": telefono or "",
                "error": "Código inválido o expirado.",
            },
            status_code=400,
        )

    if not (telefono or "").strip() or not (codigo or "").strip():
        return _error()

    try:
        persona = verify_otp(db, telefono, codigo)
    except ValueError:
        return _error()

    request.session[CUSTOMER_SESSION_KEY] = str(persona.id)
    return RedirectResponse(
        "/otp/perfil", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/otp/salir")
def customer_logout(request: Request):
    # pop, no clear: no debe cerrar la sesión de staff si coexiste.
    request.session.pop(CUSTOMER_SESSION_KEY, None)
    return RedirectResponse(
        "/otp", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/otp/perfil", response_class=HTMLResponse)
def customer_me(request: Request, persona: Persona = Depends(current_customer)):
    """Ruta protegida de prueba (paralela a `/mi-sesion` de staff)."""
    return templates.TemplateResponse(
        "auth/customer_me.html", {"request": request, "persona": persona}
    )
