# -*- coding: utf-8 -*-
"""
TarifaCobro — valores vigentes de cobro por recepción de paquetes, singleton
(módulo "Gestión de cobro y bodegaje", `.scratch/cobro-bodegaje`).

4 columnas fijas, no un catálogo de filas -- el cliente pidió exactamente
estos 4 valores editables por ADMIN, sin poder agregar un quinto tipo de
cobro ni eliminar ninguno de los 4 (mismo espíritu singleton que
`ConfiguracionConjunto`: PK fija conocida, la unicidad de la fila la
garantiza la propia PK, no la disciplina del servicio).

Sin fila -> `cobro_service.obtener_tarifas_vigentes` usa los valores
iniciales por defecto -- la fila solo se materializa cuando un ADMIN edita
por primera vez.

Cada `Cobro` guarda el monto REALMENTE aplicado en el momento (snapshot) --
cambiar una tarifa acá nunca reescribe un cobro histórico.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID

from .base import Base

# UUID fijo y conocido -- la única fila de esta tabla SIEMPRE vive acá (mismo
# principio que `ConfiguracionConjunto.ID_SINGLETON`).
ID_SINGLETON = uuid.UUID("00000000-0000-0000-0000-0000000000c0")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TarifaCobro(Base):
    __tablename__ = "tarifas_cobro"

    id = Column(UUID(as_uuid=True), primary_key=True, default=lambda: ID_SINGLETON)
    base_normal = Column(Integer, nullable=False, default=1500)
    base_extra_dimensionado = Column(Integer, nullable=False, default=2000)
    bodegaje_normal_24h = Column(Integer, nullable=False, default=1000)
    bodegaje_extra_dimensionado_24h = Column(Integer, nullable=False, default=1500)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<TarifaCobro base_normal={self.base_normal} "
            f"base_extra_dimensionado={self.base_extra_dimensionado} "
            f"bodegaje_normal_24h={self.bodegaje_normal_24h} "
            f"bodegaje_extra_dimensionado_24h={self.bodegaje_extra_dimensionado_24h}>"
        )
