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
`liwa_sender.configurado()`), en orden de precedencia **AWS SNS → LIWA →
Twilio** (`.scratch/pendientes-cliente`, pedido del cliente 2026-08-06:
problemas puntuales con Twilio -- AWS pasa al frente mientras se investiga,
LIWA/Twilio se quedan como respaldo si AWS llega a fallar por conectividad.
Antes de este cambio el orden era LIWA → Twilio → SNS,
`.scratch/sms-failover-twilio-sns/spec.md`), vía el dispatch compartido
`sms_failover.construir_sender()`:

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
            (sns_sender.sns_habilitado(), SnsNotificationSender()),
            (liwa_sender.configurado(), LiwaNotificationSender()),
            (twilio_sender.configurado(), TwilioNotificationSender()),
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


def enviar_en_segundo_plano(sender: NotificationSender, destino: str, mensaje: str) -> None:
    """Ejecuta `sender.enviar` best-effort — pensado para pasarse a
    `BackgroundTasks.add_task` (corrección en vivo 2026-08-02: mientras el
    proveedor primero en la cadena de failover esté inalcanzable, cada envío
    espera su timeout completo antes de pasar al siguiente; diferir el envío
    real fuera del request es lo que evita que esa espera bloquee el
    response).

    Traga cualquier excepción (igual que `notificacion_service.
    notificar_evento`, mismo espíritu best-effort) — un `BackgroundTask` que
    lanza solo deja una traza ruidosa en los logs del servidor, nunca llega
    a afectar al usuario que ya recibió su response; no hace falta que
    también ensucie los logs para un modo de fallo ya esperado (proveedor
    caído, failover en curso)."""
    try:
        sender.enviar(destino, mensaje)
    except Exception:
        pass
