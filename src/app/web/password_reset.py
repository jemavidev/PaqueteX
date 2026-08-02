# -*- coding: utf-8 -*-
"""
Wiring de envío de correo de recuperación de contraseña de staff (ADR-0004).

Espeja el patrón fail-closed de SMS (`app/web/notifications.py`,
`StagingOverrideSender`/`SMS_OVERRIDE_NUMBER`), con una diferencia: en vez de
NO enviar nada sin config explícita, `EMAIL_OVERRIDE_ADDRESS` tiene un default
en código (`jesus@jemavi.co`) -- decisión explícita del cliente (retroalimentación
2026-08-01/02): un correo de staging sin la variable puesta debe caer a una
dirección conocida y segura, no a "cero envíos" silencioso -- más difícil de
notar que algo quedó mal configurado que con SMS (un SMS que nunca llega se
nota de inmediato al probar; un correo silenciosamente descartado, no tanto).

`get_email_sender` (dependencia FastAPI) arma el sender: `SmtpEmailSender` si
la configuración SMTP está COMPLETA (`smtp_email_sender.configurado()`), si no
`ConsoleEmailSender` (desarrollo/tests, la suite nunca manda correo real). En
`WEB_ENV=staging`, se envuelve con `StagingOverrideEmailSender` -- el correo
real SÍ sale (si hay SMTP configurado), pero SIEMPRE hacia
`EMAIL_OVERRIDE_ADDRESS`, nunca a un staff de prueba real.

`enviar_en_segundo_plano` es la contraparte de correo de `notifications.
enviar_en_segundo_plano`/`otp.enviar_en_segundo_plano` -- pensada para
`BackgroundTasks.add_task`, best-effort (traga cualquier excepción): el
request que pide el reset no debe esperar al proveedor SMTP.
"""

import os

from app.domain import smtp_email_sender
from app.domain.email_sender import ConsoleEmailSender, EmailSender
from app.domain.smtp_email_sender import SmtpEmailSender

_EMAIL_OVERRIDE_DEFAULT = "jesus@jemavi.co"


class StagingOverrideEmailSender:
    """Envuelve `wrapped` y redirige TODO envío a `override_address`.

    A diferencia de `StagingOverrideSender` (SMS), `override_address` SIEMPRE
    tiene un valor -- si `EMAIL_OVERRIDE_ADDRESS` no está puesta o viene
    vacía, cae a `_EMAIL_OVERRIDE_DEFAULT` en vez de no enviar nada (ver
    docstring del módulo)."""

    def __init__(self, wrapped: EmailSender, override_address) -> None:
        self._wrapped = wrapped
        self._override_address = (
            override_address or ""
        ).strip() or _EMAIL_OVERRIDE_DEFAULT

    def enviar(self, destino: str, asunto: str, cuerpo: str) -> None:
        self._wrapped.enviar(self._override_address, asunto, cuerpo)


def _sender_base() -> EmailSender:
    if smtp_email_sender.configurado():
        return SmtpEmailSender()
    return ConsoleEmailSender()


def get_email_sender() -> EmailSender:
    """El `EmailSender` según `WEB_ENV`.

    - ``staging``: `StagingOverrideEmailSender` sobre el sender base -- con
      SMTP real conectado, el correo SÍ sale, pero siempre hacia
      `EMAIL_OVERRIDE_ADDRESS` (o el default), nunca a un staff de prueba.
    - cualquier otro valor (``development``, tests, sin definir): el sender
      base directo, sin override.
    """
    wrapped = _sender_base()
    if os.environ.get("WEB_ENV") == "staging":
        return StagingOverrideEmailSender(wrapped, os.environ.get("EMAIL_OVERRIDE_ADDRESS"))
    return wrapped


def enviar_en_segundo_plano(sender: EmailSender, destino: str, asunto: str, cuerpo: str) -> None:
    try:
        sender.enviar(destino, asunto, cuerpo)
    except Exception:
        pass
