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

import html

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
_ASUNTO_RESET = "Restablece tu contraseña de PaqueteX"


def _cuerpo_correo_reset(token: str) -> str:
    enlace = f"{public_base_url()}/staff/restablecer-password?token={token}"
    return (
        "Recibimos una solicitud para restablecer tu contraseña.\n\n"
        f"Este enlace es válido por 30 minutos y solo se puede usar una vez:\n{enlace}\n\n"
        "Si no fuiste tú quien lo pidió, ignora este correo -- tu contraseña actual sigue funcionando."
    )


def _cuerpo_correo_reset_html(nombre: str, token: str) -> str:
    """Versión HTML del mismo correo (pedido del cliente,
    .scratch/pendientes-cliente) -- estilos inline a propósito (no un
    `<style>`/clase CSS): la mayoría de clientes de correo los ignoran o los
    recortan. Logo servido por la propia app (`public_base_url()`, mismo
    dominio que ya resuelve el enlace de abajo)."""
    enlace = f"{public_base_url()}/staff/restablecer-password?token={token}"
    logo = f"{public_base_url()}/static/branding/papyrus-logo.png"
    nombre_seguro = html.escape(nombre)
    return f"""\
<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
          <tr>
            <td align="center" style="padding:32px 32px 16px;">
              <img src="{logo}" alt="PAPYRUS" style="max-width:180px;height:auto;">
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 32px;color:#1a1a1a;font-size:15px;line-height:1.6;">
              <p style="margin:0 0 16px;">Hola {nombre_seguro},</p>
              <p style="margin:0 0 24px;">Recibimos una solicitud para restablecer tu contraseña.</p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
                <tr>
                  <td align="center" style="border-radius:8px;background-color:#1e40af;">
                    <a href="{enlace}" style="display:inline-block;padding:12px 28px;color:#ffffff;font-weight:600;font-size:15px;text-decoration:none;">Restablecer contraseña</a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px;color:#6b7280;font-size:13px;">Si el botón no funciona, copia y pega este enlace en tu navegador:</p>
              <p style="margin:0 0 24px;word-break:break-all;font-size:13px;"><a href="{enlace}" style="color:#1e40af;">{enlace}</a></p>
              <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">Este enlace es válido por 30 minutos y solo se puede usar una vez.</p>
              <p style="margin:0;font-size:13px;color:#6b7280;">Si no fuiste tú quien lo pidió, ignora este correo -- tu contraseña actual sigue funcionando.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


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
            _cuerpo_correo_reset_html(usuario.nombre, token),
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
    def _error(mensaje: str, status_code: int = 400, campos: list[str] = None):
        return templates.TemplateResponse(
            "auth/restablecer_password.html",
            {
                "request": request,
                "token": token or "",
                "error": mensaje,
                "error_password": mensaje if "password" in (campos or []) else None,
                "error_password_confirmacion": mensaje if "password_confirmacion" in (campos or []) else None,
            },
            status_code=status_code,
        )

    if not permitido:
        return _error(_MENSAJE_RATE_LIMIT, status_code=429)

    if password != password_confirmacion:
        return _error("Las contraseñas no coinciden.", campos=["password", "password_confirmacion"])

    try:
        confirmar_reset(db, token or "", password)
    except ValueError as error:
        mensaje = str(error)
        # `confirmar_reset` lanza dos tipos de ValueError indistinguibles por
        # tipo (mismo `except`): token inválido/expirado (mensaje genérico,
        # SIN campo -- el token ni siquiera es un input visible) o
        # contraseña débil (mensaje específico de `_validar_password`, SÍ
        # marca el campo). Se distinguen por el prefijo del mensaje -- los
        # dos mensajes de `_validar_password` empiezan igual, y es un
        # prefijo estable, no vale la pena una excepción dedicada solo para
        # esto.
        campos = ["password"] if mensaje.startswith("La contraseña") else []
        return _error(mensaje, campos=campos)

    return RedirectResponse("/ingresar?restablecida=1", status_code=status.HTTP_303_SEE_OTHER)
