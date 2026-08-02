# -*- coding: utf-8 -*-
"""
Rutas de recuperación de contraseña de staff -- `/staff/olvide-password`,
`/staff/restablecer-password`.

Mensaje GENÉRICO en la solicitud (no revela si el email existe, mismo
principio que `elegible_para_otp`/`verify_credentials`) -- por eso NO hay un
caso de "error" real ahí, solo rate-limit. Envío de correo diferido a
`BackgroundTasks` (mismo patrón que OTP/notificaciones): el request no espera
al proveedor SMTP.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.password_reset_service import confirmar_reset, solicitar_reset

from ..config import public_base_url
from ..db import get_db
from ..password_reset import EmailSender, enviar_en_segundo_plano, get_email_sender
from ..rate_limit import rate_limit
from ..templating import templates

router = APIRouter()

_MENSAJE_RATE_LIMIT = "Demasiados intentos. Espera un momento e inténtalo de nuevo."
_ASUNTO_RESET = "Restablece tu contraseña de PAQUETEX"


def _cuerpo_correo_reset(token: str) -> str:
    enlace = f"{public_base_url()}/staff/restablecer-password?token={token}"
    return (
        "Recibimos una solicitud para restablecer tu contraseña de staff de PAQUETEX.\n\n"
        f"Este enlace es válido por 30 minutos y solo se puede usar una vez:\n{enlace}\n\n"
        "Si no fuiste tú quien lo pidió, ignora este correo -- tu contraseña actual sigue funcionando."
    )


@router.get("/staff/olvide-password", response_class=HTMLResponse)
def olvide_password_form(request: Request):
    return templates.TemplateResponse("auth/olvide_password.html", {"request": request})


@router.post("/staff/olvide-password", response_class=HTMLResponse)
def olvide_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sender: EmailSender = Depends(get_email_sender),
    permitido: bool = Depends(rate_limit("staff_olvide_password", 5, 60)),
    email: str = Form(None),
):
    if not permitido:
        return templates.TemplateResponse(
            "auth/olvide_password.html",
            {"request": request, "error": _MENSAJE_RATE_LIMIT},
            status_code=429,
        )

    resultado = solicitar_reset(db, email)
    if resultado is not None:
        usuario, token = resultado
        background_tasks.add_task(
            enviar_en_segundo_plano,
            sender,
            usuario.email,
            _ASUNTO_RESET,
            _cuerpo_correo_reset(token),
        )
    return templates.TemplateResponse("auth/olvide_password_enviado.html", {"request": request})


@router.get("/staff/restablecer-password", response_class=HTMLResponse)
def restablecer_password_form(request: Request, token: str = None):
    return templates.TemplateResponse(
        "auth/restablecer_password.html", {"request": request, "token": token or ""}
    )


@router.post("/staff/restablecer-password", response_class=HTMLResponse)
def restablecer_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    permitido: bool = Depends(rate_limit("staff_restablecer_password", 10, 60)),
    token: str = Form(None),
    password: str = Form(None),
    password_confirmacion: str = Form(None),
):
    def _error(mensaje: str, status_code: int = 400):
        return templates.TemplateResponse(
            "auth/restablecer_password.html",
            {"request": request, "token": token or "", "error": mensaje},
            status_code=status_code,
        )

    if not permitido:
        return _error(_MENSAJE_RATE_LIMIT, status_code=429)

    if password != password_confirmacion:
        return _error("Las contraseñas no coinciden.")

    try:
        confirmar_reset(db, token or "", password)
    except ValueError as error:
        return _error(str(error))

    return RedirectResponse("/ingresar?restablecida=1", status_code=status.HTTP_303_SEE_OTHER)
