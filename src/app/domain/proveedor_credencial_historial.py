# -*- coding: utf-8 -*-
"""
ProveedorCredencialHistorial — registro append-only de cada campo de
credencial que se cambió para un proveedor (`.scratch/administracion-
proveedores/spec.md`, issue 05, Fase 2).

A diferencia de `ProveedorConfigHistorial` (habilitado/orden, issue 01 --
nunca secreto, guarda el valor completo de antes/después), acá NUNCA se
guarda el valor de la credencial, ni antes ni después -- solo el NOMBRE del
campo que cambió (`campo`, ej. `"AWS_ACCESS_KEY_ID"`), quién y cuándo. Por
eso es una tabla separada en vez de agregarle columnas nullable a
`ProveedorConfigHistorial`: son dos eventos de auditoría con forma distinta,
mezclarlos en una sola tabla con columnas que a veces no aplican sería más
confuso que dos tablas chicas y honestas.

El valor real de la credencial vive SOLO en `.env` del servidor (nunca en
esta base de datos) -- `campo` es seguro de guardar porque es apenas el
NOMBRE de una variable de entorno del allowlist de proveedores, no su
contenido.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .preferencia_notificacion import CanalNotificacion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProveedorCredencialHistorial(Base):
    __tablename__ = "proveedores_credenciales_historial"

    __table_args__ = (
        ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name="fk_proveedores_credenciales_historial_usuario",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canal = Column(Enum(CanalNotificacion, native_enum=False, length=20), nullable=False)
    proveedor = Column(String(30), nullable=False)
    campo = Column(String(60), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return (
            f"<ProveedorCredencialHistorial canal={self.canal!r} proveedor={self.proveedor!r} "
            f"campo={self.campo!r}>"
        )
