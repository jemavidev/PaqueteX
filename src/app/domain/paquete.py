# -*- coding: utf-8 -*-
"""
Paquete — foto INMUTABLE del contexto de entrega, congelada al anunciar
(rebuild PaqueteXv.2, ADR-0001).

Al anunciarse, el Paquete congela `{anunciado_por (teléfono), nombre_destinatario,
teléfono_destinatario (si hay), apartamento}`. Distingue **Anunciante** (quien
avisa — siempre una Persona real con Teléfono estable) de **Destinatario** (a
nombre de quién llega — que puede ser un simple nombre sin teléfono).

Dos decisiones estructurales viven en la forma de esta tabla:

  - **Anunciante por FK a Persona + `announced_by_phone` congelado.** El teléfono
    de una Persona es estable, así que ambos coinciden por construcción; el
    snapshot conserva el valor aunque la referencia cambiara.
  - **Snapshot de apartamento como TEXTO copiado, nunca FK (ADR-0001).** Un FK
    seguiría a la Persona al mudarse y reescribiría la historia; las columnas
    `snapshot_conjunto`/`snapshot_torre`/`snapshot_apartamento` guardan la terna
    del apartamento resuelto EN EL INSTANTE del anuncio, y nunca se mutan.

La LÓGICA de la máquina de estados (transiciones permitidas y quién puede cada
una) NO vive aquí: esta rebanada define solo el enum `estado`, las columnas de
transición (timestamps + FK-actor nullable hacia `usuarios`) y el nacimiento en
`ANUNCIADO`. UUID PK por portabilidad del D/R basado en dump/restore.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EstadoPaquete(str, enum.Enum):
    """Ciclo de vida del Paquete. `str` mixin: el valor persistido ES la etiqueta.

    Esta rebanada solo DEFINE el enum; qué transiciones son válidas y quién puede
    dispararlas es de otra rebanada (máquina de estados).
    """

    ANUNCIADO = "ANUNCIADO"
    RECIBIDO = "RECIBIDO"
    ENTREGADO = "ENTREGADO"
    CANCELADO = "CANCELADO"


class MotivoCancelacion(str, enum.Enum):
    """Motivos canónicos de cancelación (dropdown de la UI). `str` mixin: el valor
    ES la etiqueta. El conjunto puede afinarse sin romper el esquema (la columna
    `cancel_reason` es VARCHAR)."""

    ANUNCIO_ERRONEO = "ANUNCIO_ERRONEO"
    DEVUELTO_AL_TRANSPORTADOR = "DEVUELTO_AL_TRANSPORTADOR"
    NO_RECLAMADO = "NO_RECLAMADO"
    OTRO = "OTRO"


class Paquete(Base):
    __tablename__ = "paquetes"

    # Constraints con nombre explícito, IDÉNTICOS a los de la migración
    # (`0003_paquetes_usuarios`), para que el guard de paridad esquema↔ORM
    # (test_parity_esquema_orm) no reporte drift.
    __table_args__ = (
        # Llaves de negocio legibles, únicas.
        UniqueConstraint("tracking_number", name="uq_paquetes_tracking_number"),
        UniqueConstraint("access_code", name="uq_paquetes_access_code"),
        # Anunciante: FK viva a la Persona (su teléfono estable).
        ForeignKeyConstraint(
            ["announced_by_persona_id"],
            ["personas.id"],
            name="fk_paquetes_anunciante",
        ),
        # FK-actor nullable por transición hacia `usuarios` (staff).
        ForeignKeyConstraint(
            ["announced_by_usuario_id"],
            ["usuarios.id"],
            name="fk_paquetes_announced_by_usuario",
        ),
        ForeignKeyConstraint(
            ["received_by_usuario_id"],
            ["usuarios.id"],
            name="fk_paquetes_received_by_usuario",
        ),
        ForeignKeyConstraint(
            ["delivered_by_usuario_id"],
            ["usuarios.id"],
            name="fk_paquetes_delivered_by_usuario",
        ),
        ForeignKeyConstraint(
            ["cancelled_by_usuario_id"],
            ["usuarios.id"],
            name="fk_paquetes_cancelled_by_usuario",
        ),
    )

    # Surrogate key propia (UUID por portabilidad del D/R basado en dump/restore).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # --- Llaves de negocio legibles, únicas ---------------------------------- #
    tracking_number = Column(String(50), nullable=False)
    access_code = Column(String(20), nullable=False)
    # Guía del transportador: opcional y NO se captura al anunciar.
    guide_number = Column(String(50), nullable=True)

    # --- Anunciante: FK a Persona + teléfono congelado ----------------------- #
    announced_by_persona_id = Column(UUID(as_uuid=True), nullable=False)
    announced_by_phone = Column(String(20), nullable=False)

    # --- Destinatario: snapshot congelado (nunca FK) ------------------------- #
    # Un Destinatario sin teléfono queda como un nombre bajo el tel del Anunciante.
    recipient_name = Column(String(120), nullable=False)
    recipient_phone = Column(String(20), nullable=True)

    # --- Snapshot de apartamento: terna copiada como TEXTO (ADR-0001) -------- #
    # NUNCA un FK; NULL cuando no hay apartamento resuelto al anunciar.
    snapshot_conjunto = Column(String(120), nullable=True)
    snapshot_torre = Column(String(60), nullable=True)
    snapshot_apartamento = Column(String(60), nullable=True)

    # --- Estado del ciclo de vida (VARCHAR-backed, ver EstadoPaquete) -------- #
    estado = Column(
        Enum(EstadoPaquete, native_enum=False, length=20),
        nullable=False,
        default=EstadoPaquete.ANUNCIADO,
    )

    # --- Timestamps de transición ------------------------------------------- #
    # `announced_at` = instante del anuncio (siempre presente al nacer).
    announced_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    received_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # --- FK-actor nullable por transición hacia `usuarios` ------------------- #
    # El actor sale de la sesión, nunca hardcodeado. En `announce` el actor es el
    # cliente (Persona), así que `announced_by_usuario_id` queda NULL por ahora.
    announced_by_usuario_id = Column(UUID(as_uuid=True), nullable=True)
    received_by_usuario_id = Column(UUID(as_uuid=True), nullable=True)
    delivered_by_usuario_id = Column(UUID(as_uuid=True), nullable=True)
    cancelled_by_usuario_id = Column(UUID(as_uuid=True), nullable=True)
    # Motivo de cancelación (obligatorio a nivel de servicio solo al cancelar; la
    # columna es nullable porque los no-cancelados no lo tienen). VARCHAR-backed
    # (ver MotivoCancelacion); el emparejamiento no depende de él.
    cancel_reason = Column(String(40), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Paquete id={self.id} tracking={self.tracking_number!r} "
            f"estado={self.estado} recipient={self.recipient_name!r}>"
        )
