# -*- coding: utf-8 -*-
"""
Persona — un residente del conjunto (rebuild PaqueteXv.2).

Su llave universal e identidad estable es el Teléfono (forma canónica, único,
NOT NULL), no un id opaco con teléfono nullable (ADR-0003). La Persona tiene
surrogate key propia (UUID) para las FKs de otras entidades, nombre y campos
ampliables nullable que se completan desde `/customer/verify` en rebanadas
posteriores.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Persona(Base):
    __tablename__ = "personas"

    # Constraints con nombre explícito, IDÉNTICOS a los de las migraciones
    # (`uq_personas_telefono`, `fk_personas_apartamento_actual`), para que el
    # guard de paridad esquema↔ORM (test_parity_esquema_orm) no reporte drift.
    __table_args__ = (
        UniqueConstraint("telefono", name="uq_personas_telefono"),
        ForeignKeyConstraint(
            ["apartamento_actual_id"],
            ["apartamentos.id"],
            name="fk_personas_apartamento_actual",
        ),
    )

    # Surrogate key propia (UUID por portabilidad del D/R basado en dump/restore).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # La llave universal: Teléfono canónico, único y NOT NULL.
    telefono = Column(String(20), nullable=False)

    nombre = Column(String(120), nullable=False)

    # Apartamento actual: opcional (nullable) y mutable. Nulo = sin unidad o
    # desvinculado. La membresía (asignar/mudar/desvincular) vive en
    # apartamento_service; la FK se resuelve por su constraint nombrada arriba.
    apartamento_actual_id = Column(UUID(as_uuid=True), nullable=True)

    # Campos ampliables (registro implícito ahora, se completan luego).
    email = Column(String(120), nullable=True)
    documento = Column(String(40), nullable=True)
    tipo_documento = Column(String(10), nullable=True)
    segundo_contacto = Column(String(120), nullable=True)

    # Marca de anonimización (ADR-0005). No nulo = la Persona fue "eliminada":
    # sus datos personales quedan limpios y su teléfono es sintético. La fila
    # NUNCA se borra (FK real fk_paquetes_anunciante desde paquetes).
    eliminado_en = Column(DateTime(timezone=True), nullable=True)

    # Preferencia de notificaciones de evento (Recibido/Entregado/Cancelado).
    # Activada por defecto (preserva el comportamiento existente). NUNCA
    # afecta el envío del OTP (mecanismo de login, no una notificación opcional).
    notificaciones_activas = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Persona id={self.id} telefono={self.telefono!r} nombre={self.nombre!r}>"
