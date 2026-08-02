# -*- coding: utf-8 -*-
"""
Rutas de autenticación de cliente — OTP por teléfono.

`/otp` (pedir OTP) → `/otp/verificar` (confirmar) abre
una **sesión de cliente independiente** de la de staff (`CUSTOMER_SESSION_KEY`).
Mensajes de error GENÉRICOS (no distingue causa del rechazo).

Corrección en vivo 2026-08-02: `/otp/solicitar` responde SIEMPRE igual
(genérico "revisa tu teléfono"), sin importar si el teléfono es elegible
(existe + tiene un Paquete Recibido, ver `otp_service.elegible_para_otp`) —
no revela por contenido ni por tiempo de respuesta si un teléfono específico
está registrado. El envío real se difiere a un `BackgroundTask` (mismo
patrón que las notificaciones de evento de paquete). El caso de éxito ahora
REDIRIGE a `GET /otp/verificar` (antes respondía directo en `/otp/solicitar`,
lo que causaba el aviso de "reenviar formulario" al recargar) -- el caso de
error se queda igual (sin redirigir), consistente con el resto del sitio.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.otp_sender import OtpSender
from app.domain.otp_service import preparar_otp, verify_otp
from app.domain.persona import Persona
from app.domain.telefono import normalizar_telefono

from ..db import get_db
from ..otp import enviar_en_segundo_plano, get_otp_sender
from ..rate_limit import rate_limit
from ..security import CUSTOMER_NOMBRE_SESSION_KEY, CUSTOMER_SESSION_KEY, current_customer
from ..templating import templates

router = APIRouter()

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."


@router.get("/otp", response_class=HTMLResponse)
def customer_login_form(request: Request):
    return templates.TemplateResponse("auth/customer_login.html", {"request": request})


@router.post("/otp/solicitar", response_class=HTMLResponse)
def customer_request_otp(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sender: OtpSender = Depends(get_otp_sender),
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
        resultado = preparar_otp(db, telefono)
    except ValueError:
        return templates.TemplateResponse(
            "auth/customer_login.html",
            {"request": request, "error": "Teléfono inválido."},
            status_code=400,
        )

    # `resultado` es None si el teléfono no es elegible (no existe, o no
    # tiene ningún paquete Recibido) -- se responde EXACTAMENTE igual que si
    # sí lo fuera, para no revelar elegibilidad. Solo se difiere el envío
    # real cuando sí hay algo que enviar.
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    return RedirectResponse(
        f"/otp/verificar?telefono={normalizar_telefono(telefono)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/otp/verificar", response_class=HTMLResponse)
def customer_verify_form(request: Request, telefono: str = None):
    return templates.TemplateResponse(
        "auth/customer_verify.html", {"request": request, "telefono": telefono or ""}
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
    request.session[CUSTOMER_NOMBRE_SESSION_KEY] = persona.nombre
    return RedirectResponse("/mis-datos", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/otp/salir")
def customer_logout(request: Request):
    # pop, no clear: no debe cerrar la sesión de staff si coexiste.
    request.session.pop(CUSTOMER_SESSION_KEY, None)
    request.session.pop(CUSTOMER_NOMBRE_SESSION_KEY, None)
    return RedirectResponse(
        "/otp", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/otp/perfil", response_class=HTMLResponse)
def customer_me(request: Request, persona: Persona = Depends(current_customer)):
    """Ruta protegida de prueba (paralela a `/mi-sesion` de staff)."""
    return templates.TemplateResponse(
        "auth/customer_me.html", {"request": request, "persona": persona}
    )
