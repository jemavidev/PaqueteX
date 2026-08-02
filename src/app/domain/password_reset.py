# -*- coding: utf-8 -*-
"""
PasswordReset — token de recuperación de contraseña para staff (rebuild PaqueteXv.2).

Un ADMIN/OPERADOR que olvidó su contraseña pide un enlace por correo y lo usa una
sola vez dentro de los 30 minutos siguientes. Mismo espíritu que `OtpCliente`
(código nunca en claro, expiración por columna, marca de consumo) pero con una
diferencia deliberada: el token se guarda con **SHA-256**, no bcrypt.

Un OTP de 2 dígitos (100 combinaciones) NECESITA bcrypt -- lento a propósito,
para que ni `max_intentos` compense un espacio de búsqueda tan chico. Este token
es un secreto de alta entropía (`secrets.token_urlsafe(32)`, 256 bits) que llega
por un enlace, no se teclea: no hay ataque de fuerza bruta realista contra él, y
el flujo SÍ necesita poder buscarlo por igualdad (`WHERE token_hash = ?`) para
resolver la fila desde la URL sin otra clave natural (a diferencia de OTP, que se
busca por `telefono`) -- bcrypt salado no permite esa búsqueda directa. SHA-256
(determinístico, sin sal) es la elección estándar para este tipo de token
justamente por eso.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PasswordReset(Base):
    __tablename__ = "password_resets"

    __table_args__ = (
        Index("ix_password_resets_token_hash", "token_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    usuario_id = Column(
        UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )

    # SHA-256 hex del token crudo (ver docstring del módulo) -- indexado porque
    # SÍ se busca por igualdad, a diferencia de codigo_hash/password_hash.
    token_hash = Column(String(64), nullable=False)

    expira_en = Column(DateTime(timezone=True), nullable=False)
    # Marca de consumo: no nulo = ya se usó para cambiar la contraseña (no reutilizable).
    usado_en = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<PasswordReset id={self.id} usuario_id={self.usuario_id}>"
