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

`corregir_destinatario` NO es una transición (no toca `estado`), así que no
contradice lo anterior: puede correr en `ANUNCIADO`, `RECIBIDO` o `ENTREGADO`
(`ESTADOS_CORREGIBLES`) -- ver su propio docstring.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .ocupante_service import promover_al_recibir
from .paquete import CondicionPaquete, EstadoPaquete, MotivoCancelacion, Paquete, TipoPaquete
from .telefono import normalizar_telefono
from .texto import normalizar_nombre
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
    package_type: TipoPaquete = None,
    package_condition: CondicionPaquete = None,
) -> Paquete:
    """Recibe un paquete `ANUNCIADO` → `RECIBIDO`.

    Registra `received_at` (ahora) y `received_by_usuario_id` = el actor. La Guía
    del transportador es OPCIONAL (no todos la usan); si se pasa, se persiste.
    `package_type`/`package_condition` también son opcionales — si no se pasan,
    usan los defaults `NORMAL`/`BUENO` (Grupo 2 de
    `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

    Raises:
        TransicionInvalida: si el paquete no está `ANUNCIADO` (queda intacto).
    """
    if paquete.estado is not EstadoPaquete.ANUNCIADO:
        raise TransicionInvalida(paquete.estado, "recibir")

    paquete.estado = EstadoPaquete.RECIBIDO
    paquete.received_at = _now()
    paquete.received_by_usuario_id = actor.id
    if guide_number is not None:
        paquete.guide_number = normalizar_nombre(guide_number)
    paquete.package_type = package_type or TipoPaquete.NORMAL
    paquete.package_condition = package_condition or CondicionPaquete.BUENO

    session.flush()

    # Promoción automática a principal (.scratch/ocupante-principal-
    # escenarios, ticket 04): si se puede resolver el Ocupante destinatario
    # y su unidad no tiene principal todavía, queda promovido acá mismo --
    # nunca bloquea ni falla el recibo en sí.
    promover_al_recibir(session, paquete)

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


ESTADOS_CORREGIBLES = (EstadoPaquete.ANUNCIADO, EstadoPaquete.RECIBIDO, EstadoPaquete.ENTREGADO)
"""Estados donde `corregir_destinatario` puede corregir un error de tipeo del
nombre anunciado (conversación 2026-08-16 -- pedido explícito del cliente de
ampliar la corrección más allá de `ANUNCIADO`, hasta incluir `RECIBIDO` y
`ENTREGADO`). Único punto de la verdad para este conjunto -- el caller
(`packages.py`) lo reusa para precargar `candidatos_correccion` solo para los
paquetes donde el modal "Corregir destinatario" realmente puede guardar.
`CANCELADO` queda deliberadamente afuera (no fue parte del pedido, y no tiene
sentido de negocio corregir a quién le iba a llegar un paquete que nunca se
entregó)."""


