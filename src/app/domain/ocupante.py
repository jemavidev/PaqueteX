# -*- coding: utf-8 -*-
"""
Ocupante — residente reconocido de un Apartamento, con Teléfono OPCIONAL
(rebuild PaqueteXv.2, ADR-0006).

Cada Apartamento con al menos un Ocupante tiene exactamente **uno** marcado
`es_principal` — ese Ocupante SIEMPRE tiene `persona_id` (Teléfono real,
ADR-0003 intacto para Persona). Los demás Ocupantes del mismo Apartamento
pueden o no tener `persona_id`. Ver `CONTEXT.md` (sección Ocupante) y
`docs/adr/0006-ocupante-residentes-sin-persona-propia.md`.

`nombre` se guarda en el propio Ocupante (no se deriva solo del join con
Persona) — permite listar/mostrar sin join y que ambos nombres diverjan
momentáneamente si se editan por separado.

`desvinculado_en` (.scratch/mis-datos, ticket 02): marcado histórico de "dar
de baja" — `NULL` significa activo. Un Ocupante dado de baja NUNCA se borra
(mismo espíritu que `anonimizar_persona`/ADR-0001): sus datos quedan de solo
consulta. Ver `ocupante_service.dar_de_baja_ocupante`.

`confirmado_en` (`.scratch/apartamento-catalogo-confirmacion`, ticket 06):
mismo patrón que `desvinculado_en` — `NULL` significa pending (todavía sin
verificar), con fecha significa confirmado en ese instante. TODO Ocupante
nuevo nace pending, sin excepción, incluido el primero de un Apartamento
vacío — `es_principal` ya no se marca al crear, se marca al confirmar (ver
`ocupante_service.confirmar_ocupante`). Un Ocupante pending no pierde
ninguna funcionalidad (puede anunciar/recibir igual que uno confirmado) —
la confirmación es un sello administrativo, no un gate técnico.

`uq_ocupantes_persona_activo` (encontrado en auditoría, `.scratch/pendientes-
cliente`, migración 0024): a lo sumo un Ocupante ACTIVO por `persona_id` --
antes esto SOLO lo garantizaba `ocupante_service._persona_ya_es_ocupante_
activo` (una consulta previa a nivel de aplicación), lo que dejaba una
carrera real abierta: dos altas concurrentes con el mismo teléfono (ninguna
ve todavía el commit de la otra) podían colar 2 filas activas -- exactamente
el mismo bug que esa comprobación fue escrita para evitar, solo que por la
puerta de la concurrencia en vez de la del reenvío secuencial. Este índice
lo cierra a nivel de base de datos, igual que `uq_ocupantes_principal_por_
apartamento` ya hace para `es_principal`.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ocupante(Base):
    __tablename__ = "ocupantes"

    __table_args__ = (
        ForeignKeyConstraint(
            ["apartamento_id"], ["apartamentos.id"], name="fk_ocupantes_apartamento"
        ),
        ForeignKeyConstraint(
            ["persona_id"], ["personas.id"], name="fk_ocupantes_persona"
        ),
        # Máximo 1 Ocupante principal por Apartamento — a nivel de base de datos.
        Index(
            "uq_ocupantes_principal_por_apartamento",
            "apartamento_id",
            unique=True,
            postgresql_where=text("es_principal"),
        ),
        # A lo sumo 1 Ocupante ACTIVO por Persona (con Teléfono), en
        # cualquier Apartamento -- ver docstring del módulo arriba.
        Index(
            "uq_ocupantes_persona_activo",
            "persona_id",
            unique=True,
            postgresql_where=text("persona_id IS NOT NULL AND desvinculado_en IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    apartamento_id = Column(UUID(as_uuid=True), nullable=False)
    # NULL cuando el Ocupante no tiene Teléfono propio (registro liviano).
    persona_id = Column(UUID(as_uuid=True), nullable=True)

    nombre = Column(String(120), nullable=False)
    es_principal = Column(Boolean, nullable=False, default=False)
    # NULL = activo. Con fecha = dado de baja en ese instante (histórico,
    # nunca se borra la fila -- ver docstring del módulo).
    desvinculado_en = Column(DateTime(timezone=True), nullable=True)
    # NULL = pending. Con fecha = confirmado en ese instante -- ver docstring
    # del módulo y `ocupante_service.confirmar_ocupante`.
    confirmado_en = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Ocupante id={self.id} nombre={self.nombre!r} "
            f"principal={self.es_principal} persona_id={self.persona_id}>"
        )
