# -*- coding: utf-8 -*-
"""
Ocupante — residente reconocido de un Apartamento, con Teléfono OPCIONAL
(rebuild PaqueteXv.2, ADR-0006).

Cada Apartamento con al menos un Ocupante tiene exactamente **uno** marcado
`es_principal` — ese Ocupante SIEMPRE tiene `persona_id` (Teléfono real,
ADR-0003 intacto para Persona). Los demás Ocupantes del mismo Apartamento
pueden o no tener `persona_id`. Ver `CONTEXT.md` (sección Ocupante) y
`docs/adr/0006-ocupante-residentes-sin-persona-propia.md`.

`nombre` se guarda en el propio Ocupante (no se deriva solo del join con
Persona) — permite listar/mostrar sin join y que ambos nombres diverjan
momentáneamente si se editan por separado.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ocupante(Base):
    __tablename__ = "ocupantes"

    __table_args__ = (
        ForeignKeyConstraint(
            ["apartamento_id"], ["apartamentos.id"], name="fk_ocupantes_apartamento"
        ),
        ForeignKeyConstraint(
            ["persona_id"], ["personas.id"], name="fk_ocupantes_persona"
        ),
        # Máximo 1 Ocupante principal por Apartamento — a nivel de base de datos.
        Index(
            "uq_ocupantes_principal_por_apartamento",
            "apartamento_id",
            unique=True,
            postgresql_where=text("es_principal"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    apartamento_id = Column(UUID(as_uuid=True), nullable=False)
    # NULL cuando el Ocupante no tiene Teléfono propio (registro liviano).
    persona_id = Column(UUID(as_uuid=True), nullable=True)

    nombre = Column(String(120), nullable=False)
    es_principal = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Ocupante id={self.id} nombre={self.nombre!r} "
            f"principal={self.es_principal} persona_id={self.persona_id}>"
        )
