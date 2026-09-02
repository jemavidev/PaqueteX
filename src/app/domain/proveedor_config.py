# -*- coding: utf-8 -*-
"""
ProveedorConfig — habilitado/orden de precedencia por `(canal, proveedor)`,
rebuild PaqueteXv.2 (`.scratch/administracion-proveedores/spec.md`, issue 01).

Guarda SOLO habilitado/orden -- nunca credenciales (esas siguen viviendo
únicamente en `.env` del servidor, ver Fase 2/issue 04 del spec). `canal` y
`proveedor` son las claves de texto plano del catálogo en código
(`proveedores_catalogo.py`), no un FK -- agregar un proveedor nuevo al
catálogo no requiere ninguna migración de esquema, solo una fila nueva (la
migración de siembra del issue 01 crea las filas iniciales; el service
(`proveedor_config_service.py`) hace upsert, así que un proveedor agregado al
catálogo después consigue su fila la primera vez que alguien lo guarda).

`orden` es NULL para un canal con un solo proveedor (hoy: Email/SMTP) -- no
existe un "orden de precedencia" real cuando no hay nada con qué competir.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .preferencia_notificacion import CanalNotificacion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProveedorConfig(Base):
    __tablename__ = "proveedores_notificacion_config"

    __table_args__ = (
        UniqueConstraint("canal", "proveedor", name="uq_proveedores_config_canal_proveedor"),
        ForeignKeyConstraint(
            ["updated_by"],
            ["usuarios.id"],
            name="fk_proveedores_config_updated_by",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canal = Column(Enum(CanalNotificacion, native_enum=False, length=20), nullable=False)
    proveedor = Column(String(30), nullable=False)
    habilitado = Column(Boolean, nullable=False, default=True)
    orden = Column(Integer, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    updated_by = Column(UUID(as_uuid=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ProveedorConfig canal={self.canal!r} proveedor={self.proveedor!r} "
            f"habilitado={self.habilitado!r} orden={self.orden!r}>"
        )
