# -*- coding: utf-8 -*-
"""
ConfiguracionConjunto — nombre vigente del Conjunto residencial, singleton
(rebuild PaqueteXv.2, `.scratch/apartamento-catalogo-confirmacion`).

Tabla de OVERRIDE, mismo espíritu que `PlantillaNotificacion`: sin fila,
`configuracion_conjunto_service.obtener_nombre_conjunto` usa el default
hardcodeado ("El Club") — la fila solo aparece una vez un ADMIN lo renombra.

Singleton respaldado por la propia PK (encontrado en auditoría,
`.scratch/pendientes-cliente`): antes `id` era un UUID aleatorio
(`default=uuid.uuid4`) y "nunca hay más de una fila" dependía por completo
de la disciplina del servicio (busca con `.first()`, crea solo si no hay
ninguna) -- dos ADMIN renombrando por primera vez EXACTAMENTE al mismo
instante podían colar 2 filas, sin que ninguna constraint lo evitara.
`id` ahora es un UUID FIJO conocido (`ID_SINGLETON`) -- la fila SIEMPRE
vive en esa misma PK, así que la propia unicidad de la llave primaria
garantiza que nunca pueda haber una segunda (mismo principio que protege
`Persona.telefono`, aplicado a la PK en vez de a una columna de negocio).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base

# UUID fijo y conocido -- la única fila de esta tabla SIEMPRE vive acá. No es
# un valor mágico arbitrario: es la ancla que convierte "a lo sumo una fila"
# en un invariante respaldado por la PK, en vez de una convención de servicio.
ID_SINGLETON = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConfiguracionConjunto(Base):
    __tablename__ = "configuracion_conjunto"

    id = Column(UUID(as_uuid=True), primary_key=True, default=lambda: ID_SINGLETON)
    nombre = Column(String(120), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ConfiguracionConjunto nombre={self.nombre!r}>"
