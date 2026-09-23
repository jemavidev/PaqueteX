# -*- coding: utf-8 -*-
"""
ContactoExterno — contacto consolidado desde fuentes externas al sistema
(Google Contacts, la base de clientes de PaqueteX v1.0 en producción, y
futuras fuentes), completamente independiente de `Persona`/`Ocupante`
(módulo "Consolidación de contactos externos", `.scratch/contactos-externos`,
extendido en `.scratch/contactos-externos-import-export`).

Nombre elegido para no chocar con el módulo ya existente `contacto.py`, que
clasifica teléfono-vs-WhatsApp de un valor tecleado -- algo distinto.

Teléfono y WhatsApp son las DOS llaves de fusión (`ContactoExternoTelefono`/
`ContactoExternoWhatsapp`, cada una 1 a muchos, unicidad a nivel de tabla) --
dos filas que compartan cualquiera de las dos terminan en el mismo contacto
(ver `contacto_externo_service.fusionar_fuentes`).

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
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContactoExterno(Base):
    __tablename__ = "contactos_externos"

    __table_args__ = (
        # Auditoría de desempeño 2026-09-19: `listar_contactos_externos`
        # busca `nombre.ilike('%texto%')` -- comodín al inicio, mismo
        # patrón que 0042 diagnosticó para /paquetes. Esta tabla nació
        # después de esa migración y nunca recibió el mismo tratamiento;
        # ya tiene 1.041 filas reales (import masivo) sin más índice que
        # la PK. Migración `0053_indices_contactos_telefono`.
        Index(
            "ix_contactos_externos_nombre_trgm", "nombre",
            postgresql_using="gin", postgresql_ops={"nombre": "gin_trgm_ops"},
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(120), nullable=False)
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


class ContactoExternoWhatsapp(Base):
    """Mismo patrón que `ContactoExternoTelefono` -- un contacto puede tener
    más de un usuario de WhatsApp, y la unicidad a nivel de tabla ES la
    segunda llave de fusión (`.scratch/contactos-externos-import-export`,
    junto con el teléfono). Reemplaza la columna `whatsapp_usuario` que
    antes vivía directo en `ContactoExterno` (dato de un solo valor, sin
    deduplicación) -- ninguna fuente hasta ahora la había poblado."""

    __tablename__ = "contactos_externos_whatsapps"

    __table_args__ = (
        UniqueConstraint(
            "whatsapp_usuario", name="uq_contactos_externos_whatsapps_whatsapp_usuario"
        ),
        ForeignKeyConstraint(
            ["contacto_externo_id"],
            ["contactos_externos.id"],
            name="fk_contactos_externos_whatsapps_contacto",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contacto_externo_id = Column(UUID(as_uuid=True), nullable=False)
    # Forma canónica de `normalizar_whatsapp_usuario` -- único a nivel de
    # tabla, mismo criterio que `telefono` en `ContactoExternoTelefono`.
    whatsapp_usuario = Column(String(120), nullable=False)

    def __repr__(self) -> str:
        return f"<ContactoExternoWhatsapp whatsapp_usuario={self.whatsapp_usuario!r}>"


class FuenteContactoExterno(Base):
    """Catálogo de las fuentes de Contactos externos, cada una con un
    `numero` PERMANENTE (`.scratch/pendientes-cliente`, issue 362): la vista
    de administración muestra "01 · 02 · 03" en la columna Fuentes y la
    equivalencia arriba de la tabla, para que muchas fuentes sigan siendo
    legibles y el número diga cuál se agregó primero, cuál segundo, etc.

    El orden NO se puede deducir de `contactos_externos` (los contactos que
    comparten varias fuentes quedan con la misma fecha de creación), por eso
    el número se asigna y se guarda: cada fuente NUEVA recibe el siguiente
    (empieza en 1 con la tabla vacía) y ese número nunca cambia. `numero` NO
    es autoincremental de la base de datos: lo calcula
    `contacto_externo_service.obtener_o_crear_fuente` como máximo + 1 bajo un
    bloqueo de tabla, para que una transacción revertida no deje huecos.

    `contactos_externos.fuentes` sigue guardando el NOMBRE (sin llave
    foránea): este catálogo solo le pone número. La unicidad de `nombre`
    ignorando mayúsculas ("whatsapp" = "Whatsapp") la garantiza el servicio,
    no la base -- un índice funcional `lower(nombre)` complicaría el guard de
    paridad esquema-ORM sin aportar nada, porque todas las altas pasan por
    el mismo lugar y bajo el mismo bloqueo."""

    __tablename__ = "fuentes_contactos_externos"

    __table_args__ = (
        UniqueConstraint("nombre", name="uq_fuentes_contactos_externos_nombre"),
    )

    numero = Column(Integer, primary_key=True, autoincrement=False)
    nombre = Column(String(40), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<FuenteContactoExterno numero={self.numero} nombre={self.nombre!r}>"
