# -*- coding: utf-8 -*-
"""
Conector real de SMS vía Twilio — implementa tanto `NotificationSender` como
`OtpSender` (mismo patrón que `liwa_sender.py`: llamadas REST directas por
`httpx`, sin el SDK oficial de Twilio, consistente con el resto del
proyecto — ver `.scratch/sms-failover-twilio-sns/spec.md`).

Envío: ``POST
https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json``,
Basic Auth ``(AccountSid, AuthToken)``, body form-encoded ``{"To": destino,
"MessagingServiceSid": TWILIO_MESSAGING_SERVICE_SID, "Body": mensaje}``.

Variables de entorno requeridas: ``TWILIO_ACCOUNT_SID``,
``TWILIO_AUTH_TOKEN``, ``TWILIO_MESSAGING_SERVICE_SID``. Si faltan,
`_config()` lanza `RuntimeError` — la selección de esta implementación vs.
LIWA/SNS/consola vive en la capa web (`app/web/notifications.py`,
`app/web/otp.py`), no aquí.

El `destino` llega ya en E.164 con ``"+"`` (forma canónica de
`telefono.py`) y se pasa tal cual — a diferencia de LIWA, que le quita el
``"+"``.

Se usa un Messaging Service (no un número fijo) — la cuenta de Twilio en
uso no opera con `TWILIO_FROM_NUMBER`.
"""

import os

import httpx

from app.domain.otp_sender import mensaje_codigo
from app.domain.sms_failover import HTTP_STATUS_RECONECTABLES, ErrorConectividadSms

_TIMEOUT_SEGUNDOS = 15.0


def _config() -> tuple[str, str, str]:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    messaging_service_sid = os.environ.get("TWILIO_MESSAGING_SERVICE_SID")
    if not (account_sid and auth_token and messaging_service_sid):
        raise RuntimeError(
            "Configuración de Twilio incompleta — se requieren TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN y TWILIO_MESSAGING_SERVICE_SID."
        )
    return account_sid, auth_token, messaging_service_sid


def configurado() -> bool:
    """¿Están las TRES variables de Twilio presentes? Usado por la capa web
    para decidir si este proveedor entra en la cadena de precedencia — mirar
    solo `TWILIO_ACCOUNT_SID` dejaría entrar un proveedor a medio configurar
    (ver la misma nota en `liwa_sender.configurado()`)."""
    try:
        _config()
        return True
    except RuntimeError:
        return False


def _enviar_sms(destino: str, mensaje: str) -> None:
    account_sid, auth_token, messaging_service_sid = _config()
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    payload = {"To": destino, "MessagingServiceSid": messaging_service_sid, "Body": mensaje}

    try:
        respuesta = httpx.post(
            url, data=payload, auth=(account_sid, auth_token), timeout=_TIMEOUT_SEGUNDOS
        )
        respuesta.raise_for_status()
    except httpx.TransportError as error:
        raise ErrorConectividadSms(f"Twilio no fue alcanzable: {error}") from error
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status >= 500 or status in HTTP_STATUS_RECONECTABLES:
            raise ErrorConectividadSms(f"Twilio respondió {status}") from error
        raise RuntimeError(f"Twilio rechazó el envío ({status}): {error.response.text}") from error


class TwilioNotificationSender:
    """Implementación real de `NotificationSender` vía Twilio."""

    def enviar(self, destino: str, mensaje: str) -> None:
        _enviar_sms(destino, mensaje)


class TwilioOtpSender:
    """Implementación real de `OtpSender` vía Twilio."""

    def enviar(self, telefono: str, codigo: str) -> None:
        _enviar_sms(telefono, mensaje_codigo(codigo))
