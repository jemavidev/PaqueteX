# -*- coding: utf-8 -*-
"""
Wiring de notificaciones de la capa web (ADR-0004).

`StagingOverrideSender` es la salvaguarda **fail-closed** (brief §10,
`CONTEXT.md` invariante 6): en `WEB_ENV=staging`, TODO mensaje se redirige a
`SMS_OVERRIDE_NUMBER`; si esa variable falta, **no se envía nada** — nunca cae al
envío real. `get_notification_sender` (dependencia FastAPI) elige el sender
según el entorno; no se cachea (se lee el entorno en cada llamada, igual que
`secret_key()`/`database_url()` — barato de construir, nada que poolear).

El sender BASE (antes de envolver con el override de staging) se arma en
`_sender_base()` a partir de los proveedores SMS reales cuya configuración
esté COMPLETA (`configurado()` de cada módulo, no solo la presencia de una
variable — un proveedor a medias no debe entrar a la cadena, ver nota en
`liwa_sender.configurado()`), en orden de precedencia **LIWA → Twilio →
SNS** (`.scratch/sms-failover-twilio-sns/spec.md`), vía el dispatch
compartido `sms_failover.construir_sender()`:

  - 0 configurados → `ConsoleNotificationSender` (desarrollo/tests — así la
    suite NUNCA manda SMS real, ya que el entorno de test no define esas
    variables).
  - 1 configurado → ese sender directo, sin envolver (mismo comportamiento
    de siempre cuando solo hay LIWA).
  - 2+ configurados → `FailoverSmsSender`, que reintenta con el siguiente
    proveedor SOLO ante una falla de conectividad (`ErrorConectividadSms`) —
    nunca ante un rechazo explícito del proveedor.

En staging, `StagingOverrideSender` sigue protegiendo sin importar cuántos
proveedores estén detrás: el SMS de verdad sale (por el que gane la cadena
de failover), pero SIEMPRE hacia `SMS_OVERRIDE_NUMBER`, nunca a un residente
real.
"""

import os

from app.domain import liwa_sender, sns_sender, twilio_sender
from app.domain.liwa_sender import LiwaNotificationSender
from app.domain.notification_sender import ConsoleNotificationSender, NotificationSender
from app.domain.sms_failover import construir_sender
from app.domain.sns_sender import SnsNotificationSender
from app.domain.twilio_sender import TwilioNotificationSender


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


def _sender_base() -> NotificationSender:
    return construir_sender(
        [
            (liwa_sender.configurado(), LiwaNotificationSender()),
            (twilio_sender.configurado(), TwilioNotificationSender()),
            (sns_sender.sns_habilitado(), SnsNotificationSender()),
        ],
        ConsoleNotificationSender(),
    )


def get_notification_sender() -> NotificationSender:
    """El `NotificationSender` según `WEB_ENV` y si hay LIWA configurado.

    - ``staging``: `StagingOverrideSender` sobre el sender base — el wrapper
      es la pieza de seguridad; con LIWA real conectado, el SMS SÍ sale, pero
      siempre hacia `SMS_OVERRIDE_NUMBER`, nunca a un residente real.
    - cualquier otro valor (``development``, tests, sin definir): el sender
      base directo, sin override.
    """
    wrapped = _sender_base()
    if os.environ.get("WEB_ENV") == "staging":
        return StagingOverrideSender(wrapped, os.environ.get("SMS_OVERRIDE_NUMBER"))
    return wrapped
