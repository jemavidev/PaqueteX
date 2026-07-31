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
**LIWA → Twilio → SNS** (mismo mecanismo y misma razón que
`app/web/notifications.py::_sender_base()` —
`.scratch/sms-failover-twilio-sns/spec.md`), vía el dispatch compartido
`sms_failover.construir_sender()`: 0 configurados → `DevOtpSender`
(desarrollo/tests, la suite nunca manda SMS real); 1 configurado → ese
sender directo; 2+ configurados → `FailoverSmsSender`, reintenta con el
siguiente SOLO ante una falla de conectividad.
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
            (liwa_sender.configurado(), LiwaOtpSender()),
            (twilio_sender.configurado(), TwilioOtpSender()),
            (sns_sender.sns_habilitado(), SnsOtpSender()),
        ],
        DevOtpSender(),
    )
