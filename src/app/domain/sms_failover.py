# -*- coding: utf-8 -*-
"""
Failover genérico entre senders SMS (LIWA/Twilio/SNS) — independiente de
proveedor, reutilizable tanto para `NotificationSender` como `OtpSender`
(mismo shape `(destino, mensaje) -> None` una vez construido el mensaje).

`FailoverSmsSender.enviar` reintenta SOLO ante `ErrorConectividadSms` — el
mensaje casi con certeza no llegó a ningún lado (timeout, conexión
rechazada, DNS/TCP como el bloqueo actual de LIWA, 5xx, 401/403). Cualquier
otra excepción — un `RuntimeError` de rechazo explícito del proveedor
(número inválido, saldo insuficiente, `success: false`) — se propaga de
inmediato sin intentar el siguiente sender: reintentar ahí arriesgaría un
envío duplicado, ya que ese proveedor sí recibió y procesó la solicitud.

Cada sender real (`LiwaNotificationSender`, `TwilioNotificationSender`,
`SnsNotificationSender`, y sus contrapartes OTP) es responsable de traducir
sus propias excepciones de conectividad (de `httpx`/`boto3`) a
`ErrorConectividadSms` — este módulo no conoce ni depende de esas librerías.
"""

from typing import Protocol

# Status HTTP que indican un problema de infra/red del lado del proveedor (no
# un rechazo del contenido del mensaje) — compartido por `liwa_sender.py` y
# `twilio_sender.py`, ambos vía `httpx`.
HTTP_STATUS_RECONECTABLES = {401, 403}


class ErrorConectividadSms(Exception):
    """El proveedor no fue alcanzable — reintentable con el siguiente sender
    de la cadena."""


class _SenderSms(Protocol):
    def enviar(self, destino: str, mensaje: str) -> None: ...


class FailoverSmsSender:
    """Envuelve una lista ordenada de senders SMS. `.enviar()` prueba cada
    uno en orden, deteniéndose en el primer éxito."""

    def __init__(self, senders: list[_SenderSms]) -> None:
        if not senders:
            raise ValueError("FailoverSmsSender necesita al menos un sender.")
        self.senders = senders

    def enviar(self, destino: str, mensaje: str) -> None:
        ultimo_error: ErrorConectividadSms | None = None
        for sender in self.senders:
            try:
                sender.enviar(destino, mensaje)
                return
            except ErrorConectividadSms as error:
                ultimo_error = error
                continue
        assert ultimo_error is not None
        raise ultimo_error


def construir_sender(candidatos: list[tuple[bool, _SenderSms]], sender_por_defecto: _SenderSms):
    """Dispatch compartido por `app/web/notifications.py` y `app/web/otp.py`:
    a partir de `candidatos` — pares (¿está configurado?, sender real) YA en
    orden de precedencia — arma 0 configurados → `sender_por_defecto`
    (Console/Dev); 1 → ese sender directo, sin envolver; 2+ → `FailoverSmsSender`
    envolviéndolos en ese mismo orden."""
    proveedores = [sender for esta_configurado, sender in candidatos if esta_configurado]
    if not proveedores:
        return sender_por_defecto
    if len(proveedores) == 1:
        return proveedores[0]
    return FailoverSmsSender(proveedores)