def corregir_destinatario(
    session: Session,
    paquete: Paquete,
    actor: Usuario,
    recipient_name: str,
    recipient_phone: str = None,
) -> Paquete:
    """Corrige `recipient_name`/`recipient_phone` de un Paquete en
    `ESTADOS_CORREGIBLES` (`ANUNCIADO`/`RECIBIDO`/`ENTREGADO`).

    Excepción ACOTADA y auditada a la inmutabilidad del snapshot (ADR-0001):
    ADR-0001 protege contra que un FK a una entidad mutable (Apartamento)
    reescriba paquetes viejos SOLO porque la Persona cambió después — no
    contra que el staff corrija, de forma explícita, un error de tipeo del
    cliente al anunciar (p.ej. "Jesu Peres" → "Jesús Pérez", el nombre YA
    registrado de esa Persona). No cambia `estado`; registra
    `corrected_at`/`corrected_by_usuario_id` igual que las demás
    transiciones -- por eso puede convivir con el resto del ciclo de vida sin
    contradecir la regla "ENTREGADO/CANCELADO son terminales" del docstring
    del módulo: eso aplica a TRANSICIONES de estado, y esto no es una.

    Ampliado (conversación 2026-08-16, pedido explícito del cliente) de
    "solo `ANUNCIADO`" a `ESTADOS_CORREGIBLES`: un error de tipeo en el
    nombre no siempre se nota mientras el paquete sigue anunciado -- puede
    saltar recién al recibirlo, o incluso al entregarlo. Sigue siendo el
    mismo tipo de corrección acotada (un typo del snapshot, no una entidad
    viva reescribiendo historia), así que el mismo principio de ADR-0001
    aplica igual de bien más allá de `ANUNCIADO`. `CANCELADO` se excluyó
    deliberadamente -- no fue parte del pedido.

    `recipient_phone` es opcional (actualización parcial): si no se pasa, el
    teléfono del destinatario queda como estaba.

    Raises:
        TransicionInvalida: si el paquete no está en `ESTADOS_CORREGIBLES`
            (queda intacto) -- `CANCELADO` es la única forma de llegar acá
            hoy, ya que es el único estado fuera del conjunto.
        ValueError: si `recipient_name` es vacío (el paquete queda intacto).
    """
    if paquete.estado not in ESTADOS_CORREGIBLES:
        raise TransicionInvalida(paquete.estado, "corregir destinatario")

    nombre = (recipient_name or "").strip()
    if not nombre:
        raise ValueError("El nombre del destinatario es obligatorio.")

    telefono_normalizado = None
    if recipient_phone is not None and recipient_phone.strip():
        telefono_normalizado = normalizar_telefono(recipient_phone)

    paquete.recipient_name = normalizar_nombre(nombre)
    if telefono_normalizado is not None:
        paquete.recipient_phone = telefono_normalizado
    paquete.corrected_at = _now()
    paquete.corrected_by_usuario_id = actor.id

    session.flush()
    return paquete


def corregir_apartamento(
    session: Session,
    paquete: Paquete,
    actor: Usuario,
    apartamento: Apartamento,
) -> Paquete:
    """Corrige el Apartamento (snapshot) de un Paquete `ANUNCIADO` —
    segunda excepción ACOTADA y auditada a la inmutabilidad del snapshot
    (ADR-0001), hermana de `corregir_destinatario`.

    Pensada para el caso de "Paquete huérfano"
    (`.scratch/asociacion-retroactiva-apartamento`): un Paquete se anunció
    antes de que su Teléfono estuviera vinculado a un Apartamento, y ese
    Teléfono se vincula después -- el staff autoriza, explícitamente y
    mientras el Paquete sigue `ANUNCIADO`, que el snapshot se complete con el
    Apartamento ya conocido. Copia el texto de `apartamento` (nunca un FK,
    mismo criterio que `paquete_service.announce`). No cambia `estado`;
    registra `corrected_at`/`corrected_by_usuario_id` igual que
    `corregir_destinatario` (mismas columnas, sin distinguir en el esquema
    cuál de las dos correcciones fue).

    Raises:
        TransicionInvalida: si el paquete no está `ANUNCIADO` (queda intacto)
            — una vez `RECIBIDO` el contexto de entrega es tan inmutable como
            siempre, sin excepción.
        ValueError: si `apartamento` es ``None`` (el paquete queda intacto) —
            mismo criterio "valida antes de mutar" que el resto del archivo.
    """
    if paquete.estado is not EstadoPaquete.ANUNCIADO:
        raise TransicionInvalida(paquete.estado, "corregir apartamento")

    if apartamento is None:
        raise ValueError("El apartamento es obligatorio.")

    paquete.snapshot_conjunto = apartamento.conjunto
    paquete.snapshot_torre = apartamento.torre
    paquete.snapshot_apartamento = apartamento.apartamento
    paquete.corrected_at = _now()
    paquete.corrected_by_usuario_id = actor.id

    session.flush()
    return paquete
