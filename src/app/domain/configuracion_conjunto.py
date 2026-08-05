# -*- coding: utf-8 -*-
"""
ConfiguracionConjunto — nombre vigente del Conjunto residencial, singleton
(rebuild PaqueteXv.2, `.scratch/apartamento-catalogo-confirmacion`).

Tabla de OVERRIDE, mismo espíritu que `PlantillaNotificacion`: sin fila,
`configuracion_conjunto_service.obtener_nombre_conjunto` usa el default
hardcodeado ("El Club") — la fila solo aparece una vez un ADMIN lo renombra.
Nunca hay más de una fila (el servicio la busca con `.first()` y la
crea/actualiza, nunca la duplica).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConfiguracionConjunto(Base):
    __tablename__ = "configuracion_conjunto"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(120), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ConfiguracionConjunto nombre={self.nombre!r}>"
