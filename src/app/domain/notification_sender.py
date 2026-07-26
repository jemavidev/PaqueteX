# -*- coding: utf-8 -*-
"""
Puerto de envío de notificaciones de evento del Paquete — el dominio no sabe (ni
le importa) cómo llega el mensaje al residente. Deliberadamente SEPARADO de
`OtpSender` (misma forma, semántica distinta: un código temporal no es un mensaje
de evento templado; fusionarlos ahora sería una abstracción prematura — YAGNI).

La integración real con un proveedor SMS (Twilio u otro) es otra rebanada; aquí
solo el punto de extensión + una implementación de desarrollo/test que no manda
red (mismo espíritu que `DevOtpSender`).
"""

from typing import Protocol


class NotificationSender(Protocol):
    def enviar(self, destino: str, mensaje: str) -> None: ...


class ConsoleNotificationSender:
    """Implementación de desarrollo/test: NO envía SMS real.

    Captura cada mensaje enviado, para que los tests puedan leer lo que "se
    envió" sin depender de un proveedor externo.
    """

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    def enviar(self, destino: str, mensaje: str) -> None:
        self.enviados.append((destino, mensaje))
