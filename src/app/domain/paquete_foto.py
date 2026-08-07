# -*- coding: utf-8 -*-
"""
PaqueteFoto — foto(s) opcionales de un Paquete, capturadas al recibir
(rebuild PaqueteXv.2, Grupo 2 de `ajustes-post-referencia-funcional`).

Un Paquete puede tener varias fotos (por si el staff sube más de una). Guarda
solo la `url` que devuelve el puerto `FotoStorage` — el almacenamiento real
(local en desarrollo, S3 en producción) es intercambiable sin tocar este
modelo.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaqueteFoto(Base):
    __tablename__ = "paquete_fotos"

    __table_args__ = (
        ForeignKeyConstraint(
            ["paquete_id"], ["paquetes.id"], name="fk_paquete_fotos_paquete"
        ),
        # FK sin índice propio (auditoría de base de datos,
        # .scratch/pendientes-cliente): se consulta una vez por paquete en
        # /consultar y /mis-paquetes (listar_fotos filtra por paquete_id).
        Index("ix_paquete_fotos_paquete_id", "paquete_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paquete_id = Column(UUID(as_uuid=True), nullable=False)
    url = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<PaqueteFoto id={self.id} paquete_id={self.paquete_id}>"
