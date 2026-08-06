# -*- coding: utf-8 -*-
"""
PlantillaNotificacion — texto de mensaje editable por evento (y por motivo de
cancelación), rebuild PaqueteXv.2 (Grupo 8 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

Único por `(evento, motivo)` — `motivo` es NULL salvo para `CANCELADO`, donde
hay una plantilla por cada `MotivoCancelacion`. Si no existe una fila para un
`(evento, motivo)` dado, `construir_mensaje` usa el texto por defecto
hardcodeado (comportamiento de hoy, sin cambios) — esta tabla es un OVERRIDE,
no la única fuente de verdad.

`UniqueConstraint("evento", "motivo")` por sí sola NO alcanza para
`motivo IS NULL` -- Postgres trata cada `NULL` como distinto de cualquier
otro para efectos de unicidad, así que dos filas con el mismo `evento` y
`motivo=NULL` no la violarían. El índice único parcial de abajo
(`uq_plantillas_notificacion_evento_motivo_nulo`, migración 0023) cierra
exactamente ese hueco -- mismo patrón que `uq_ocupantes_principal_por_
apartamento` en `Ocupante`.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlantillaNotificacion(Base):
    __tablename__ = "plantillas_notificacion"

    __table_args__ = (
        UniqueConstraint(
            "evento", "motivo", name="uq_plantillas_notificacion_evento_motivo"
        ),
        Index(
            "uq_plantillas_notificacion_evento_motivo_nulo",
            "evento",
            unique=True,
            postgresql_where=text("motivo IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evento = Column(String(20), nullable=False)
    motivo = Column(String(40), nullable=True)
    texto = Column(String(500), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<PlantillaNotificacion evento={self.evento!r} motivo={self.motivo!r}>"
        )
