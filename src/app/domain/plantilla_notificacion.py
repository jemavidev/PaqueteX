# -*- coding: utf-8 -*-
"""
PlantillaNotificacion — texto de mensaje editable por evento (y por motivo de
cancelación) y por canal, rebuild PaqueteXv.2 (Grupo 8 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`; extendida a multicanal
en `.scratch/plantillas-notificacion-multicanal`, ticket 01).

Única por `(evento, motivo, canal)` — `motivo` es NULL salvo para `CANCELADO`,
donde hay una plantilla por cada `MotivoCancelacion`; `canal` es uno de
`CanalNotificacion.SMS`/`EMAIL`/`WHATSAPP` (`LLAMADA` no se modela aquí — no
se pidió). Si no existe una fila para un `(evento, motivo, canal)` dado,
`construir_mensaje`/`obtener_texto_actual` usan el texto por defecto
hardcodeado (comportamiento de hoy, sin cambios) — esta tabla es un OVERRIDE,
no la única fuente de verdad.

`asunto` solo tiene sentido para `canal == EMAIL` (SMS/WhatsApp no tienen
asunto) — queda `NULL` para los demás canales, nunca validado a nivel de
columna (la capa de dominio decide cuándo importa).

`UniqueConstraint("evento", "motivo", "canal")` por sí sola NO alcanza para
`motivo IS NULL` -- Postgres trata cada `NULL` como distinto de cualquier
otro para efectos de unicidad, así que dos filas con el mismo `evento`+
`canal` y `motivo=NULL` no la violarían. El índice único parcial de abajo
(`uq_plantillas_notificacion_evento_motivo_nulo`, migración 0023, extendido
con `canal` en la migración 0033) cierra exactamente ese hueco -- mismo
patrón que `uq_ocupantes_principal_por_apartamento` en `Ocupante`.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from .base import Base
from .preferencia_notificacion import CanalNotificacion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlantillaNotificacion(Base):
    __tablename__ = "plantillas_notificacion"

    __table_args__ = (
        UniqueConstraint(
            "evento", "motivo", "canal", name="uq_plantillas_notificacion_evento_motivo_canal"
        ),
        Index(
            "uq_plantillas_notificacion_evento_motivo_nulo",
            "evento",
            "canal",
            unique=True,
            postgresql_where=text("motivo IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evento = Column(String(20), nullable=False)
    motivo = Column(String(40), nullable=True)
    canal = Column(Enum(CanalNotificacion, native_enum=False, length=20), nullable=False)
    texto = Column(String(500), nullable=False)
    asunto = Column(String(200), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<PlantillaNotificacion evento={self.evento!r} motivo={self.motivo!r} "
            f"canal={self.canal!r}>"
        )
