# -*- coding: utf-8 -*-
"""
Máquina de estados del Paquete — transiciones del ciclo de vida (Seam A).

El Paquete nace `ANUNCIADO` (ver `paquete_service.announce`). Este módulo gobierna
las transiciones posteriores, cada una registrando **quién** (el `Usuario` de la
sesión real, nunca hardcodeado) y **cuándo**:

    ANUNCIADO ──receive──▶ RECIBIDO ──deliver──▶ ENTREGADO   (terminal)
        └────────cancel────────┴─────cancel──────▶ CANCELADO (terminal)

`ENTREGADO` y `CANCELADO` son terminales: cualquier transición desde ellos se
rechaza con `TransicionInvalida`. Toda transición **valida antes de mutar**: un
rechazo deja el Paquete intacto (ni estado ni timestamps cambian).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .paquete import EstadoPaquete, MotivoCancelacion, Paquete
from .telefono import normalizar_telefono
from .usuario import Usuario


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TransicionInvalida(Exception):
    """Se intentó una transición desde un estado que no la permite.

    El Paquete queda intacto (la validación ocurre antes de cualquier mutación).
    """

    def __init__(self, estado_actual: EstadoPaquete, transicion: str):
        self.estado_actual = estado_actual
        self.transicion = transicion
        super().__init__(
            f"Transición '{transicion}' no permitida desde el estado "
            f"{getattr(estado_actual, 'value', estado_actual)}."
        )


def receive(
    session: Session,
    paquete: Paquete,
    actor: Usuario,
    guide_number: str = None,
) -> Paquete:
    """Recibe un paquete `ANUNCIADO` → `RECIBIDO`.

    Registra `received_at` (ahora) y `received_by_usuario_id` = el actor. La Guía
    del transportador es OPCIONAL (no todos la usan); si se pasa, se persiste.

    Raises:
        TransicionInvalida: si el paquete no está `ANUNCIADO` (queda intacto).
    """
    if paquete.estado is not EstadoPaquete.ANUNCIADO:
        raise TransicionInvalida(paquete.estado, "recibir")

    paquete.estado = EstadoPaquete.RECIBIDO
    paquete.received_at = _now()
    paquete.received_by_usuario_id = actor.id
    if guide_number is not None:
        paquete.guide_number = guide_number

    session.flush()
    return paquete


def deliver(session: Session, paquete: Paquete, actor: Usuario) -> Paquete:
    """Entrega un paquete `RECIBIDO` → `ENTREGADO` (terminal).

    Registra `delivered_at` (ahora) y `delivered_by_usuario_id` = el actor. El
    destinatario snapshot del Paquete (nombre/apartamento congelados al anunciar)
    sigue intacto para confirmar quién retira.

    Raises:
        TransicionInvalida: si el paquete no está `RECIBIDO` (queda intacto) —
            todavía `ANUNCIADO`, o ya `ENTREGADO`/`CANCELADO`.
    """
    if paquete.estado is not EstadoPaquete.RECIBIDO:
        raise TransicionInvalida(paquete.estado, "entregar")

    paquete.estado = EstadoPaquete.ENTREGADO
    paquete.delivered_at = _now()
    paquete.delivered_by_usuario_id = actor.id

    session.flush()
    return paquete


def cancel(session: Session, paquete: Paquete, actor: Usuario, motivo) -> Paquete:
    """Cancela un paquete `ANUNCIADO` o `RECIBIDO` → `CANCELADO` (terminal).

    El motivo es OBLIGATORIO (trazabilidad): un `MotivoCancelacion` o un string no
    vacío. Registra `cancelled_at` (ahora), `cancelled_by_usuario_id` = el actor y
    `cancel_reason` = el motivo. Cancelar es irreversible.

    Raises:
        TransicionInvalida: si el paquete está en un estado terminal
            (`ENTREGADO`/`CANCELADO`) — queda intacto.
        ValueError: si `motivo` es ``None`` o vacío — el paquete queda intacto
            (se valida antes de mutar).
    """
    if paquete.estado not in (EstadoPaquete.ANUNCIADO, EstadoPaquete.RECIBIDO):
        raise TransicionInvalida(paquete.estado, "cancelar")

    if motivo is None:
        raise ValueError("El motivo de cancelación es obligatorio.")
    if isinstance(motivo, MotivoCancelacion):
        reason = motivo.value
    else:
        reason = str(motivo).strip()
        if not reason:
            raise ValueError("El motivo de cancelación es obligatorio.")

    paquete.estado = EstadoPaquete.CANCELADO
    paquete.cancelled_at = _now()
    paquete.cancelled_by_usuario_id = actor.id
    paquete.cancel_reason = reason

    session.flush()
    return paquete


def corregir_destinatario(
    session: Session,
    paquete: Paquete,
    actor: Usuario,
    recipient_name: str,
    recipient_phone: str = None,
) -> Paquete:
    """Corrige `recipient_name`/`recipient_phone` de un Paquete `ANUNCIADO`.

    Excepción ACOTADA y auditada a la inmutabilidad del snapshot (ADR-0001):
    ADR-0001 protege contra que un FK a una entidad mutable (Apartamento)
    reescriba paquetes viejos SOLO porque la Persona cambió después — no
    contra que el staff corrija, de forma explícita y mientras el paquete
    sigue `ANUNCIADO`, un error de tipeo del cliente (p.ej. "Jesu Peres" →
    "Jesús Pérez", el nombre YA registrado de esa Persona). No cambia
    `estado`; registra `corrected_at`/`corrected_by_usuario_id` igual que las
    demás transiciones.

    `recipient_phone` es opcional (actualización parcial): si no se pasa, el
    teléfono del destinatario queda como estaba.

    Raises:
        TransicionInvalida: si el paquete no está `ANUNCIADO` (queda intacto)
            — una vez `RECIBIDO` el contexto de entrega es tan inmutable como
            siempre, sin excepción.
        ValueError: si `recipient_name` es vacío (el paquete queda intacto).
    """
    if paquete.estado is not EstadoPaquete.ANUNCIADO:
        raise TransicionInvalida(paquete.estado, "corregir destinatario")

    nombre = (recipient_name or "").strip()
    if not nombre:
        raise ValueError("El nombre del destinatario es obligatorio.")

    telefono_normalizado = None
    if recipient_phone is not None and recipient_phone.strip():
        telefono_normalizado = normalizar_telefono(recipient_phone)

    paquete.recipient_name = nombre
    if telefono_normalizado is not None:
        paquete.recipient_phone = telefono_normalizado
    paquete.corrected_at = _now()
    paquete.corrected_by_usuario_id = actor.id

    session.flush()
    return paquete
