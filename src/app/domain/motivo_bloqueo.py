# -*- coding: utf-8 -*-
"""
MotivoBloqueo — catálogo editable de motivos para bloquear a un residente
(módulo "Bloquear clientes", `.scratch/bloquear-clientes`).

Mismo molde exacto que `MotivoCancelacion`/`MotivoAnulacionCobro`: etiqueta
única, sin campo `activo` (borrado siempre duro). `Persona.motivo_bloqueo`
guarda el TEXTO copiado (no una FK), mismo criterio que
`Paquete.cancel_reason` -- borrar/renombrar un motivo del catálogo no
reengancha bloqueos ya registrados.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MotivoBloqueo(Base):
    __tablename__ = "motivos_bloqueo"

    __table_args__ = (UniqueConstraint("etiqueta", name="uq_motivos_bloqueo_etiqueta"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    etiqueta = Column(String(40), nullable=False)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<MotivoBloqueo etiqueta={self.etiqueta!r}>"
