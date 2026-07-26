# -*- coding: utf-8 -*-
"""
Wiring de envío de OTP de la capa web (ADR-0004).

A diferencia de las notificaciones de evento, el OTP **no** pasa por
`StagingOverrideSender` — el código debe llegar de verdad a quien lo pidió
(es autodirigido: cada quien solo puede solicitar el suyo), no hay riesgo de
"spamear" a un tercero como sí lo hay con las notificaciones de paquete.

`get_otp_sender` (dependencia FastAPI) usa `LiwaOtpSender` si hay
credenciales de LIWA configuradas (`LIWA_API_KEY`), o `DevOtpSender` si no
(desarrollo/tests — la suite nunca manda SMS real).
"""

import os

from app.domain.liwa_sender import LiwaOtpSender
from app.domain.otp_sender import DevOtpSender, OtpSender


def get_otp_sender() -> OtpSender:
    if os.environ.get("LIWA_API_KEY"):
        return LiwaOtpSender()
    return DevOtpSender()
