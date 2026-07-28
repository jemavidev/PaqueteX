# -*- coding: utf-8 -*-
"""
PersonaPreferenciaNotificacion — preferencia de notificación por Canal ×
Evento (Grupo 13, Ronda 2 de `ajustes-post-referencia-funcional`).

Reemplaza el booleano único `Persona.notificaciones_activas` (todo o nada, un
solo canal implícito) por una matriz: cada `Persona` puede activar/desactivar
cada `Canal` para cada `Evento` de forma independiente. `Persona.
notificaciones_activas` NO se elimina (dato histórico neutral) pero deja de
leerse — ver `preferencia_notificacion_service.py`.

Tabla dispersa a propósito: si no hay fila para un `(persona_id, canal,
evento)`, NO significa "sin preferencia guardada aún" en el sentido de un
estado indefinido — `preferencia_notificacion_service.preferencia_activa`
resuelve la ausencia con el default histórico (SMS activo, el resto
inactivo), así que una Persona nueva nunca necesita un backfill de filas.

Solo `SMS` está conectado a un proveedor real (LIWA) hoy — `EMAIL`,
`LLAMADA` y `WHATSAPP` se guardan y se muestran, pero ningún envío real se
dispara para ellos todavía (no hay proveedor integrado).
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CanalNotificacion(str, enum.Enum):
    """`str` mixin: el valor persistido ES la etiqueta."""

    SMS = "SMS"
    EMAIL = "EMAIL"
    LLAMADA = "LLAMADA"
    WHATSAPP = "WHATSAPP"


# Los eventos son los mismos 4 que ya dispara `notificacion_service`
# (`EstadoPaquete` los define) — sin duplicar el enum aquí, se persiste su
# `.value` tal cual (mismo patrón VARCHAR-backed que el resto del dominio).


class PersonaPreferenciaNotificacion(Base):
    __tablename__ = "persona_preferencia_notificacion"

    __table_args__ = (
        ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_preferencia_notificacion_persona",
        ),
        UniqueConstraint(
            "persona_id",
            "canal",
            "evento",
            name="uq_persona_preferencia_notificacion_persona_canal_evento",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), nullable=False)
    canal = Column(Enum(CanalNotificacion, native_enum=False, length=20), nullable=False)
    evento = Column(String(20), nullable=False)
    activo = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<PersonaPreferenciaNotificacion persona_id={self.persona_id} "
            f"canal={self.canal} evento={self.evento} activo={self.activo}>"
        )
