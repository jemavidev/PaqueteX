# -*- coding: utf-8 -*-
"""
Wiring de notificaciones de la capa web (ADR-0004).

`StagingOverrideSender` es la salvaguarda **fail-closed** (brief §10,
`CONTEXT.md` invariante 6): en `WEB_ENV=staging`, TODO mensaje se redirige a
`SMS_OVERRIDE_NUMBER`; si esa variable falta, **no se envía nada** — nunca cae al
envío real. `get_notification_sender` (dependencia FastAPI) elige el sender
según el entorno; no se cachea (se lee el entorno en cada llamada, igual que
`secret_key()`/`database_url()` — barato de construir, nada que poolear).
"""

import os

from app.domain.notification_sender import ConsoleNotificationSender, NotificationSender


class StagingOverrideSender:
    """Envuelve `wrapped` y redirige TODO envío a `override_number`.

    Si `override_number` es ``None``/vacío, `.enviar()` **no hace nada**
    (fail-closed): nunca delega al `wrapped` sin un destino de prueba explícito.
    """

    def __init__(self, wrapped: NotificationSender, override_number) -> None:
        self._wrapped = wrapped
        self._override_number = (override_number or "").strip() or None

    def enviar(self, destino: str, mensaje: str) -> None:
        if not self._override_number:
            return  # fail-closed: sin config de override, cero envíos.
        self._wrapped.enviar(self._override_number, mensaje)


def get_notification_sender() -> NotificationSender:
    """El `NotificationSender` según `WEB_ENV`.

    - ``staging``: `StagingOverrideSender` sobre un `ConsoleNotificationSender`
      (el wrapper es la pieza de seguridad; conectar un proveedor real detrás es
      otra rebanada, cambio de una sola implementación).
    - cualquier otro valor (``development``, tests, sin definir):
      `ConsoleNotificationSender` directo, sin override — nada sale de la
      máquina de todos modos.
    """
    if os.environ.get("WEB_ENV") == "staging":
        return StagingOverrideSender(
            ConsoleNotificationSender(), os.environ.get("SMS_OVERRIDE_NUMBER")
        )
    return ConsoleNotificationSender()
