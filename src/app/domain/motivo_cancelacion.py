# -*- coding: utf-8 -*-
"""
MotivoCancelacion — catálogo editable de motivos de cancelación de un Paquete
(`.scratch/motivos-cancelacion-catalogo`), gestionado por ADMIN desde
`/administracion/notificaciones`.

Reemplaza el enum Python fijo que existía antes (`EstadoPaquete`/`MotivoCancelacion`
en `paquete.py`, retirado en el ticket 04 de esa rebanada). Deliberadamente simple
por decisión explícita del cliente: `etiqueta` es el ÚNICO campo de contenido, sin
código interno separado -- es el mismo texto que se guarda directo en
`Paquete.cancel_reason` y en `PlantillaNotificacion.motivo` cuando se elige ese
motivo. Sin columna `activo` (borrado siempre duro) y sin historial de auditoría
propio -- ver `motivo_cancelacion_service.py` para las reglas de negocio.

`etiqueta` cap a 40 caracteres, igual que `Paquete.cancel_reason` y
`PlantillaNotificacion.motivo` -- las tres columnas comparten el mismo ancho a
propósito, para que cualquier etiqueta que quepa acá quepa también en las otras dos.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MotivoCancelacion(Base):
    __tablename__ = "motivos_cancelacion"

    __table_args__ = (
        UniqueConstraint("etiqueta", name="uq_motivos_cancelacion_etiqueta"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    etiqueta = Column(String(40), nullable=False)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<MotivoCancelacion etiqueta={self.etiqueta!r}>"
