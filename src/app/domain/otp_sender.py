# -*- coding: utf-8 -*-
"""
Puerto de envío de OTP — el dominio no sabe (ni le importa) cómo llega el código
al residente. La implementación real (Twilio/proveedor SMS + override fail-closed
de staging, brief §10) es la rebanada de **notificaciones**; aquí solo el punto
de extensión + una implementación de desarrollo/test que no manda red.

`.enviar()` devuelve la clave de catálogo del proveedor que entregó de
verdad ("AWS_SNS"/"LIWA"/"TWILIO"), o `None` sin envío real que registrar
-- ticket 12 (`.scratch/estadisticas-cobro-dashboard`): mismo contrato que
`NotificationSender` (ver su docstring), para que `app.web.otp.
enviar_en_segundo_plano` sepa cuándo anotar el registro de envíos SMS.
`DevOtpSender` (acá abajo) devuelve `None` a propósito -- nunca manda un
SMS real.
"""

from typing import Protocol


class OtpSender(Protocol):
    def enviar(self, telefono: str, codigo: str) -> str | None: ...


def mensaje_codigo(codigo: str) -> str:
    """El texto exacto que recibe el residente con su código OTP — un solo
    lugar para los tres proveedores reales (Liwa/Twilio/Sns), evita
    duplicar el copy tres veces."""
    return f"Tu código de verificación PAQUETEX es: {codigo}"


class DevOtpSender:
    """Implementación de desarrollo/test: NO envía SMS real.

    Captura el último código enviado por teléfono, para que los tests puedan leer
    lo que "se envió" sin depender de un proveedor externo.
    """

    def __init__(self) -> None:
        self.enviados: dict[str, str] = {}

    def enviar(self, telefono: str, codigo: str) -> None:
        self.enviados[telefono] = codigo
