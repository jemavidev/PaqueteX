# -*- coding: utf-8 -*-
"""
MensajeEspecial — un aviso guardado que el ADMIN manda a mano desde la vista "Avisos especiales" en los días sin
servicio (fines de semana, festivos, cierres) -- `.scratch/avisos-especiales`, tickets 01-02.

Un solo `texto` para SMS y WhatsApp, con `{nombre}` y `{codigos}` como únicas variables (ver
`aviso_especial_service.VARIABLES_MENSAJE`). `nombre` es único entre los mensajes vigentes, sin distinguir
mayúsculas (índice parcial sobre `lower(nombre)` con `eliminado_en IS NULL`).

Eliminación LÓGICA (`eliminado_en`): los envíos ya hechos (`EnvioAvisoEspecial`) siguen apuntando al mensaje, y un
nombre eliminado puede volver a usarse en un mensaje nuevo.

MensajeEspecialHistorial — registro append-only de cada creación, edición y eliminación (mismo criterio que
`PlantillaNotificacionHistorial`: solo INSERT, `usuario_id` nullable para callers sin actor).

EnvioAvisoEspecial — registro append-only de cada SMS de aviso especial que SÍ salió (ticket 05): de él sale el
"Último SMS" de cada cliente. Guarda el nombre del mensaje COPIADO al enviar (el mensaje puede renombrarse o
eliminarse después) y, a propósito, ni el texto ni el teléfono -- mismo criterio de privacidad que `RegistroSms`. Los
intentos fallidos no van acá: solo a `registros_sms`.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKeyConstraint, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID

from .base import Base

MAX_NOMBRE_MENSAJE = 60
MAX_TEXTO_MENSAJE = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MensajeEspecial(Base):
    __tablename__ = "mensajes_especiales"
    __table_args__ = (
        Index(
            "uq_mensajes_especiales_nombre_vigente",
            func.lower(text("nombre")),
            unique=True,
            postgresql_where=text("eliminado_en IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(MAX_NOMBRE_MENSAJE), nullable=False)
    texto = Column(String(MAX_TEXTO_MENSAJE), nullable=False)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    actualizado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    eliminado_en = Column(DateTime(timezone=True), nullable=True)


class AccionMensajeEspecial(str, enum.Enum):
    CREADO = "CREADO"
    EDITADO = "EDITADO"
    ELIMINADO = "ELIMINADO"


class MensajeEspecialHistorial(Base):
    __tablename__ = "mensajes_especiales_historial"
    __table_args__ = (
        ForeignKeyConstraint(
            ["mensaje_id"], ["mensajes_especiales.id"], name="fk_mensajes_especiales_historial_mensaje"
        ),
        ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"], name="fk_mensajes_especiales_historial_usuario"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mensaje_id = Column(UUID(as_uuid=True), nullable=False)
    accion = Column(Enum(AccionMensajeEspecial, native_enum=False, length=20), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    nombre_anterior = Column(String(MAX_NOMBRE_MENSAJE), nullable=True)
    nombre_nuevo = Column(String(MAX_NOMBRE_MENSAJE), nullable=True)
    texto_anterior = Column(String(MAX_TEXTO_MENSAJE), nullable=True)
    texto_nuevo = Column(String(MAX_TEXTO_MENSAJE), nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class EnvioAvisoEspecial(Base):
    __tablename__ = "envios_aviso_especial"
    __table_args__ = (
        ForeignKeyConstraint(
            ["persona_id"], ["personas.id"], name="fk_envios_aviso_especial_persona", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["mensaje_id"], ["mensajes_especiales.id"], name="fk_envios_aviso_especial_mensaje"),
        ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_envios_aviso_especial_usuario"),
        Index("ix_envios_aviso_especial_persona_creado_en", "persona_id", "creado_en"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), nullable=False)
    mensaje_id = Column(UUID(as_uuid=True), nullable=False)
    nombre_mensaje = Column(String(MAX_NOMBRE_MENSAJE), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    creado_en = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
