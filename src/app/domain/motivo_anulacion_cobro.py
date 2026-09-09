# -*- coding: utf-8 -*-
"""
MotivoAnulacionCobro — catálogo editable de motivos para anular un cobro a
"$0 pesos" (módulo "Gestión de cobro y bodegaje", `.scratch/cobro-bodegaje`).

Mismo molde exacto que `MotivoCancelacion`: `etiqueta` única, sin campo
`activo` (borrado siempre duro), sin historial de auditoría propio -- ver
`cobro_service.py` para las reglas de negocio. `Cobro.motivo_anulacion`
guarda el TEXTO copiado (no una FK), mismo criterio que
`Paquete.cancel_reason`: el emparejamiento es por texto exacto, sin FK de
por medio -- borrar/renombrar un motivo del catálogo no reengancha cobros
ya anulados.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MotivoAnulacionCobro(Base):
    __tablename__ = "motivos_anulacion_cobro"

    __table_args__ = (
        UniqueConstraint("etiqueta", name="uq_motivos_anulacion_cobro_etiqueta"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    etiqueta = Column(String(40), nullable=False)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<MotivoAnulacionCobro etiqueta={self.etiqueta!r}>"
