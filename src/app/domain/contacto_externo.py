# -*- coding: utf-8 -*-
"""
ContactoExterno — contacto consolidado desde fuentes externas al sistema
(Google Contacts, la base de clientes de PaqueteX v1.0 en producción, y
futuras fuentes), completamente independiente de `Persona`/`Ocupante`
(módulo "Consolidación de contactos externos", `.scratch/contactos-externos`).

Nombre elegido para no chocar con el módulo ya existente `contacto.py`, que
clasifica teléfono-vs-WhatsApp de un valor tecleado -- algo distinto.

Este módulo NO crea, modifica ni referencia ninguna fila de `Persona`/
`Ocupante` -- es una tabla de consulta aparte, mientras se decide qué hacer
con estos contactos (promoverlos a residentes reales, descartar algunos,
etc.) en un momento posterior.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    Column,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContactoExterno(Base):
    __tablename__ = "contactos_externos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(120), nullable=False)
    # Ninguna fuente actual lo provee (ni Google Contacts ni la base de
    # producción v1.0 tienen este dato) -- columna lista para cuando una
    # fuente futura sí lo traiga.
    whatsapp_usuario = Column(String(120), nullable=True)
    # Tags de qué fuente(s) aportaron este contacto (ej. "google_contacts",
    # "produccion_v1") -- no se muestra en la búsqueda simple, pero permite
    # priorizar más adelante cuáles revisar primero.
    fuentes = Column(ARRAY(String(40)), nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ContactoExterno nombre={self.nombre!r} fuentes={self.fuentes!r}>"


class ContactoExternoTelefono(Base):
    __tablename__ = "contactos_externos_telefonos"

    __table_args__ = (
        UniqueConstraint("telefono", name="uq_contactos_externos_telefonos_telefono"),
        ForeignKeyConstraint(
            ["contacto_externo_id"],
            ["contactos_externos.id"],
            name="fk_contactos_externos_telefonos_contacto",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contacto_externo_id = Column(UUID(as_uuid=True), nullable=False)
    # Forma canónica de `normalizar_telefono` -- único a nivel de tabla: un
    # mismo teléfono nunca puede pertenecer a dos ContactoExterno distintos,
    # esa unicidad ES la llave de fusión (ver `contacto_externo_service`).
    telefono = Column(String(20), nullable=False)

    def __repr__(self) -> str:
        return f"<ContactoExternoTelefono telefono={self.telefono!r}>"
