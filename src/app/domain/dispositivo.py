# -*- coding: utf-8 -*-
"""
Dispositivo registrado y eventos de seguridad del PIN de operador (`.scratch/pin-operador-dispositivo`).

- **Dispositivo**: un equipo (navegador) identificado por una cookie firmada propia, distinta de la de sesión. Lleva el
  contador de PIN fallidos seguidos: el PIN es único y no se elige Usuario al desbloquear, así que un PIN equivocado no
  se puede atribuir a nadie -- el contador es del equipo.
- **RegistroDispositivo**: el par Dispositivo–Usuario que nace al entrar con contraseña. Vigente mientras no venzan los
  días configurados y su `registros_version` coincida con la del Usuario (subirla = "Cerrar en todos los dispositivos").
- **EventoSeguridad**: rastro mínimo para el límite de cambios de PIN rechazados y el aviso al ADMIN de bloqueos por
  intentos.

Reglas en `operador_dispositivo_service.py`.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKeyConstraint, Index, Integer
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Dispositivo(Base):
    __tablename__ = "dispositivos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    ultimo_uso_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    intentos_pin_fallidos = Column(Integer, nullable=False, default=0, server_default="0")

    def __repr__(self) -> str:
        return f"<Dispositivo id={self.id}>"


class RegistroDispositivo(Base):
    __tablename__ = "registros_dispositivo"

    __table_args__ = (
        ForeignKeyConstraint(
            ["dispositivo_id"], ["dispositivos.id"], ondelete="CASCADE", name="fk_registros_dispositivo_dispositivo"
        ),
        ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="CASCADE", name="fk_registros_dispositivo_usuario"),
    )

    dispositivo_id = Column(UUID(as_uuid=True), primary_key=True)
    usuario_id = Column(UUID(as_uuid=True), primary_key=True)
    registrado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    registros_version = Column(Integer, nullable=False, default=0, server_default="0")

    def __repr__(self) -> str:
        return f"<RegistroDispositivo dispositivo={self.dispositivo_id} usuario={self.usuario_id}>"


class TipoEventoSeguridad(str, enum.Enum):
    PIN_REPETIDO = "PIN_REPETIDO"
    BLOQUEO_POR_INTENTOS = "BLOQUEO_POR_INTENTOS"


class EventoSeguridad(Base):
    __tablename__ = "eventos_seguridad"

    __table_args__ = (
        Index("ix_eventos_seguridad_tipo_creado_en", "tipo", "creado_en"),
        ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="CASCADE", name="fk_eventos_seguridad_usuario"),
        ForeignKeyConstraint(
            ["dispositivo_id"], ["dispositivos.id"], ondelete="SET NULL", name="fk_eventos_seguridad_dispositivo"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tipo = Column(Enum(TipoEventoSeguridad, native_enum=False, length=30), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    dispositivo_id = Column(UUID(as_uuid=True), nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<EventoSeguridad {self.tipo} en={self.creado_en}>"
