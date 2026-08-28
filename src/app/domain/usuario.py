# -*- coding: utf-8 -*-
"""
Usuario (staff) — entidad separada de la Persona (rebuild PaqueteXv.2).

El staff que opera sobre los paquetes es una identidad distinta del residente:
tiene un `rol` (`ADMIN`/`OPERADOR`) que gobierna sus privilegios. Esta rebanada
define SOLO el esqueleto mínimo que necesitan las FK-de-actor del Paquete
(quién ejecutó cada transición). Las columnas de credencial (contraseña fuerte)
son de la rebanada de auth, NO de aquí.

UUID PK por portabilidad del D/R basado en dump/restore.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RolUsuario(str, enum.Enum):
    """Rol del staff. `str` mixin: el valor persistido ES la etiqueta."""

    ADMIN = "ADMIN"
    OPERADOR = "OPERADOR"


class Usuario(Base):
    __tablename__ = "usuarios"

    # Unicidad del email con nombre explícito, IDÉNTICO al de la migración 0005
    # (`uq_usuarios_email`), para que el guard de paridad no reporte drift.
    __table_args__ = (UniqueConstraint("email", name="uq_usuarios_email"),)

    # Surrogate key propia (UUID por portabilidad del D/R basado en dump/restore).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    nombre = Column(String(120), nullable=False)

    # Credenciales de acceso (rebanada staff-auth). NULLABLE: un Usuario puede
    # existir como actor sin credenciales (p.ej. en tests de lifecycle); en
    # producción todo staff real tiene ambos. El email único es la llave de acceso;
    # la contraseña se guarda SOLO hasheada (nunca en claro).
    email = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=True)

    # Contacto propio del staff (.scratch/notificaciones-enviar-prueba,
    # ticket 01) -- SIN relación con el Teléfono/WhatsApp de Persona
    # (identidad de residente, ADR-0003/ADR-0007: llave de login/OTP, única).
    # Acá son datos de contacto de texto libre, opcionales, sin unicidad ni
    # implicación de auth -- solo sirven para pre-llenar a dónde mandar un
    # mensaje de prueba desde `/administracion/notificaciones`. Mismo largo
    # que `Persona.telefono` para ambas columnas (no `Persona.
    # whatsapp_usuario`, que guarda un HANDLE, `String(120)`) -- acá
    # `whatsapp` es un destino con forma de número, igual que `telefono`.
    telefono = Column(String(20), nullable=True)
    whatsapp = Column(String(20), nullable=True)

    # VARCHAR-backed (`native_enum=False`): se persiste como VARCHAR(20) sin tipo
    # ENUM nativo de Postgres (alembic compara mal los ENUM nativos y rompería el
    # guard de paridad). SQLAlchemy valida el conjunto de valores en Python.
    rol = Column(
        Enum(RolUsuario, native_enum=False, length=20), nullable=False
    )

    # Grupo 18 (Ronda 2): desactivar, nunca borrar -- las FK de actor
    # (received_by_usuario_id, etc.) dependen de que el Usuario siga
    # existiendo para la auditoría (Grupo 11). Activo por defecto: preserva
    # el comportamiento de todo el staff creado antes de este grupo.
    activo = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Usuario id={self.id} nombre={self.nombre!r} rol={self.rol}>"
