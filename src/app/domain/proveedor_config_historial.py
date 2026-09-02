# -*- coding: utf-8 -*-
"""
ProveedorConfigHistorial — registro append-only de cada cambio de
habilitado/orden de un `ProveedorConfig` (`.scratch/administracion-
proveedores/spec.md`, issue 01) -- mismo patrón exacto que
`PlantillaNotificacionHistorial`: nunca se hace UPDATE ni DELETE, solo
INSERT, uno por cada llamada exitosa a
`proveedor_config_service.guardar_habilitado_orden`.

`canal`/`proveedor` quedan denormalizados (copiados al momento del cambio)
para poder consultar el historial sin JOIN, igual que
`PlantillaNotificacionHistorial`.

Guarda el valor COMPLETO de antes/después -- a diferencia del historial de
credenciales (Fase 2, issue 05), habilitado/orden nunca es secreto, así que
no hay razón para ocultarlo. `_anterior=None` en la primera fila que se crea
para un `(canal, proveedor)` es honesto ("no había nada guardado todavía"),
no un dato inventado -- mismo criterio que `usuario_id` nullable.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKeyConstraint, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .preferencia_notificacion import CanalNotificacion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProveedorConfigHistorial(Base):
    __tablename__ = "proveedores_notificacion_config_historial"

    __table_args__ = (
        ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name="fk_proveedores_config_historial_usuario",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canal = Column(Enum(CanalNotificacion, native_enum=False, length=20), nullable=False)
    proveedor = Column(String(30), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    habilitado_anterior = Column(Boolean, nullable=True)
    habilitado_nuevo = Column(Boolean, nullable=False)
    orden_anterior = Column(Integer, nullable=True)
    orden_nuevo = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return (
            f"<ProveedorConfigHistorial canal={self.canal!r} proveedor={self.proveedor!r} "
            f"habilitado_nuevo={self.habilitado_nuevo!r} orden_nuevo={self.orden_nuevo!r}>"
        )
