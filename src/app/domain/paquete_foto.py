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

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, String, text
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
        # Importador espejo v1 → v2 (migración 0064_paquete_fotos_origen_v1).
        Index(
            "uq_paquete_fotos_origen_v1_id",
            "origen_v1_id",
            unique=True,
            postgresql_where=text("origen_v1_id IS NOT NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paquete_id = Column(UUID(as_uuid=True), nullable=False)
    url = Column(String(500), nullable=False)
    # Id del `file_uploads` de la v1 del que se copió esta foto
    # (`.scratch/importador-v1-espejo`). Nulo = foto nativa. Nunca se muestra.
    origen_v1_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<PaqueteFoto id={self.id} paquete_id={self.paquete_id}>"
