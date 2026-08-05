# -*- coding: utf-8 -*-
"""
Apartamento — entidad ligera (Conjunto → Torre → Apartamento) del rebuild
PaqueteXv.2.

Unidad **opcional** que agrupa a los Teléfonos que comparten `apartamento_actual`.
Su identidad es la **terna normalizada** `(conjunto, torre, apartamento)` con
restricción única, de modo que escribir la misma unidad (en cualquier
casing/espaciado) resuelva a la existente.

**Catálogo cerrado** (`.scratch/apartamento-catalogo-confirmacion`, ticket 03):
ya NO es "creable sobre la marcha" -- el catálogo completo (804 unidades) se
siembra una sola vez por migración (ticket 02); `apartamento_service.
resolver_apartamento` solo resuelve contra esas filas, nunca crea una nueva.

La jerarquía vive como tres columnas de una SOLA tabla ligera, no tres tablas.
Tiene surrogate key propia (UUID) por portabilidad del D/R basado en dump/restore.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .texto import normalizar_nombre


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalizar_componente(valor: str, etiqueta: str) -> str:
    """Normaliza un componente de la terna: strip + colapso de espacios + MAYÚSCULAS
    (vía `texto.normalizar_nombre`, el mismo canonicalizador que usan Persona/Paquete).

    Raises:
        ValueError: si el componente es ``None`` o queda vacío tras normalizar
            (un Apartamento necesita sus tres coordenadas para tener identidad).
    """
    if valor is None:
        raise ValueError(f"El componente '{etiqueta}' del Apartamento es obligatorio.")
    limpio = normalizar_nombre(valor)
    if not limpio:
        raise ValueError(
            f"El componente '{etiqueta}' del Apartamento no puede estar vacío."
        )
    return limpio


def normalizar_terna(conjunto: str, torre: str, apartamento: str) -> tuple[str, str, str]:
    """Devuelve la terna canónica ``(conjunto, torre, apartamento)``.

    Cada componente se normaliza por separado (strip + colapso de espacios internos
    + MAYÚSCULAS), de modo que ``"Torre A"``, ``" torre a "`` y ``"TORRE  A"``
    resuelvan al mismo valor y el dedup único no genere duplicados.

    Args:
        conjunto: nombre del conjunto residencial.
        torre: identificador de la torre/bloque.
        apartamento: identificador del apartamento/unidad.

    Returns:
        La terna en forma canónica.

    Raises:
        ValueError: si algún componente es ``None`` o queda vacío tras normalizar.
    """
    return (
        _normalizar_componente(conjunto, "conjunto"),
        _normalizar_componente(torre, "torre"),
        _normalizar_componente(apartamento, "apartamento"),
    )


class Apartamento(Base):
    __tablename__ = "apartamentos"

    # Unicidad de la terna declarada con nombre explícito, IDÉNTICO al de la
    # migración (`uq_apartamentos_terna`), para que el guard de paridad
    # esquema↔ORM (test_parity_esquema_orm) no reporte drift.
    __table_args__ = (
        UniqueConstraint(
            "conjunto", "torre", "apartamento", name="uq_apartamentos_terna"
        ),
    )

    # Surrogate key propia (UUID por portabilidad del D/R basado en dump/restore).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # La identidad de negocio: la terna normalizada (casing/espacios ya canónicos).
    conjunto = Column(String(120), nullable=False)
    torre = Column(String(60), nullable=False)
    apartamento = Column(String(60), nullable=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Apartamento id={self.id} "
            f"terna=({self.conjunto!r}, {self.torre!r}, {self.apartamento!r})>"
        )
