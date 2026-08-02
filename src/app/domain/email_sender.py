# -*- coding: utf-8 -*-
"""
Puerto de envío de correo — el dominio no sabe (ni le importa) cómo llega el
mensaje al staff. La implementación real (SMTP + override fail-closed de
staging) es la rebanada de recuperación de contraseña; aquí solo el punto de
extensión + una implementación de desarrollo/test que no manda red (mismo
espíritu que `ConsoleNotificationSender`/`DevOtpSender`).
"""

from typing import Protocol


class EmailSender(Protocol):
    def enviar(self, destino: str, asunto: str, cuerpo: str) -> None: ...


class ConsoleEmailSender:
    """Implementación de desarrollo/test: NO envía correo real.

    Captura cada mensaje enviado, para que los tests puedan leer lo que "se
    envió" sin depender de un proveedor externo.
    """

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str, str]] = []

    def enviar(self, destino: str, asunto: str, cuerpo: str) -> None:
        self.enviados.append((destino, asunto, cuerpo))
