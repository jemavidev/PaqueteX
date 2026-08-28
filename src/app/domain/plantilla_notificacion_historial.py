# -*- coding: utf-8 -*-
"""
PlantillaNotificacionHistorial — registro append-only de cada guardado de
`PlantillaNotificacion` (`.scratch/plantillas-notificacion-multicanal`,
ticket 04). Nunca se hace UPDATE ni DELETE sobre esta tabla -- solo INSERT,
uno por cada llamada exitosa a `notificacion_service.guardar_plantilla`.

`evento`/`motivo`/`canal` quedan denormalizados (copiados de la plantilla al
momento del guardado) para poder consultar el historial sin JOIN -- mismo
criterio que el resto del dominio (evento/motivo ya se guardan como String
plano en otras tablas, no FK a un enum).

`usuario_id` es nullable: `guardar_plantilla` lo recibe como parámetro
opcional (default `None`) para no romper ningún caller existente sin un
actor real a mano (ej. los tests de dominio que llaman la función directo,
sin pasar por una ruta HTTP con sesión) -- un historial con
`usuario_id=NULL` es honesto ("cambio sin actor identificado"), no un dato
inventado.

Sin UI ni ruta de consulta en esta rebanada -- el registro queda disponible
para consulta directa a la base de datos o trabajo futuro.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .preferencia_notificacion import CanalNotificacion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlantillaNotificacionHistorial(Base):
    __tablename__ = "plantillas_notificacion_historial"

    __table_args__ = (
        ForeignKeyConstraint(
            ["plantilla_id"],
            ["plantillas_notificacion.id"],
            name="fk_plantillas_notificacion_historial_plantilla",
        ),
        ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name="fk_plantillas_notificacion_historial_usuario",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plantilla_id = Column(UUID(as_uuid=True), nullable=False)
    evento = Column(String(20), nullable=False)
    motivo = Column(String(40), nullable=True)
    canal = Column(Enum(CanalNotificacion, native_enum=False, length=20), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    texto_anterior = Column(String(500), nullable=True)
    texto_nuevo = Column(String(500), nullable=False)
    asunto_anterior = Column(String(200), nullable=True)
    asunto_nuevo = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return (
            f"<PlantillaNotificacionHistorial plantilla_id={self.plantilla_id} "
            f"evento={self.evento!r} canal={self.canal!r}>"
        )
