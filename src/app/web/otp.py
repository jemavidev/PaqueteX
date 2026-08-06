# -*- coding: utf-8 -*-
"""
Wiring de envío de OTP de la capa web (ADR-0004).

A diferencia de las notificaciones de evento, el OTP **no** pasa por
`StagingOverrideSender` — el código debe llegar de verdad a quien lo pidió
(es autodirigido: cada quien solo puede solicitar el suyo), no hay riesgo de
"spamear" a un tercero como sí lo hay con las notificaciones de paquete.

`get_otp_sender` (dependencia FastAPI) arma el sender a partir de los
proveedores SMS reales cuya configuración esté COMPLETA (`configurado()` de
cada módulo, no solo la presencia de una variable), en orden de precedencia
**AWS SNS → LIWA → Twilio** (mismo mecanismo y misma razón que
`app/web/notifications.py::_sender_base()` — `.scratch/pendientes-cliente`,
pedido del cliente 2026-08-06: problemas puntuales con Twilio, AWS pasa al
frente mientras se investiga. Antes de este cambio el orden era LIWA →
Twilio → SNS, `.scratch/sms-failover-twilio-sns/spec.md`), vía el dispatch
compartido `sms_failover.construir_sender()`: 0 configurados →
`DevOtpSender` (desarrollo/tests, la suite nunca manda SMS real); 1
configurado → ese sender directo; 2+ configurados → `FailoverSmsSender`,
reintenta con el siguiente SOLO ante una falla de conectividad.

`enviar_en_segundo_plano` (corrección en vivo 2026-08-02) es la contraparte
OTP de `notifications.enviar_en_segundo_plano` -- pensada para
`BackgroundTasks.add_task`, best-effort (traga cualquier excepción). Antes
el envío de OTP era síncrono a propósito ("el cliente necesita saber YA si
no le va a llegar el código"); se acepta ahora el mismo trade-off que las
notificaciones de evento (retroalimentación 2026-08-02: la demora de 5-10s
en "pedir el código" pesaba más que la garantía de error visible).
"""

from app.domain import liwa_sender, sns_sender, twilio_sender
from app.domain.liwa_sender import LiwaOtpSender
from app.domain.otp_sender import DevOtpSender, OtpSender
from app.domain.sms_failover import construir_sender
from app.domain.sns_sender import SnsOtpSender
from app.domain.twilio_sender import TwilioOtpSender


def get_otp_sender() -> OtpSender:
    return construir_sender(
        [
            (sns_sender.sns_habilitado(), SnsOtpSender()),
            (liwa_sender.configurado(), LiwaOtpSender()),
            (twilio_sender.configurado(), TwilioOtpSender()),
        ],
        DevOtpSender(),
    )


def enviar_en_segundo_plano(sender: OtpSender, telefono: str, codigo: str) -> None:
    try:
        sender.enviar(telefono, codigo)
    except Exception:
        pass
