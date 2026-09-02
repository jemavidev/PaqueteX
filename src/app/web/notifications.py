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
`liwa_sender.configurado()`), combinado con el habilitado/orden de
`ProveedorConfig` (issue 02, `.scratch/administracion-proveedores/spec.md`
-- `proveedor_config_service.armar_candidatos()`): un proveedor entra a la
cadena SOLO si las dos condiciones son ciertas a la vez. El orden YA NO es
una constante fija en código -- se lee de la BD, editable desde
`/administracion/proveedores` sin redeploy. El orden por defecto (sembrado
por la migración 0037, mismo que tenía la constante vieja) sigue siendo
**AWS SNS → LIWA → Twilio** (`.scratch/pendientes-cliente`, pedido del
cliente 2026-08-06: problemas puntuales con Twilio -- AWS pasa al frente
mientras se investiga, LIWA/Twilio se quedan como respaldo si AWS llega a
fallar por conectividad. Antes de ese cambio el orden era LIWA → Twilio →
SNS, `.scratch/sms-failover-twilio-sns/spec.md`), vía el dispatch compartido
`sms_failover.construir_sender()` (que en sí no cambió -- sigue recibiendo
exactamente `[(bool, sender), ...]`):

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

import logging
import os

from fastapi import Depends
from sqlalchemy.orm import Session

from app.domain import liwa_sender, sns_sender, twilio_sender
from app.domain.liwa_sender import LiwaNotificationSender
from app.domain.notification_sender import ConsoleNotificationSender, NotificationSender
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedor_config_service import armar_candidatos
from app.domain.sms_failover import construir_sender
from app.domain.sns_sender import SnsNotificationSender
from app.domain.twilio_sender import TwilioNotificationSender

from .db import get_db

logger = logging.getLogger(__name__)


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


def _sender_base(db: Session) -> NotificationSender:
    candidatos = armar_candidatos(
        db,
        CanalNotificacion.SMS,
        [
            ("AWS_SNS", sns_sender.sns_habilitado(), SnsNotificationSender()),
            ("LIWA", liwa_sender.configurado(), LiwaNotificationSender()),
            ("TWILIO", twilio_sender.configurado(), TwilioNotificationSender()),
        ],
    )
    return construir_sender(candidatos, ConsoleNotificationSender())


def sms_configurado() -> bool:
    """¿Hay al menos un proveedor SMS real configurado? Mismos tres checks
    que arma `_sender_base()` (SNS/LIWA/Twilio) -- fuente única para
    cualquier caller que solo necesite el booleano, sin construir el sender
    (.scratch/notificaciones-enviar-prueba, ticket 02: `admin.py` lo usa
    para decidir si el botón "Enviar prueba" de la pestaña SMS aparece
    habilitado, sin duplicar la lista de proveedores)."""
    return sns_sender.sns_habilitado() or liwa_sender.configurado() or twilio_sender.configurado()


def get_notification_sender(db: Session = Depends(get_db)) -> NotificationSender:
    """El `NotificationSender` según `WEB_ENV` y si hay LIWA configurado.

    - ``staging``: `StagingOverrideSender` sobre el sender base — el wrapper
      es la pieza de seguridad; con LIWA real conectado, el SMS SÍ sale, pero
      siempre hacia `SMS_OVERRIDE_NUMBER`, nunca a un residente real.
    - cualquier otro valor (``development``, tests, sin definir): el sender
      base directo, sin override.

    `db` (issue 02, `.scratch/administracion-proveedores/spec.md`): el orden
    de precedencia y el habilitado/deshabilitado de cada proveedor ya no son
    una constante fija -- se leen de `ProveedorConfig` vía
    `proveedor_config_service.armar_candidatos()` en cada llamada, igual que
    el resto de esta función lee el entorno fresco cada vez."""
    wrapped = _sender_base(db)
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

    No deja que la excepción tumbe el `BackgroundTask` (igual que
    `notificacion_service.notificar_evento`, mismo espíritu best-effort) --
    pero SÍ se registra con `logger.exception` (2026-09-01, corrección en
    vivo): `FailoverSmsSender` (si hay 2+ proveedores) solo deja propagar
    una excepción cuando TODOS fallaron (ver `sms_failover.py`), así que
    cualquier excepción que llegue hasta acá es un envío que de verdad no
    salió -- nunca un proveedor caído a mitad de failover como decía este
    comentario antes (ESE caso ya se resuelve solo, sin excepción, dentro
    del propio `FailoverSmsSender`). Un fallo total y silencioso de los 3
    proveedores tomó horas de investigación manual por AWS CLI porque acá
    no quedaba ningún rastro."""
    try:
        sender.enviar(destino, mensaje)
    except Exception:
        logger.exception("Envío de notificación a %s falló en todos los proveedores.", destino)
